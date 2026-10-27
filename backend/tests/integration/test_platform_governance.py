"""真实Redis Lua/并发/栅栏和PG租约；平台仅合成输入，不证明真实供应商配额。"""

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import redis
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.capability.service import register_binding
from yuliang.modules.connectors.archive import RecordedPlatformClient, _verify
from yuliang.modules.connectors.base import GatewayContext, ObjectRef
from yuliang.modules.connectors.governance import (
    CallDeferred,
    GovernanceUnavailable,
    PlatformGovernance,
    QuotaLimits,
    QuotaScope,
)
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import OperationSpec, PlatformHttpClient
from yuliang.modules.connectors.outcomes import Outcome
from yuliang.platform.db import business_transaction
from yuliang.worker.connector_schedule import reschedule_connector_task

from .test_capability_evidence import capability as capability
from .test_identity_database import database as database
from .test_infrastructure import local_url
from .test_password_sessions import login_api as login_api
from .test_platform_payloads import decoder, parameters, setup, signer

pytestmark = pytest.mark.integration


def quota_only_preflight(
    session: Session, ctx: GatewayContext, spec: OperationSpec, target: ObjectRef | None
) -> None:
    """本文件只测配额边界；真实数据写资格使用test_resource_freshness验证，不以此替代。"""
    _verify(session, ctx, spec)
    if target:
        ctx.assert_ref(target)


@pytest.fixture
def governance() -> Iterator[PlatformGovernance]:
    limits = QuotaLimits(1000, 1000, 10, 10, 10, 10, 60000)
    prefix = "test-platform-" + uuid4().hex
    client = redis.Redis.from_url(local_url("YULIANG_TEST_REDIS_URL"), decode_responses=True)
    gov = PlatformGovernance(client, limits, namespace=prefix)
    try:
        assert client.ping()
        yield gov
    finally:
        keys = list(client.scan_iter(match=prefix + ":*"))
        if keys:
            client.delete(*keys)
        client.close()


def scope(cap: dict[str, Any]) -> QuotaScope:
    return QuotaScope(uuid4(), setup(cap)[0])


def test_reserved_lanes_and_provider_account_separation(
    governance: PlatformGovernance, capability: dict[str, Any]
) -> None:
    gov = PlatformGovernance(
        governance.client, QuotaLimits(8, 6, 1, 1, 1, 1, 60000), namespace=governance.namespace
    )
    current = scope(capability)
    assert all(gov.admit(current, "SYNC").allowed for _ in range(4))
    denied = gov.admit(current, "SYNC")
    assert not denied.allowed and denied.reason == "QUOTA" and denied.retry_at is not None
    assert gov.admit(current, "EXECUTE").allowed
    assert not gov.admit(current, "EXECUTE").allowed
    assert gov.admit(current, "READBACK").allowed
    assert not gov.admit(current, "READBACK").allowed
    # 账户满不会错误扣应用桶；应用配额跨同一应用的账户共享。
    assert gov.client.hget(gov.keys(current)[0], "used") == "6"
    another = replace(current, ctx=replace(current.ctx, account_id=uuid4()))
    assert gov.admit(another, "READBACK").allowed
    assert gov.admit(another, "READBACK").allowed
    assert not gov.admit(another, "READBACK").allowed
    assert gov.admit(replace(current, application_id=uuid4()), "SYNC").allowed
    other_tenant = replace(
        current, ctx=replace(current.ctx, identity=replace(current.ctx.identity, tenant_id=999))
    )
    assert gov.keys(other_tenant)[1] != gov.keys(current)[1]
    assert str(current.ctx.account_id) not in "".join(gov.keys(current))


def test_atomic_concurrency_no_overdraft(
    governance: PlatformGovernance, capability: dict[str, Any]
) -> None:
    gov = PlatformGovernance(
        governance.client, QuotaLimits(12, 7, 0, 0, 0, 0, 86400000), namespace=governance.namespace
    )
    current = scope(capability)
    with ThreadPoolExecutor(max_workers=16) as pool:
        tickets = list(pool.map(lambda _: gov.admit(current, "SYNC"), range(40)))
    assert sum(ticket.allowed for ticket in tickets) == 7
    assert gov.client.hget(gov.keys(current)[0], "used") == "7"
    assert gov.client.hget(gov.keys(current)[1], "used") == "7"


