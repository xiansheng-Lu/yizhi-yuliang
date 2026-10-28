"""当前有效来源合同及实际成功页的规范事实，不读原生比率或未证实金额。"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.connectors.base import GatewayContext

FACT_SQL = """SELECT f.* FROM metric_facts f JOIN sync_runs r
                        ON (r.tenant_id,r.id)=(f.tenant_id,f.sync_run_id)
                        JOIN sync_pages p ON (p.tenant_id,p.run_id,p.page_no,p.outbound_id)=
                        (f.tenant_id,f.sync_run_id,f.page_no,f.outbound_id)
                        WHERE f.shop_id=:shop AND f.account_id=:account
                        AND f.product_line=:line AND f.fact_date BETWEEN :start AND :end
 AND f.metric_name=ANY(CAST(:metrics AS text[]))
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
                        ) AND EXISTS(SELECT 1 FROM sync_sources src WHERE src.id=f.source_id
 AND src.binding_id=:binding)
 ORDER BY f.fact_date,f.metric_name,f.id"""


def query_facts(
    session: Session, ctx: GatewayContext, start: str, end: str
) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            text(FACT_SQL),
            dict(
                shop=ctx.shop_id,
                account=ctx.account_id,
                line=ctx.product_line,
                start=start,
                end=end,
                metrics=[
                    "spend_minor",
                    "paid_gmv_minor",
                    "impressions",
                    "clicks",
                    "paid_order_count",
                    "refund_gmv_minor",
                ],
                binding=ctx.binding_id,
            ),
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
