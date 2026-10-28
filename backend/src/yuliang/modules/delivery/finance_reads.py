"""只读财务观察投影；财务数值与预算写资格分别解释。"""

from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.connectors.adq_finance_protocol import FinancePage, finance_spec
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.freshness import ResourceFreshness
from yuliang.modules.connectors.redaction import canonical
from yuliang.modules.connectors.sync_store import PagePermit, SyncConflict, SyncStore
from yuliang.modules.delivery.wallet_proofs import METRICS, WalletMeasureProof
from yuliang.platform.errors import ApiError, ErrorCode


def project_finance(
    session: Session,
    ctx: GatewayContext,
    kind: Literal["FUNDS", "OPERATIONS"],
    permit: PagePermit,
    page: FinancePage,
    store: SyncStore,
) -> None:
    lease = permit.lease
    previous = session.execute(
        text("""SELECT total_number,total_page FROM adq_finance_pages
      WHERE run_id=:run ORDER BY page_no LIMIT 1"""),
        dict(run=lease.run_id),
    ).first()
    if previous is not None and tuple(previous) != (page.total_number, page.total_page):
        raise SyncConflict("PAGINATION_CHANGED")
    context = dict(
        tenant=ctx.identity.tenant_id,
        account=ctx.account_id,
        source=lease.source_id,
        run=lease.run_id,
        page=lease.page_no,
        outbound=permit.outbound_id,
        kind=kind,
        fetched=page.sync.fetched_at,
    )
    session.execute(
        text("""INSERT INTO adq_finance_pages(tenant_id,account_id,source_id,run_id,
      page_no,outbound_id,kind,rows_digest,total_number,total_page,fetched_at)
      VALUES(:tenant,:account,:source,:run,:page,:outbound,:kind,:digest,:total,:pages,:fetched)"""),
        context
        | dict(
            digest=store.codec.hash("finance-rows-v1", canonical(page.rows)),
            total=page.total_number,
            pages=page.total_page,
        ),
    )
    for ordinal, row in enumerate(page.rows):
        if kind == "FUNDS":
            session.execute(
                text("""INSERT INTO adq_fund_observations(tenant_id,account_id,
              run_id,page_no,fund_type,native_balance,native_realtime_cost,fund_status)
              VALUES(:tenant,:account,:run,:page,:fund_type,:native_balance,
               :native_realtime_cost,:fund_status)"""),
                context | row,
            )
        else:
            session.execute(
                text("""INSERT INTO adq_operation_observations(id,tenant_id,
              account_id,run_id,page_no,row_no,operation_object_type,operation_object_id,
              operation_action,operator_platform,native_created_time)
              VALUES(:id,:tenant,:account,:run,:page,:ordinal,:operation_object_type,
               :operation_object_id,:operation_action,:operator_platform,:native_created_time)"""),
                context | row | dict(id=uuid4(), ordinal=ordinal),
            )


