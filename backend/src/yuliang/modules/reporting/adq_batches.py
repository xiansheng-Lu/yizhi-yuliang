"""不可变请求批次组合稳定的逐日来源；水位只越过连续成功日期。"""

from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.connectors.adq_report_protocol import ReportQuery
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.redaction import canonical
from yuliang.modules.connectors.sync_store import SyncConflict, SyncStore
from yuliang.modules.reporting.adq_contracts import current_report_contract


def start_batch(
    session: Session,
    ctx: GatewayContext,
    batch_id: UUID,
    query: ReportQuery,
    page_size: int,
    store: SyncStore,
) -> Any:
    contract = current_report_contract(session, ctx, query.kind, query.time_line)
    digest = store.codec.hash(
        "report-batch-v1",
        canonical(
            dict(
                query=query.model_dump(),
                page_size=page_size,
                tenant=ctx.identity.tenant_id,
                user=str(ctx.identity.user_id),
                account=str(ctx.account_id),
                shop=str(ctx.shop_id),
                authorization=str(ctx.authorization_id),
                authorization_version=ctx.authorization_version,
                binding=str(ctx.binding_id),
                capability_version=ctx.capability_version,
                catalog=ctx.catalog_sha256,
                contract=str(contract.id) if contract else None,
            )
        ),
    )
    params = dict(
        id=batch_id,
        tenant=ctx.identity.tenant_id,
        user=ctx.identity.user_id,
        account=ctx.account_id,
        shop=ctx.shop_id,
        binding=ctx.binding_id,
        authorization=ctx.authorization_id,
        version=ctx.authorization_version,
        contract=contract.id if contract else None,
        digest=digest,
        kind=query.kind,
        time_line=query.time_line,
        start=date.fromisoformat(query.start_date),
        end=date.fromisoformat(query.end_date),
    )
    session.execute(
        text("""INSERT INTO adq_report_batches(id,tenant_id,user_id,account_id,shop_id,
        binding_id,authorization_id,authorization_version,contract_id,request_digest,kind,
        start_date,end_date,time_line,state) VALUES(:id,:tenant,:user,:account,:shop,:binding,
        :authorization,:version,:contract,:digest,:kind,:start,:end,:time_line,'RUNNING')
        ON CONFLICT(id) DO NOTHING"""),
        params,
    )
    batch = (
        session.execute(
            text("SELECT * FROM adq_report_batches WHERE id=:id FOR UPDATE"),
            params,
        )
        .mappings()
        .first()
    )
    if batch is None or batch["request_digest"] != digest:
        raise SyncConflict("REPORT_BATCH_CONTEXT_CHANGED")
    if batch["state"] == "SUCCESS":
        return batch
    current = date.fromisoformat(query.start_date)
    while current <= date.fromisoformat(query.end_date):
        session.execute(
            text("""INSERT INTO adq_report_batch_days(tenant_id,batch_id,report_date,run_id,state)
            VALUES(:tenant,:id,:day,:run,'PENDING') ON CONFLICT DO NOTHING"""),
            params | dict(day=current, run=uuid5(batch_id, current.isoformat())),
        )
        current += timedelta(days=1)
    return batch


def mark_day(
    session: Session,
    batch_id: UUID,
    day: date,
    state: str,
    source_id: UUID | None = None,
) -> None:
    session.execute(
        text("""UPDATE adq_report_batch_days SET state=:state,source_id=COALESCE(:source,source_id)
        WHERE batch_id=:batch AND report_date=:day AND state<>'SUCCESS'"""),
        dict(batch=batch_id, day=day, state=state, source=source_id),
    )


def batch_status(session: Session, batch_id: UUID) -> dict[str, Any]:
    session.execute(
        text("SELECT * FROM adq_report_batches WHERE id=:id FOR UPDATE"),
        dict(id=batch_id),
    ).mappings().one()
    days = (
        session.execute(
            text("SELECT * FROM adq_report_batch_days WHERE batch_id=:id ORDER BY report_date"),
            dict(id=batch_id),
        )
        .mappings()
        .all()
    )
    through = None
    for day in days:
        if day["state"] != "SUCCESS":
            break
        through = day["report_date"]
    state = (
        "SUCCESS"
        if days and all(d["state"] == "SUCCESS" for d in days)
        else ("RATE_LIMITED" if any(d["state"] == "RATE_LIMITED" for d in days) else "PARTIAL")
    )
    session.execute(
        text("UPDATE adq_report_batches SET state=:state,confirmed_through=:through WHERE id=:id"),
        dict(state=state, through=through, id=batch_id),
    )
    coverage = session.execute(
        text("""SELECT count(*) AS observations,
        count(*) FILTER(WHERE o.gaps<>'[]'::jsonb) AS with_gaps
        FROM adq_report_observations o JOIN adq_report_batch_days d
        ON (d.tenant_id,d.run_id)=(o.tenant_id,o.run_id) WHERE d.batch_id=:id"""),
        dict(id=batch_id),
    ).one()
    return dict(
        batchId=str(batch_id),
        state=state,
        confirmedReadThrough=through.isoformat() if through else None,
        coverageKind="CONFIRMED_READ_PAGES_ONLY",
        observations=coverage.observations,
        observationsWithFactGaps=coverage.with_gaps,
        days=[
            dict(
                date=d["report_date"].isoformat(),
                runId=str(d["run_id"]),
                sourceId=str(d["source_id"]) if d["source_id"] else None,
                state=d["state"],
            )
            for d in days
        ],
    )
