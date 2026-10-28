"""完整成功run才可读取；跨页重复、总量漂移拒绝原子提交。"""

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from yuliang.modules.capability.service import maintenance
from yuliang.modules.connectors.adq_assets import AssetPage
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.plan_protocol import PlanLevel
from yuliang.modules.connectors.sync_store import PagePermit, SyncConflict
from yuliang.modules.delivery.plan_proofs import PlanMeasureProof


def project_plan_page(
    session: Session, ctx: GatewayContext, level: PlanLevel, permit: PagePermit, page: AssetPage
) -> None:
    args = dict(
        tenant=ctx.identity.tenant_id,
        source=permit.lease.source_id,
        run=permit.lease.run_id,
        page=permit.lease.page_no,
        outbound=permit.outbound_id,
        account=ctx.account_id,
        shop=ctx.shop_id,
        level=level,
        authorization=ctx.authorization_id,
        auth_version=ctx.authorization_version,
        binding_version=ctx.capability_version,
        size=page.page_size,
        total=page.total_number,
        pages=page.total_page,
        fetched=page.sync.fetched_at,
    )
    prior = session.execute(
        text("""SELECT page_size,total_number,total_page,level
        FROM adq_plan_pages WHERE run_id=:run ORDER BY page_no LIMIT 1"""),
        args,
    ).first()
    if prior is not None and tuple(prior) != (
        page.page_size,
        page.total_number,
        page.total_page,
        level,
    ):
        raise SyncConflict("PLAN_TOTAL_CHANGED")
    for item in page.sync.records:
        if session.execute(
            text("""SELECT 1 FROM adq_plan_observations
            WHERE run_id=:run AND native_id=:native"""),
            args | dict(native=item.native_id),
        ).first():
            raise SyncConflict("DUPLICATE_PLAN_ACROSS_PAGES")
    session.execute(
        text("""INSERT INTO adq_plan_pages(tenant_id,source_id,run_id,
        page_no,outbound_id,account_id,shop_id,level,page_size,total_number,total_page,fetched_at,
        authorization_id,authorization_version,binding_version)
        VALUES(:tenant,:source,:run,:page,:outbound,:account,:shop,:level,:size,:total,:pages,:fetched,
        :authorization,:auth_version,:binding_version)"""),
        args,
    )
    for item in page.sync.records:
        session.execute(
            text("""INSERT INTO adq_plan_observations
            (tenant_id,run_id,page_no,native_id,observation)
            VALUES(:tenant,:run,:page,:native,CAST(:observation AS jsonb))"""),
            args | dict(native=item.native_id, observation=json.dumps(item.values)),
        )


def register_plan_proof(connection: Connection, tenant_id: int, proof: PlanMeasureProof) -> UUID:
    maintenance(connection)
    proof = PlanMeasureProof.model_validate(proof.model_dump())
    if not proof.checked_at <= datetime.now(UTC) < proof.expires_at:
        raise ValueError("计划依据不在有效期")
    row = connection.execute(
        text("""SELECT s.account_id,b.connector_id,b.provider,
        b.product_line,b.operation_kind,b.http_method,b.normalized_path,b.revision
        FROM sync_sources s JOIN capability_bindings b ON
        (b.tenant_id,b.id)=(s.tenant_id,s.binding_id)
        WHERE s.tenant_id=:tenant AND s.id=:source"""),
        dict(tenant=tenant_id, source=proof.source_id),
    ).first()
    path = "/v1.3/campaigns/get" if proof.level == "CAMPAIGN" else "/v1.3/adgroups/get"
    if row is None or tuple(row)[1:] != (
        "adq",
        "tencent",
        "adq",
        "READ",
        "GET",
        path,
        proof.binding_version,
    ):
        raise ValueError("计划口径依据来源层级或绑定版本不匹配")
    old = connection.execute(
        text("SELECT payload FROM adq_plan_measure_proofs WHERE id=:id"), dict(id=proof.id)
    ).scalar_one_or_none()
    if old is not None:
        if old != proof.model_dump(mode="json"):
            raise ValueError("不可覆盖已有计划依据")
        return proof.id
    connection.execute(
        text("""INSERT INTO adq_plan_measure_proofs(id,tenant_id,source_id,
        account_id,version,payload,checked_at,expires_at)
        VALUES(:id,:tenant,:source,:account,:version,CAST(:payload AS jsonb),:checked,:expires)"""),
        dict(
            id=proof.id,
            tenant=tenant_id,
            source=proof.source_id,
            account=row[0],
            version=proof.version,
            payload=proof.model_dump_json(),
            checked=proof.checked_at,
            expires=proof.expires_at,
        ),
    )
    return proof.id


