"""已确认页、精确原生观察、事实修订同事务；缺失口径保留缺口。"""

import json
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.connectors.adq_report_protocol import ReportPage, ReportQuery
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.redaction import canonical
from yuliang.modules.connectors.sync_store import PagePermit, SyncConflict, SyncStore
from yuliang.modules.reporting.adq_contracts import ReportContract, current_report_contract
from yuliang.modules.reporting.models import SAFE_INTEGER, FactDimensions, MetricSample, SourceProof
from yuliang.modules.reporting.service import FactWriter


def _money(value: str | None, unit: str | None, name: str, gaps: list[str]) -> int | None:
    if value is None or unit is None:
        gaps.append(f"{name}_MISSING" if value is None else f"{name}_UNIT_UNCONFIRMED")
        return None
    result = int(value) * (100 if unit == "CNY_YUAN_INTEGER" else 1)
    if abs(result) > SAFE_INTEGER:
        gaps.append(f"{name}_UNSAFE_INTEGER")
        return None
    return result


def report_samples(
    ctx: GatewayContext,
    query: ReportQuery,
    day: date,
    row: dict[str, Any],
    contract: ReportContract | None,
    exclusive_shop: bool,
) -> tuple[list[MetricSample], list[str]]:
    gaps: list[str] = []
    if contract is None:
        return [], ["REPORT_CONTRACT_UNCONFIRMED"]
    if not exclusive_shop:
        return [], ["WHOLE_ACCOUNT_SHOP_ATTRIBUTION_UNCONFIRMED"]
    hour = None
    if query.kind == "HOURLY":
        if contract.hour_encoding != "HOUR_OF_DAY" or not 0 <= int(row["native_hour"]) <= 23:
            return [], ["HOUR_ENCODING_UNCONFIRMED"]
        hour = int(row["native_hour"])
    basis = dict(
        fact_date=day,
        granularity="DAY" if query.kind == "DAILY" else "HOUR",
        fact_hour=hour,
        provider="tencent",
        product_line="adq",
        entity_kind="ACCOUNT",
        native_entity_id=ctx.native_account_id,
        shop_id=ctx.shop_id,
        account_id=ctx.account_id,
        timezone=contract.timezone,
        # time_line改变日期归集语义，必须与点击/曝光窗口共同参与可比键。
        attribution_window=f"{contract.attribution_window}/{query.time_line}",
        refund_policy=contract.refund_policy,
    )

    def sample(metric: str, unit: str, **values: Any) -> MetricSample:
        dimensions = FactDimensions.model_validate(
            basis
            | dict(
                metric_name=metric,
                unit=unit,
                currency="CNY" if unit == "MINOR" else None,
                basis_currency="CNY" if unit == "RATIO" else None,
            )
        )
        return MetricSample(dimensions=dimensions, **values)

    samples = []
    for field, metric in (("view_count", "impressions"), ("valid_click_count", "clicks")):
        value = int(row[field]) if row[field] is not None else None
        if value is None or not 0 <= value <= SAFE_INTEGER:
            gaps.append(f"{field.upper()}_MISSING_OR_UNSAFE")
            value = None
        samples.append(sample(metric, "COUNT", value=value))
    cost = _money(row["cost"], contract.cost_unit, "COST", gaps)
    gmv = None
    if contract.order_amount_is_gmv:
        gmv = _money(row["order_amount"], contract.order_amount_unit, "GMV", gaps)
    else:
        gaps.append("ORDER_AMOUNT_GMV_MEANING_UNCONFIRMED")
    samples.extend(
        (sample("spend_minor", "MINOR", value=cost), sample("paid_gmv_minor", "MINOR", value=gmv))
    )
    computable = cost is not None and cost > 0 and gmv is not None
    if not computable:
        gaps.append("ROI_UNCOMPUTABLE")
    samples.append(
        sample(
            "roi",
            "RATIO",
            numerator=gmv if computable else None,
            denominator=cost if computable else None,
        )
    )
    return samples, gaps


def project_report(
    session: Session,
    ctx: GatewayContext,
    query: ReportQuery,
    day: date,
    permit: PagePermit,
    page: ReportPage,
    store: SyncStore,
    writer: FactWriter,
    contract_id: Any,
) -> None:
    lease = permit.lease
    contract = current_report_contract(session, ctx, query.kind, query.time_line)
    if (contract.id if contract else None) != contract_id:
        raise SyncConflict("REPORT_CONTRACT_CHANGED")
    previous = session.execute(
        text("""SELECT total_number,total_page FROM adq_report_pages
        WHERE run_id=:run ORDER BY page_no LIMIT 1"""),
        dict(run=lease.run_id),
    ).first()
    if previous is not None and tuple(previous) != (page.total_number, page.total_page):
        raise SyncConflict("PAGINATION_CHANGED")
    links = (
        session.execute(
            text("""SELECT l.shop_id FROM entity_links l JOIN shops s
        ON (s.tenant_id,s.id)=(l.tenant_id,l.shop_id)
        WHERE l.account_id=:account AND s.deleted_at IS NULL"""),
            dict(account=ctx.account_id),
        )
        .scalars()
        .all()
    )
    exclusive = links == [ctx.shop_id]
    params = dict(
        tenant=ctx.identity.tenant_id,
        account=ctx.account_id,
        source=lease.source_id,
        run=lease.run_id,
        page=lease.page_no,
        outbound=permit.outbound_id,
        day=day,
        kind=query.kind,
        fetched=page.sync.fetched_at,
        contract=contract_id,
    )
    session.execute(
        text("""INSERT INTO adq_report_pages(tenant_id,account_id,source_id,run_id,page_no,
        outbound_id,report_date,kind,rows_digest,total_number,total_page,fetched_at)
        VALUES(:tenant,:account,:source,:run,:page,:outbound,:day,:kind,:digest,:total,:pages,:fetched)"""),
        params
        | dict(
            digest=store.codec.hash("report-rows-v1", canonical(page.rows)),
            total=page.total_number,
            pages=page.total_page,
        ),
    )
    proof = SourceProof(
        sync_run_id=lease.run_id,
        page_no=lease.page_no,
        outbound_id=permit.outbound_id,
        source_updated_at=None,
        fetched_at=page.sync.fetched_at,
    )
    for ordinal, row in enumerate(page.rows):
        if session.execute(
            text("SELECT 1 FROM adq_report_observations WHERE run_id=:run AND native_key=:key"),
            dict(run=lease.run_id, key=row["native_key"]),
        ).first():
            raise SyncConflict("DUPLICATE_REPORT_GRAIN")
        samples, gaps = report_samples(ctx, query, day, row, contract, exclusive)
        decisions = writer.apply(session, ctx, proof, samples) if samples else []
        session.execute(
            text("""INSERT INTO adq_report_observations(tenant_id,run_id,page_no,row_no,
            native_key,raw_values,gaps,fact_decisions,contract_id) VALUES(:tenant,:run,:page,
            :ordinal,:key,CAST(:raw AS jsonb),CAST(:gaps AS jsonb),
            CAST(:decisions AS jsonb),:contract)"""),
            params
            | dict(
                ordinal=ordinal,
                key=row["native_key"],
                raw=json.dumps(row),
                gaps=json.dumps(gaps),
                decisions=json.dumps(decisions),
            ),
        )