def test_window_rollover_and_disabled_lane(
    governance: PlatformGovernance, capability: dict[str, Any]
) -> None:
    gov = PlatformGovernance(
        governance.client, QuotaLimits(2, 2, 1, 1, 1, 1, 60000), namespace=governance.namespace
    )
    current = scope(capability)
    denied = gov.admit(current, "SYNC")
    assert denied.reason == "LANE_DISABLED" and denied.retry_at is None
    assert gov.admit(current, "READBACK").allowed
    assert gov.admit(current, "READBACK").allowed
    assert not gov.admit(current, "READBACK").allowed
    for key in gov.keys(current)[:2]:
        gov.client.hset(key, "window", "0")  # 实际Redis故障/时窗输入，不等待时钟边界。
    assert gov.admit(current, "READBACK").allowed
    assert gov.client.hget(gov.keys(current)[0], "used") == "1"


def test_circuit_open_single_probe_fencing_recovery(
    governance: PlatformGovernance, capability: dict[str, Any]
) -> None:
    gov = governance
    current = scope(capability)
    for _ in range(3):
        ticket = gov.admit(current, "SYNC")
        assert ticket.allowed
        gov.report(ticket, Outcome("UPSTREAM_ERROR", "READ_FAILED"))
    assert gov.admit(current, "SYNC").reason == "CIRCUIT_OPEN"
    for key in gov.keys(current)[2:4]:
        gov.client.hset(key, "open_until", "0")
    assert gov.admit(current, "SYNC").reason == "PROBE_REQUIRED"
    with ThreadPoolExecutor(max_workers=8) as pool:
        tickets = list(pool.map(lambda _: gov.admit(current, "PROBE"), range(8)))
    admitted = [ticket for ticket in tickets if ticket.allowed]
    assert len(admitted) == 1
    first = admitted[0]
    for key in gov.keys(current)[2:4]:
        gov.client.hset(key, "probe_until", "0")
    second = gov.admit(current, "PROBE")
    assert second.allowed
    assert first.probe_token != second.probe_token
    assert gov.report(first, Outcome("READ_SUCCEEDED", "READ_SUCCEEDED"))["changed"] == 0
    assert gov.inspect(current)["application"]["state"] == "HALF_OPEN"
    gov.report(second, Outcome("READ_SUCCEEDED", "READ_SUCCEEDED"))
    assert gov.inspect(current)["application"]["state"] == "CLOSED"
    assert gov.admit(current, "SYNC").allowed
    assert gov.report(first, Outcome("UPSTREAM_ERROR", "READ_FAILED"))["changed"] == 0


def test_429_backoff_manual_hold_and_alert_dedup(
    governance: PlatformGovernance, capability: dict[str, Any]
) -> None:
    gov = governance
    current = scope(capability)
    alerts: list[dict[str, Any]] = []
    gov.alert_sink = alerts.append
    tickets = [gov.admit(current, "SYNC") for _ in range(10)]
    for ticket in tickets[:-1]:
        gov.report(ticket, Outcome("RATE_LIMITED", "READ_FAILED", True, 30))
    denied = gov.admit(current, "SYNC")
    assert denied.reason == "RATE_LIMITED" and denied.retry_at_ms - denied.server_ms >= 29000
    assert len(alerts) == 1
    assert gov.report(tickets[0], Outcome("UPSTREAM_ERROR", "READ_FAILED"))["changed"] == 0
    gov.report(tickets[-1], Outcome("RATE_LIMITED", "READ_FAILED", True, None, True))
    denied = gov.admit(current, "SYNC")
    assert denied.reason == "MANUAL_REVIEW" and denied.retry_at is None
    gov.release_manual(current, "local-test/proof-1")
    assert gov.admit(current, "SYNC").reason == "PROBE_REQUIRED"
    probe = gov.admit(current, "PROBE")
    gov.report(probe, Outcome("READ_SUCCEEDED", "READ_SUCCEEDED"))
    assert gov.admit(current, "SYNC").allowed


def test_account_only_rate_limit_and_alert_failure(
    governance: PlatformGovernance, capability: dict[str, Any]
) -> None:
    gov = governance
    current = scope(capability)

    def broken(_: dict[str, Any]) -> None:
        raise RuntimeError("synthetic-alert-transport-failure")

    gov.alert_sink = broken
    result = gov.report(
        gov.admit(current, "SYNC"),
        Outcome("RATE_LIMITED", "READ_FAILED", True, 20),
        rate_scope="ACCOUNT",
    )
    assert result["alertDelivered"] is False
    assert gov.admit(current, "SYNC").reason == "RATE_LIMITED"
    other = replace(current, ctx=replace(current.ctx, account_id=uuid4()))
    assert gov.admit(other, "SYNC").allowed
    assert gov.inspect(current)["application"].get("cooldown_until") is None


def test_unavailable_redis_fails_closed(capability: dict[str, Any]) -> None:
    gov = PlatformGovernance.from_url(
        SecretStr("redis://127.0.0.1:1/0"), QuotaLimits(10, 10, 1, 1, 1, 1, 60000)
    )
    try:
        with pytest.raises(GovernanceUnavailable, match="禁止新调用"):
            gov.admit(scope(capability), "SYNC")
    finally:
        gov.client.close()


