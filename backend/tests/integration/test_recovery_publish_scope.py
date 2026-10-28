"""真实双job恢复生产链；调用实际task，MQ仅本地确认回调，不启动worker。"""

from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.modules.command.applications import register_value_source
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.freshness import configure_policy, register_requirement
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.sync_store import MirrorRecord, SyncLease, SyncPage
from yuliang.modules.execution.attempt_models import BusinessKind, BusinessRef
from yuliang.modules.execution.attempts import ExecutionOrders
from yuliang.platform.db import business_transaction
from yuliang.platform.messages import WorkMessage

from .test_approvals import pending, request, reviewer
from .test_budget_initialization import budget_ready as budget_ready
from .test_capability_evidence import capability as capability
from .test_execution_attempts import SyntheticBudgetSerializer
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_platform_payloads import decoder, parameters, signer
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import risk_ready as risk_ready
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


class TargetSerializer(SyntheticBudgetSerializer):
    def __init__(self, spec: Any, native: str):
        super().__init__(spec)
        self.native = native

    def render(self, ctx: Any, job: Any, key: UUID) -> Any:
        spec, wire = super().render(ctx, job, key)
        return spec, wire | dict(object_id=self.native)


async def second_shop(d: dict[str, Any]) -> dict[str, Any]:
    """初始合成资源目录+真实留档采集，不插job/receipt/recovery状态。"""
    shop, campaign = uuid4(), uuid4()
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO shops(id,tenant_id,platform,native_id,name)
            SELECT :id,tenant_id,platform,:native,'Synthetic scope X' FROM shops WHERE id=:old"""),
            dict(id=shop, native=str(shop), old=d["shop"]),
        )
        owner.execute(
            text(
                "INSERT INTO entity_links(tenant_id,shop_id,account_id) "
                "VALUES(:tenant,:shop,:account)"
            ),
            dict(tenant=d["tenant"], shop=shop, account=d["ctx"].account_id),
        )
        owner.execute(
            text("""INSERT INTO campaigns(id,tenant_id,shop_id,account_id,
            provider,product_line,native_id)
            VALUES(:id,:tenant,:shop,:account,'tencent','adq','457')"""),
            dict(id=campaign, tenant=d["tenant"], shop=shop, account=d["ctx"].account_id),
        )
    ctx, readctx = replace(d["ctx"], shop_id=shop), replace(d["readctx"], shop_id=shop)
    wire = parameters(readctx) | dict(object_id="457")
    lease = d["store"].begin(readctx, d["readspec"], "plan.state", uuid4(), wire)
    assert isinstance(lease, SyncLease)
    permit = d["store"].prepare(readctx, d["readspec"], lease)
    recorded = RecordedPlatformClient(
        PlatformHttpClient(
            signer,
            transport=httpx.MockTransport(
                lambda req: httpx.Response(
                    200, json=dict(code=0, data=dict(id="457", budget_minor=100))
                )
            ),
        ),
        d["archive"],
        decoder=decoder,
    )
    response = await recorded.send(
        readctx,
        d["readspec"],
        permit.outbound_id,
        wire | dict(cursor=None),
        SecretStr("synthetic"),
        target=d["target"].model_copy(update=dict(native_id="457")),
        before_persist=lambda session: d["store"].dispatch_guard(
            session, readctx, d["readspec"], permit, wire | dict(cursor=None)
        ),
    )
    assert response.exchange is not None
    at = response.exchange.received_at
    d["store"].commit_page(
        readctx,
        d["readspec"],
        permit,
        SyncPage(
            (MirrorRecord(native_id="457", values=dict(budget_minor=100)),),
            None,
            True,
            at,
            at - timedelta(seconds=1),
        ),
    )
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        configure_policy(owner, lease.source_id, d["policy"], "synthetic/scope-X/freshness")
        register_requirement(
            owner, ctx.binding_id, lease.source_id, "457", "synthetic/scope-X/required"
        )
        register_value_source(
            owner,
            campaign,
            lease.source_id,
            ctx.authorization_id,
            "CHANGE_DAILY_BUDGET",
            "budget_minor",
            "synthetic/scope-X/value",
        )
    return d | dict(
        shop=shop,
        campaign=campaign,
        ctx=ctx,
        readctx=readctx,
        target=d["target"].model_copy(update=dict(native_id="457")),
        input=d["input"].model_copy(
            update=dict(shop_id=shop, target_id=campaign, idempotency_key=uuid4().hex)
        ),
    )


def recovery(d: dict[str, Any], job: UUID) -> dict[str, Any]:
    with business_transaction(d["engine"], d["identity"]) as session:
        return dict(
            session.execute(
                text("""SELECT r.*,e.state AS publish_state,e.attempts AS publishes,
            e.payload AS event_payload FROM execution_recoveries r JOIN outbox_events e
            ON (e.tenant_id,e.event_id)=(r.tenant_id,r.current_event_id) WHERE r.job_id=:job"""),
                dict(job=job),
            )
            .mappings()
            .one()
        )


@pytest.mark.parametrize("revoke_y", [False, True])
async def test_actual_system_task_only_publishes_its_own_job(
    budget_ready: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    revoke_y: bool,
) -> None:
    from yuliang.modules.execution import recovery_schedule
    from yuliang.worker import tasks
    from yuliang.worker.app import app as worker_app

    d = budget_ready
    x = await second_shop(d)
    # 真实审批并先完成两份SENT登记，再仅Y发生网络未知；不绕过未知写阻断规则。
    xjob = reviewer(x).review(request(pending(x, amount=90))).job
    yjob = reviewer(d).review(request(pending(d, amount=90))).job
    xs = ExecutionOrders(x["applications"], TargetSerializer(x["spec"], "457"))
    ys = ExecutionOrders(d["applications"], TargetSerializer(d["spec"], "456"))
    xp = xs.prepare(
        BusinessRef(kind=BusinessKind.COMMAND_JOB, id=UUID(xjob.job_id)),
        x["ctx"],
        xjob.version,
        0,
        uuid4(),
    )
    yp = ys.prepare(
        BusinessRef(kind=BusinessKind.COMMAND_JOB, id=UUID(yjob.job_id)),
        d["ctx"],
        yjob.version,
        0,
        uuid4(),
    )
    assert xp.attempt.attempt_id != yp.attempt.attempt_id
    calls = []

    def timeout(wire: httpx.Request) -> httpx.Response:
        calls.append(wire.method)
        raise httpx.ReadTimeout("synthetic scope-Y timeout", request=wire)

    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(timeout)),
        d["archive"],
        decoder=decoder,
    )
    assert yp.spec is not None and yp.parameters is not None
    await client.send(
        d["ctx"],
        yp.spec,
        yp.attempt.outbound_id,
        yp.parameters,
        SecretStr("synthetic"),
        target=d["target"],
        execution_id=yp.attempt.execution_id,
        attempt_id=yp.attempt.attempt_id,
        before_verify=ys.before_verify(yp.attempt),
        after_admission=ys.after_admission(yp.attempt, d["ctx"], yp.spec, yp.parameters),
    )
    assert calls == ["POST"]
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""UPDATE target_execution_guards
            SET lease_until=clock_timestamp()-interval '1 second'
            WHERE job_id IN (:x,:y)"""),
            dict(x=UUID(xjob.job_id), y=UUID(yjob.job_id)),
        )
        owner.execute(
            text("""UPDATE execution_recoveries SET next_due=clock_timestamp()-
            CASE WHEN job_id=:x THEN interval '2 minutes' ELSE interval '1 minute' END
            WHERE job_id IN (:x,:y)"""),
            dict(x=UUID(xjob.job_id), y=UUID(yjob.job_id)),
        )
        owner.execute(
            text("""UPDATE execution_recovery_events e SET due_at=r.next_due
            FROM execution_recoveries r WHERE e.recovery_id=r.id AND r.job_id IN (:x,:y)"""),
            dict(x=UUID(xjob.job_id), y=UUID(yjob.job_id)),
        )
        owner.execute(
            text("""UPDATE outbox_events e SET not_before=r.next_due
            FROM execution_recoveries r WHERE e.event_id=r.current_event_id
            AND r.job_id IN (:x,:y)"""),
            dict(x=UUID(xjob.job_id), y=UUID(yjob.job_id)),
        )
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:member"),
            dict(member=d["identity"].membership_id),
        )
        owner.execute(
            text("""INSERT INTO data_scopes(tenant_id,membership_id,scope_type,account_id)
            VALUES(:tenant,:member,'ACCOUNT',:account)"""),
            dict(
                tenant=d["tenant"], member=d["identity"].membership_id, account=d["ctx"].account_id
            ),
        )
        # X始终撤scope；对照额外撤Y，不能因此改变X。
        if not revoke_y:
            owner.execute(
                text("""INSERT INTO data_scopes(tenant_id,membership_id,scope_type,shop_id)
                VALUES(:tenant,:member,'SHOP',:shop)"""),
                dict(tenant=d["tenant"], member=d["identity"].membership_id, shop=d["shop"]),
            )
    before_x = recovery(d, UUID(xjob.job_id))
    initial_y = recovery(d, UUID(yjob.job_id))
    notices = []

    def sender(identifier: UUID, name: str, payload: dict[str, Any], due: Any) -> None:
        assert name == "yuliang.system.execution_recovery" and payload["job_id"] == yjob.job_id
        notices.append(identifier)

    monkeypatch.setattr(tasks, "engine", lambda: d["engine"])
    monkeypatch.setattr(recovery_schedule, "celery_recovery_sender", lambda app: sender)
    monkeypatch.setattr(worker_app.conf, "yuliang_settings", d["settings"])
    item = WorkMessage.model_validate(initial_y["event_payload"])
    result = tasks.recover_execution.run(item.model_dump(mode="json"))
    after_y = recovery(d, UUID(yjob.job_id))
    assert recovery(d, UUID(xjob.job_id)) == before_x
    if revoke_y:
        assert result["recovery"]["state"] == after_y["state"] == "BLOCKED_AUTH" and notices == []
    else:
        assert result["recovery"]["state"] == after_y["state"] == "WAITING"
        assert after_y["publish_state"] == "PUBLISHED" and notices == [after_y["current_event_id"]]
        with business_transaction(d["engine"], d["identity"]) as session:
            assert (
                session.execute(
                    text(
                        "SELECT count(*) FROM execution_recovery_evidence "
                        "WHERE job_id=:job AND kind='AUTH_BLOCKED'"
                    ),
                    dict(job=UUID(yjob.job_id)),
                ).scalar_one()
                == 0
            )
    assert calls == ["POST"]
