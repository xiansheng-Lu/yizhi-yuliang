"""基于规范事实及真实成功页的覆盖；未知口径和缺值不补零。"""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.analysis.coverage import period_days
from yuliang.modules.analysis.models import AnalysisQuery
from yuliang.platform.errors import ApiError, ErrorCode

REQUIRED: dict[str, tuple[str, ...]] = {
    "ads": ("spend_minor", "paid_gmv_minor", "impressions", "clicks"),
    "shop": ("paid_gmv_minor", "paid_order_count", "refund_gmv_minor"),
    "live": (),
    "video": (),
}


def require_coverage(
    session: Session,
    query: AnalysisQuery,
    *,
    account_scope: tuple[UUID, ...] | None = None,
    comparison_window: bool = False,
) -> dict[str, Any]:
    start, end = date.fromisoformat(query.start_date), date.fromisoformat(query.end_date)
    days = (
        tuple(start + timedelta(days=i) for i in range((end - start).days + 1))
        if comparison_window
        else period_days(query)
    )
    accounts = (
        session.execute(
            text("""SELECT id,provider,product_line FROM ad_accounts
        WHERE id=ANY(access.analysis_accounts(:shop,:platform))"""),
            dict(shop=query.shop_id, platform=query.platform_id),
        )
        .mappings()
        .all()
    )
    if account_scope is not None:
        if not set(account_scope) <= {a["id"] for a in accounts}:
            raise ApiError(ErrorCode.FORBIDDEN, "原分析账户范围已撤销，不能计算或扩大账户集合")
        accounts = [a for a in accounts if a["id"] in account_scope]
    gaps: list[dict[str, str]] = []
    signatures: dict[tuple[str, str, str], set[tuple[Any, ...]]] = {}
    for dimension in query.dimensions:
        selected = [
            a
            for a in accounts
            if (dimension == "ads" and a["product_line"] == "adq")
            or (dimension == "shop" and a["product_line"] == "wxshop")
        ]
        if not selected or not REQUIRED[dimension]:
            gaps.append(dict(field=dimension, message="所选维度尚无完整规范事实来源"))
            continue
        for account in selected:
            for day in days:
                for metric in REQUIRED[dimension]:
                    rows = session.execute(
                        text("""SELECT f.attribution_window,f.refund_policy,
                        f.unit,f.currency FROM metric_facts f JOIN sync_runs r
                        ON (r.tenant_id,r.id)=(f.tenant_id,f.sync_run_id)
                        JOIN sync_pages p ON (p.tenant_id,p.run_id,p.page_no,p.outbound_id)=
                        (f.tenant_id,f.sync_run_id,f.page_no,f.outbound_id)
                        WHERE f.shop_id=:shop AND f.account_id=:account
                        AND f.product_line=:line AND f.fact_date=:day AND f.metric_name=:metric
                        AND f.granularity='DAY' AND f.entity_kind='ACCOUNT'
                        AND f.timezone='Asia/Shanghai' AND f.value_num IS NOT NULL
                        AND f.attribution_window NOT IN ('UNKNOWN','UNCONFIRMED')
                        AND f.refund_policy<>'UNKNOWN' AND r.state='SUCCESS'
                        AND (
                          (f.product_line='adq' AND EXISTS(
                            SELECT 1 FROM adq_report_pages rp
                    JOIN adq_report_observations ro ON
                              (ro.tenant_id,ro.run_id,ro.page_no)=(rp.tenant_id,rp.run_id,rp.page_no)
                    JOIN adq_report_contracts c ON (c.tenant_id,c.id)=(ro.tenant_id,ro.contract_id)
                    JOIN sync_sources ss ON (ss.tenant_id,ss.id)=(f.tenant_id,f.source_id)
                    WHERE (rp.tenant_id,rp.run_id,rp.page_no)=(f.tenant_id,f.sync_run_id,f.page_no)
                            AND rp.report_date=f.fact_date AND rp.kind='DAILY'
                            AND c.binding_id=ss.binding_id AND c.account_id=f.account_id
                            AND c.shop_id=f.shop_id AND c.active
                            AND c.checked_at<=clock_timestamp() AND c.expires_at>clock_timestamp()
                            AND (c.payload->>'attribution_window')||'/'||(c.payload->>'time_line')
                              =f.attribution_window
                            AND c.payload->>'refund_policy'=f.refund_policy
                            AND c.payload->>'timezone'=f.timezone
                            AND (SELECT count(*) FROM adq_report_pages pp
                      WHERE (pp.tenant_id,pp.run_id)=(rp.tenant_id,rp.run_id))=rp.total_page))
                          OR (f.product_line='wxshop' AND EXISTS(
                            SELECT 1 FROM shop_compass_observations co
                    JOIN shop_compass_contracts c ON
                        (c.tenant_id,c.id)=(co.tenant_id,co.contract_id)
                    JOIN sync_sources ss ON (ss.tenant_id,ss.id)=(f.tenant_id,f.source_id)
                    WHERE (co.tenant_id,co.run_id,co.page_no)=(f.tenant_id,f.sync_run_id,f.page_no)
                            AND co.report_date=f.fact_date AND c.binding_id=ss.binding_id
                            AND c.account_id=f.account_id AND c.shop_id=f.shop_id AND c.active
                            AND c.checked_at<=clock_timestamp() AND c.expires_at>clock_timestamp()
                            AND c.payload->>'definition_key'=f.attribution_window
                            AND c.payload->>'refund_policy'=f.refund_policy
                            AND c.payload->>'timezone'=f.timezone))
                        )"""),
                        dict(
                            shop=query.shop_id,
                            account=account["id"],
                            line=account["product_line"],
                            day=day,
                            metric=metric,
                        ),
                    ).all()
                    signature_key = (dimension, str(account["id"]), metric)
                    if len(rows) == 1:
                        signatures.setdefault(signature_key, set()).add(tuple(rows[0]))
                        if len(signatures[signature_key]) > 1:
                            gaps.append(
                                dict(
                                    field=dimension,
                                    message=f"{day.isoformat()}:{metric}:跨日口径不可比",
                                )
                            )
                    if len(rows) != 1:
                        gaps.append(
                            dict(
                                field=dimension,
                                message=f"{day.isoformat()}:{metric}:缺失或口径不唯一",
                            )
                        )
    if gaps:
        raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "分析数据覆盖不足", details=gaps[:100])
    return dict(
        timezone="Asia/Shanghai",
        days=len(days),
        dimensions=list(query.dimensions),
        source="CONFIRMED_NON_MISSING_FACTS_WITH_SUCCESSFUL_PAGES",
        signatures={"|".join(key): list(next(iter(values))) for key, values in signatures.items()},
    )