def _source(
    session: Session,
    ctx: GatewayContext,
    source_id: UUID,
    store: SyncStore,
    kind: Literal["FUNDS", "OPERATIONS"],
) -> Any:
    spec = finance_spec(ctx, kind)
    store._read(session, ctx, spec)
    row = (
        session.execute(
            text("""SELECT * FROM sync_sources WHERE id=:source
      AND account_id=:account AND binding_id=:binding AND resource_kind=:kind"""),
            dict(
                source=source_id,
                account=ctx.account_id,
                binding=ctx.binding_id,
                kind=f"adq.finance.{kind.lower()}",
            ),
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(ErrorCode.NO_DATA, "财务来源不属于当前账户、产品线或读取绑定")
    if row["context_digest"] != store._context(ctx, spec):
        raise SyncConflict("CONTEXT_CHANGED")
    return row


def wallet_projection(
    session: Session, ctx: GatewayContext, source_id: UUID, store: SyncStore
) -> dict[str, Any]:
    _source(session, ctx, source_id, store, "FUNDS")
    health = ResourceFreshness(store.engine).inspect(session, ctx, source_id)
    now = health["queriedAt"]
    rows = (
        session.execute(
            text("""SELECT o.*,p.outbound_id,p.fetched_at,r.request_id
      FROM adq_fund_observations o JOIN adq_finance_pages p
       ON (p.tenant_id,p.run_id,p.page_no)=(o.tenant_id,o.run_id,o.page_no)
      JOIN platform_requests r ON (r.tenant_id,r.id)=(p.tenant_id,p.outbound_id)
      WHERE p.source_id=:source AND o.run_id=:run ORDER BY o.fund_type"""),
            dict(source=source_id, run=health["successRunId"]),
        )
        .mappings()
        .all()
    )
    funds = {row["fund_type"]: row for row in rows}
    proof_rows = (
        session.execute(
            text("""SELECT DISTINCT ON(metric) *
      FROM adq_wallet_measure_proofs WHERE source_id=:source
      ORDER BY metric,checked_at DESC,sequence DESC"""),
            dict(source=source_id),
        )
        .mappings()
        .all()
    )
    proofs = {row["metric"]: row for row in proof_rows}
    measures = {}
    for metric in METRICS:
        value: int | None = None
        currency: str | None = None
        state, reason = "UNCONFIRMED", "METRIC_SOURCE_OR_UNIT_UNCONFIRMED"
        proof_id = None
        stored = proofs.get(metric)
        if stored is not None:
            try:
                proof = WalletMeasureProof.model_validate(stored["payload"])
                valid = (
                    stored["active"]
                    and proof.id == stored["id"]
                    and proof.source_id == source_id
                    and proof.metric == metric
                    and proof.checked_at == stored["checked_at"]
                    and proof.expires_at == stored["expires_at"]
                    and proof.checked_at <= now < proof.expires_at
                )
                if not valid:
                    state, reason = "UNAVAILABLE", "METRIC_PROOF_EXPIRED_REVOKED_OR_INCONSISTENT"
                elif health["status"] != "FRESH":
                    state, reason = "UNAVAILABLE", health["reason"] or "SOURCE_NOT_FRESH"
                elif any(
                    fund not in funds or funds[fund][proof.source_field] is None
                    for fund in proof.fund_types
                ):
                    state, reason = "UNAVAILABLE", "REQUIRED_FUND_OR_FIELD_MISSING"
                else:
                    total = sum(int(funds[fund][proof.source_field]) for fund in proof.fund_types)
                    normalized = total * (1 if proof.native_unit == "CNY_MINOR" else 100)
                    if abs(normalized) > 9007199254740991:
                        state, reason = "UNAVAILABLE", "NORMALIZED_AMOUNT_EXCEEDS_SAFE_INTEGER"
                    else:
                        value, currency, state, reason = (
                            normalized,
                            proof.currency,
                            "AVAILABLE",
                            "VERIFIED_SOURCE_MEASURE",
                        )
                        proof_id = str(proof.id)
            except (ValueError, TypeError, KeyError):
                state, reason = "UNAVAILABLE", "METRIC_PROOF_OR_NATIVE_VALUE_INVALID"
        measures[metric] = dict(
            valueMinor=value, currency=currency, state=state, reason=reason, proofId=proof_id
        )
    return dict(
        sourceId=str(source_id),
        accountId=str(ctx.account_id),
        provider="tencent",
        productLine="adq",
        budgetUsable=False,
        measures=measures,
        rawFunds=[dict(row) for row in rows],
        freshness={key: value for key, value in health.items() if key != "policy"},
    )


def operations_projection(
    session: Session,
    ctx: GatewayContext,
    source_id: UUID,
    store: SyncStore,
    *,
    run_id: UUID | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    _source(session, ctx, source_id, store, "OPERATIONS")
    if (
        type(offset) is not int
        or type(limit) is not int
        or not 0 <= offset <= 10000000
        or not 1 <= limit <= 100
    ):
        raise ValueError("只接受有界的日志观察分页")
    selected = (
        session.execute(
            text("""SELECT * FROM sync_runs WHERE source_id=:source
      AND (CAST(:run AS uuid) IS NULL OR id=:run) ORDER BY started_at DESC LIMIT 1"""),
            dict(source=source_id, run=run_id),
        )
        .mappings()
        .first()
    )
    if selected is None:
        raise ApiError(ErrorCode.NO_DATA, "没有当前范围的日志读取批次")
    rows = (
        session.execute(
            text("""SELECT o.id AS observation_id,o.operation_object_type,
      o.operation_object_id,o.operation_action,o.operator_platform,o.native_created_time,
      o.native_log_id,o.time_unit,p.outbound_id,p.fetched_at,r.request_id,o.page_no,o.row_no
      FROM adq_operation_observations o JOIN adq_finance_pages p
       ON (p.tenant_id,p.run_id,p.page_no)=(o.tenant_id,o.run_id,o.page_no)
      JOIN platform_requests r ON (r.tenant_id,r.id)=(p.tenant_id,p.outbound_id)
      WHERE p.source_id=:source AND o.run_id=:run ORDER BY o.page_no,o.row_no
      OFFSET :offset LIMIT :limit"""),
            dict(source=source_id, run=selected["id"], offset=offset, limit=limit + 1),
        )
        .mappings()
        .all()
    )
    return dict(
        sourceId=str(source_id),
        runId=str(selected["id"]),
        state=selected["state"],
        complete=selected["state"] == "SUCCESS",
        rows=[dict(r) for r in rows[:limit]],
        nextOffset=offset + limit if len(rows) > limit else None,
        nativeLogIdentity="NOT_PROVIDED_BY_SDK",
        executable=False,
    )