async def test_governed_wire_single_attempt_denial_and_replay(
    governance: PlatformGovernance, capability: dict[str, Any]
) -> None:
    ctx, spec, target, archive = setup(capability)
    calls = []

    def platform(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "35"}, json={"code": 1})

    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(signer, transport=httpx.MockTransport(platform)),
            archive,
            decoder=decoder,
        ),
        governance,
        uuid4(),
        write_preflight=quota_only_preflight,
    )
    outbound = uuid4()
    result = await client.send(
        ctx, spec, outbound, parameters(ctx), SecretStr("synthetic"), lane="EXECUTE", target=target
    )
    assert result.metadata["networkAttempts"] == 1 and result.metadata["category"] == "RATE_LIMITED"
    replay = await client.send(
        ctx, spec, outbound, parameters(ctx), SecretStr("synthetic"), lane="EXECUTE", target=target
    )
    assert replay.metadata["archiveId"] == result.metadata["archiveId"] and len(calls) == 1
    with pytest.raises(CallDeferred) as denied:
        await client.send(
            ctx,
            spec,
            uuid4(),
            parameters(ctx),
            SecretStr("synthetic"),
            lane="EXECUTE",
            target=target,
        )
    assert denied.value.decision.reason == "RATE_LIMITED" and len(calls) == 1
    with business_transaction(capability["engine"], ctx.identity) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM platform_requests WHERE account_id=:id"),
                {"id": ctx.account_id},
            ).scalar_one()
            == 1
        )
    with pytest.raises(ValueError, match="通道"):
        await client.send(
            ctx, spec, uuid4(), parameters(ctx), SecretStr("synthetic"), lane="PROBE", target=target
        )


async def test_open_circuit_prevents_wire_then_only_read_probe_recovers(
    governance: PlatformGovernance, capability: dict[str, Any]
) -> None:
    cap = capability
    ctx, spec, target, archive = setup(cap)
    calls = []
    successful = False

    def platform(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200 if successful else 500, json={"code": 0})

    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(signer, transport=httpx.MockTransport(platform)),
            archive,
            decoder=decoder,
        ),
        governance,
        uuid4(),
        write_preflight=quota_only_preflight,
    )
    for _ in range(3):
        result = await client.send(
            ctx,
            spec,
            uuid4(),
            parameters(ctx),
            SecretStr("synthetic"),
            lane="EXECUTE",
            target=target,
        )
        assert result.metadata["externalOutcome"] == "UNKNOWN"
    with pytest.raises(CallDeferred) as error:
        await client.send(
            ctx,
            spec,
            uuid4(),
            parameters(ctx),
            SecretStr("synthetic"),
            lane="EXECUTE",
            target=target,
        )
    assert error.value.decision.reason == "CIRCUIT_OPEN" and len(calls) == 3
    current = QuotaScope(client.application_id, ctx)
    for key in governance.keys(current)[2:4]:
        governance.client.hset(key, "open_until", "0")
    with pytest.raises(CallDeferred) as error:
        await client.send(
            ctx,
            spec,
            uuid4(),
            parameters(ctx),
            SecretStr("synthetic"),
            lane="EXECUTE",
            target=target,
        )
    assert error.value.decision.reason == "PROBE_REQUIRED" and len(calls) == 3
    read = cap["binding"].model_copy(
        update={
            "id": uuid4(),
            "operation_kind": "READ",
            "http_method": "GET",
            "normalized_path": "/v3.0/adgroups/get",
            "catalog_id": "90b086afd649f5dc0600",
            "permission_code": "overview.read",
            "action": "analyze",
        }
    )
    with cap["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_binding(owner, read)
    readctx = replace(ctx, binding_id=read.id, catalog_id=read.catalog_id)
    readspec = replace(
        spec, effect="READ", method="GET", path=read.normalized_path, catalog_id=read.catalog_id
    )
    successful = True
    probe = await client.send(
        readctx,
        readspec,
        uuid4(),
        parameters(ctx),
        SecretStr("synthetic"),
        lane="PROBE",
        target=target,
    )
    assert probe.metadata["externalOutcome"] == "READ_SUCCEEDED" and len(calls) == 4
    assert governance.inspect(current)["application"]["state"] == "CLOSED"
    assert governance.admit(current, "EXECUTE").allowed


async def test_recorded_response_preserved_on_reporting_failure_and_replay_skips_redis(
    governance: PlatformGovernance, capability: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, spec, target, archive = setup(capability)
    calls = []

    def platform(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"code": 0})

    def failed_report(*args: Any, **kwargs: Any) -> None:
        raise GovernanceUnavailable()  # 故障注入，不宣称真实Redis服务中断。

    monkeypatch.setattr(governance, "report", failed_report)
    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(signer, transport=httpx.MockTransport(platform)),
            archive,
            decoder=decoder,
        ),
        governance,
        uuid4(),
        write_preflight=quota_only_preflight,
    )
    outbound = uuid4()
    result = await client.send(
        ctx, spec, outbound, parameters(ctx), SecretStr("synthetic"), lane="EXECUTE", target=target
    )
    assert (
        result.metadata["externalOutcome"] == "ACCEPTED"
        and not result.metadata["governanceRecorded"]
    )
    unavailable = PlatformGovernance.from_url(SecretStr("redis://127.0.0.1:1/0"), governance.limits)
    client.governance = unavailable
    try:
        replay = await client.send(
            ctx,
            spec,
            outbound,
            parameters(ctx),
            SecretStr("synthetic"),
            lane="EXECUTE",
            target=target,
        )
        assert replay.metadata["externalOutcome"] == "ACCEPTED" and len(calls) == 1
        with pytest.raises(GovernanceUnavailable):
            await client.send(
                ctx,
                spec,
                uuid4(),
                parameters(ctx),
                SecretStr("synthetic"),
                lane="EXECUTE",
                target=target,
            )
        assert len(calls) == 1
    finally:
        unavailable.client.close()


