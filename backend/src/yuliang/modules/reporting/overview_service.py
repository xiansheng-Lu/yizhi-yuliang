"""Independent OverviewLoader read service; immutable explicit account selection."""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import Engine, text

from yuliang.modules.configuration.integration_service import IntegrationService
from yuliang.modules.reporting.overview_calculator import ai_counts, days, semantics, totals
from yuliang.modules.reporting.overview_models import OverviewReport, OverviewRequest
from yuliang.modules.reporting.query_facts import query_facts
from yuliang.modules.reporting.query_models import QueryRequest
from yuliang.modules.reporting.query_scope import query_context
from yuliang.platform.db import VerifiedIdentity, business_transaction


class OverviewService:
    def __init__(self, engine: Engine, identity: VerifiedIdentity):
        self.engine, self.identity = engine, identity

    def load(self, request: OverviewRequest) -> OverviewReport:
        first, last = request.filter.start_date, request.filter.end_date
        dates = days(first, last)
        start = date.fromisoformat(first)
        previous_start = (start - timedelta(days=len(dates))).isoformat()
        previous_end = (start - timedelta(days=1)).isoformat()
        account_ids = {a.account_id for a in request.accounts}
        current: list[dict[str, Any]] = []
        previous: list[dict[str, Any]] = []
        latest_runs: dict[Any, Any] = {}
        engine = self.engine.execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=True
        )
        with business_transaction(engine, self.identity) as session:
            for scope in request.accounts:
                ctx, _ = query_context(
                    session,
                    IntegrationService(self.engine, self.identity),
                    QueryRequest(scope=scope, resource="reports"),
                )
                for output, low, high in (
                    (current, first, last),
                    (previous, previous_start, previous_end),
                ):
                    rows = query_facts(session, ctx, low, high)
                    # Current latest run must still succeed, including empty/failed newer runs.
                    for row in rows:
                        if row["source_id"] not in latest_runs:
                            latest_runs[row["source_id"]] = session.execute(
                                text(
                                    "SELECT id,state FROM sync_runs WHERE source_id=:source "
                                    "ORDER BY started_at DESC,id DESC LIMIT 1"
                                ),
                                dict(source=row["source_id"]),
                            ).one()
                        latest = latest_runs[row["source_id"]]
                        if latest.id == row["sync_run_id"] and latest.state == "SUCCESS":
                            output.append(row)
            # Jobs are actor-visible through RLS; never infer completion from HTTP acceptance.
            jobs = (
                session.execute(
                    text("""SELECT document FROM command_jobs
                WHERE document IS NOT NULL AND (shop_id,account_id) IN (
                    SELECT * FROM unnest(CAST(:shops AS uuid[]),CAST(:accounts AS uuid[])))
                AND (updated_at AT TIME ZONE 'Asia/Shanghai')::date BETWEEN :start AND :end"""),
                    dict(
                        shops=[str(a.shop_id) for a in request.accounts],
                        accounts=[a.account_id for a in request.accounts],
                        start=first,
                        end=last,
                    ),
                )
                .scalars()
                .all()
            )
        value = totals(current, account_ids, dates)
        before = totals(previous, account_ids, days(previous_start, previous_end))
        comparable = (
            before.spend is not None
            and before.gmv is not None
            and semantics(current) == semantics(previous)
        )
        points = []
        for day in dates:
            part = totals([f for f in current if f["fact_date"] == day], account_ids, (day,))
            points.append(dict(at=day.isoformat(), spend=part.spend, gmv=part.gmv, roi=part.roi))
        source_times = [
            f["source_updated_at"] for f in current if f["source_updated_at"] is not None
        ]
        ai = ai_counts(list(jobs))
        note = (
            "仅冻结显式ADQ账户范围；广告支付归因GMV/CNY分，不混入店铺经营GMV；"
            "缺账户日/未知口径为null，不补零；无计划/直播规范维度，不摊派账户金额。"
        )
        if not comparable:
            note += "前期同账户完整覆盖或跨期可比口径不足，previous=null。"
        if not source_times:
            note += "源更新时间未提供，updatedAt=null。"
        elif len(source_times) != len(current):
            note += "仅部分事实提供源更新时间，updatedAt为已知源时间最大值。"
        note += "已知口径=" + repr(sorted(semantics(current)))
        fetched = [f["fetched_at"] for f in current]
        if fetched:
            note += (
                "；持久抓取时间范围=" + min(fetched).isoformat() + "~" + max(fetched).isoformat()
            )
        note += "；未配置新鲜度SLA，不声明FRESH。"
        return OverviewReport(
            filter=request.filter,
            updated_at=max(source_times).isoformat() if source_times else None,
            coverage_note=note,
            totals=value,
            previous=before if comparable else None,
            trend=dict(status="ready", data=points),
            channels=[
                dict(
                    id="adq",
                    name="腾讯广告",
                    detail="显式冻结账户日范围；非计划归因",
                    spend=value.spend,
                    gmv=value.gmv,
                    roi=value.roi,
                    share=1.0 if value.spend else None,
                )
            ],
            distributions=dict(
                status=[],
                channel=[dict(id="adq", name="腾讯广告", value=value.spend)] if value.spend else [],
            ),
            funnel=dict(status="unavailable", reason="缺同范围进房/下单/支付漏斗规范维度"),
            traffic=dict(status="unavailable", reason="缺流量来源规范事实，不根据GMV推断"),
            ai=ai,
        )
