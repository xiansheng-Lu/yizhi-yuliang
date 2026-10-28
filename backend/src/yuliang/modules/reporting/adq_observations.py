"""受当前账户/版本/口径约束的报表观察；缺失ROI不回填旧事实。"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.connectors.adq_report_protocol import ReportQuery
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.sync_store import SyncConflict
from yuliang.modules.reporting.adq_contracts import current_report_contract
from yuliang.modules.reporting.adq_projection import report_samples


def observation_view(
    session: Session,
    ctx: GatewayContext,
    batch_id: Any,
    *,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ValueError("观察投影分页超界")
    batch = (
        session.execute(
            text("""SELECT * FROM adq_report_batches WHERE id=:id AND account_id=:account
        AND binding_id=:binding AND shop_id=:shop AND user_id=:user
        AND authorization_id=:authorization AND authorization_version=:version"""),
            dict(
                id=batch_id,
                account=ctx.account_id,
                binding=ctx.binding_id,
                shop=ctx.shop_id,
                user=ctx.identity.user_id,
                authorization=ctx.authorization_id,
                version=ctx.authorization_version,
            ),
        )
        .mappings()
        .first()
    )
    if batch is None:
        raise SyncConflict("REPORT_BATCH_CONTEXT_CHANGED")
    query = ReportQuery.model_validate(
        dict(
            kind=batch["kind"],
            start_date=batch["start_date"].isoformat(),
            end_date=batch["end_date"].isoformat(),
            time_line=batch["time_line"],
        )
    )
    contract = current_report_contract(session, ctx, query.kind, query.time_line)
    if contract is not None and contract.id != batch["contract_id"]:
        contract = None
    linked = (
        session.execute(
            text("""SELECT l.shop_id FROM entity_links l JOIN shops s
        ON (s.tenant_id,s.id)=(l.tenant_id,l.shop_id)
        WHERE l.account_id=:id AND s.deleted_at IS NULL"""),
            dict(id=ctx.account_id),
        )
        .scalars()
        .all()
    )
    records = (
        session.execute(
            text("""SELECT o.*,p.report_date,p.fetched_at,p.outbound_id,d.state AS day_state
        FROM adq_report_observations o JOIN adq_report_pages p
        ON (p.tenant_id,p.run_id,p.page_no)=(o.tenant_id,o.run_id,o.page_no)
        JOIN adq_report_batch_days d ON (d.tenant_id,d.run_id)=(o.tenant_id,o.run_id)
        WHERE d.batch_id=:id ORDER BY p.report_date,o.page_no,o.row_no
        LIMIT :limit OFFSET :offset"""),
            dict(id=batch_id, limit=limit, offset=offset),
        )
        .mappings()
        .all()
    )
    result = []
    for row in records:
        samples, gaps = report_samples(
            ctx, query, row["report_date"], row["raw_values"], contract, linked == [ctx.shop_id]
        )
        if any(decision in {"CONFLICT", "STALE"} for decision in row["fact_decisions"]):
            samples = []
            gaps.append("FACT_REVISION_UNRESOLVED")
        result.append(
            dict(
                date=row["report_date"].isoformat(),
                nativeHour=row["raw_values"]["native_hour"],
                dayState=row["day_state"],
                gaps=sorted(set(row["gaps"] + gaps)),
                measures={
                    "roi": dict(
                        value=None,
                        numerator=None,
                        denominator=None,
                        unit="RATIO",
                        comparableKey=None,
                    )
                }
                | {
                    s.dimensions.metric_name: dict(
                        value=str(s.normalized_value) if s.normalized_value is not None else None,
                        numerator=str(s.numerator) if s.numerator is not None else None,
                        denominator=str(s.denominator) if s.denominator is not None else None,
                        unit=s.dimensions.unit,
                        comparableKey=s.dimensions.comparable_key,
                    )
                    for s in samples
                },
                factDecisions=row["fact_decisions"],
                runId=str(row["run_id"]),
                outboundId=str(row["outbound_id"]),
                sourceUpdatedAt=None,
                fetchedAt=row["fetched_at"].isoformat(),
            )
        )
    return dict(
        batchId=str(batch_id),
        state=batch["state"],
        coverageKind="CONFIRMED_READ_PAGES_ONLY",
        rows=result,
    )