def scheduled(
    cap: dict[str, Any], ctx: GatewayContext, *, attempts: int = 1, expired: bool = False
) -> UUID:
    event_id, job_id, task_id = uuid4(), uuid4(), uuid4()
    with business_transaction(cap["engine"], ctx.identity) as session:
        session.execute(
            text("""INSERT INTO outbox_events(event_id,tenant_id,aggregate_type,aggregate_id,
              event_type,payload) VALUES(:event,:tenant,'job',:job,
              'yuliang.system.schedule_recovery',CAST(:payload AS jsonb))"""),
            {
                "event": event_id,
                "tenant": ctx.identity.tenant_id,
                "job": str(job_id),
                "payload": json.dumps(
                    {
                        "event_id": str(event_id),
                        "tenant_id": ctx.identity.tenant_id,
                        "job_id": str(job_id),
                        "membership_id": str(ctx.identity.membership_id),
                        "user_id": str(ctx.identity.user_id),
                    }
                ),
            },
        )
        session.execute(
            text("""INSERT INTO scheduled_tasks(id,tenant_id,kind,business_key,state,payload,due_at,
          attempts,max_attempts,lease_owner,lease_until,source_event_id)
          VALUES(:id,:tenant,'connector.sync',:key,'CLAIMED','{}',now(),:attempts,3,'local-worker',:until,:event)"""),
            {
                "id": task_id,
                "tenant": ctx.identity.tenant_id,
                "key": str(task_id),
                "attempts": attempts,
                "until": datetime.now(UTC) + timedelta(seconds=-30 if expired else 60),
                "event": event_id,
            },
        )
    return task_id


@pytest.mark.parametrize("mode", ["reschedule", "exhausted", "manual", "wrong_owner", "expired"])
def test_pg_reschedule_lease_and_maximum(
    governance: PlatformGovernance, capability: dict[str, Any], mode: str
) -> None:
    current = scope(capability)
    ctx = current.ctx
    ticket = governance.admit(current, "SYNC")
    governance.report(ticket, Outcome("RATE_LIMITED", "READ_FAILED", True, 40, mode == "manual"))
    decision = governance.admit(current, "SYNC")
    task = scheduled(
        capability, ctx, attempts=3 if mode == "exhausted" else 1, expired=mode == "expired"
    )
    with business_transaction(capability["engine"], ctx.identity) as session:
        if mode in {"wrong_owner", "expired"}:
            with pytest.raises(ValueError, match="租约"):
                reschedule_connector_task(
                    session,
                    ctx.identity,
                    task,
                    "wrong" if mode == "wrong_owner" else "local-worker",
                    decision,
                )
            return
        state = reschedule_connector_task(session, ctx.identity, task, "local-worker", decision)
        assert state == ("FAILED" if mode in {"manual", "exhausted"} else "RESCHEDULED")
        row = (
            session.execute(text("SELECT * FROM scheduled_tasks WHERE id=:id"), {"id": task})
            .mappings()
            .one()
        )
        assert row["lease_owner"] is None and row["lease_until"] is None
        assert row["last_error"] == "CONNECTOR_" + decision.reason
        if mode == "reschedule":
            assert row["due_at"] >= decision.retry_at and row["attempts"] == 1