def read_plan_observations(
    session: Session,
    ctx: GatewayContext,
    level: PlanLevel,
    application_id: UUID,
    required_scopes: frozenset[str],
) -> dict[str, object]:
    """调用者传入服务器构造ctx；当前授权和范围每次重新校验。"""
    from yuliang.modules.connectors.adq_read_scope import require_adq_read
    from yuliang.modules.delivery.plan_proofs import measure_plan

    version = require_adq_read(session, ctx, application_id, required_scopes)
    kind = f"adq.plans.{level.lower()}"
    args = dict(account=ctx.account_id, shop=ctx.shop_id, binding=ctx.binding_id, kind=kind)
    runs = (
        session.execute(
            text("""SELECT r.*,s.id AS actual_source FROM sync_sources s
        JOIN sync_runs r ON (r.tenant_id,r.source_id)=(s.tenant_id,s.id)
        WHERE s.account_id=:account AND s.binding_id=:binding AND s.resource_kind=:kind
        AND s.enabled ORDER BY r.started_at DESC,r.id DESC LIMIT 2"""),
            args,
        )
        .mappings()
        .all()
    )
    if not runs:
        return dict(
            status="not_connected", observations=[], normalized=[], gaps=["PLAN_SOURCE_NOT_SYNCED"]
        )
    # 不跨不同filters选取局部run冒充全量；生产协议只有固定fields/full-page参数。
    run = runs[0]
    if len(runs) > 1 and runs[1]["actual_source"] != run["actual_source"]:
        return dict(
            status="error", observations=[], normalized=[], gaps=["AMBIGUOUS_PLAN_FULL_SOURCE"]
        )
    if run["state"] != "SUCCESS":
        return dict(status="error", observations=[], normalized=[], gaps=["PLAN_RUN_NOT_COMPLETE"])
    args |= dict(run=run["id"])
    pages = (
        session.execute(
            text("SELECT * FROM adq_plan_pages WHERE run_id=:run ORDER BY page_no"), args
        )
        .mappings()
        .all()
    )
    if not pages or any(
        p["account_id"] != ctx.account_id
        or p["shop_id"] != ctx.shop_id
        or p["authorization_id"] != ctx.authorization_id
        or p["authorization_version"] != version
        or p["binding_version"] != ctx.capability_version
        or p["level"] != level
        for p in pages
    ):
        return dict(
            status="error", observations=[], normalized=[], gaps=["PLAN_RUN_AUTHORIZATION_CHANGED"]
        )
    total = pages[0]["total_number"]
    rows = (
        session.execute(
            text("""SELECT o.native_id,o.observation,p.fetched_at
        FROM adq_plan_observations o JOIN adq_plan_pages p USING(tenant_id,run_id,page_no)
        WHERE o.run_id=:run ORDER BY o.native_id LIMIT 10001"""),
            args,
        )
        .mappings()
        .all()
    )
    if len(rows) != total or len(pages) != max(1, pages[0]["total_page"]):
        return dict(
            status="error", observations=[], normalized=[], gaps=["PLAN_COVERAGE_INCOMPLETE"]
        )
    payload = session.execute(
        text("""SELECT payload FROM adq_plan_measure_proofs
        WHERE source_id=:source ORDER BY version DESC LIMIT 1"""),
        dict(source=run["actual_source"]),
    ).scalar_one_or_none()
    observations = [
        dict(
            nativeId=r["native_id"], values=r["observation"], fetchedAt=r["fetched_at"].isoformat()
        )
        for r in rows
    ]
    if payload is None:
        return dict(
            status="empty" if total == 0 else "coverage_error",
            observations=observations,
            normalized=[],
            gaps=[] if total == 0 else ["PLAN_MEASURE_PROOF_MISSING"],
        )
    proof = PlanMeasureProof.model_validate(payload)
    normalized, gaps = [], []
    now = datetime.now(UTC)
    for row in rows:
        result, missing = measure_plan(
            row["observation"],
            proof,
            source_id=run["actual_source"],
            binding_version=ctx.capability_version,
            fetched_at=row["fetched_at"],
            now=now,
        )
        gaps.extend(missing)
        if result is not None:
            normalized.append(
                dict(
                    nativeId=row["native_id"],
                    level=level,
                    values=result,
                    proofId=str(proof.id),
                    proofVersion=proof.version,
                    sourceId=str(run["actual_source"]),
                    runId=str(run["id"]),
                )
            )
    return dict(
        status="coverage_error" if gaps else "empty" if total == 0 else "ready",
        observations=observations,
        normalized=[] if gaps else normalized,
        gaps=sorted(set(gaps)),
    )
