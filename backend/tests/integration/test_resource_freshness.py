"""真实PG/Redis验证缓存与后端写预检；平台及OAuth换码输入全部合成。"""

import time
from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.freshness import (
    ResourceFreshness,
    ResourcePolicy,
    ResourceWriteGuard,
    configure_policy,
    register_requirement,
)
from yuliang.modules.connectors.governance import QuotaScope
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.sync_store import MirrorRecord, SyncLease, SyncPage
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_identity_database import database as database
from .test_oauth_vault import SyntheticExchange
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_platform_payloads import decoder, parameters, setup, signer
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


async def snapshot(
    data: dict[str, Any], *, status: int = 200, known_source: bool = True
) -> tuple[SyncLease, SyncPage]:
    ctx, spec, store = data["readctx"], data["readspec"], data["store"]
    lease = store.begin(ctx, spec, "plan.state", uuid4(), parameters(ctx))
    assert isinstance(lease, SyncLease)
    permit = store.prepare(ctx, spec, lease)
    client = RecordedPlatformClient(
        PlatformHttpClient(
            signer,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    status, json={"code": 0, "data": {"id": "456", "budget_minor": 100}}
                )
            ),
        ),
        data["archive"],
        decoder=decoder,
    )
    payload = parameters(ctx) | {"cursor": None}
    result = await client.send(
        ctx,
        spec,
        permit.outbound_id,
        payload,
        SecretStr("synthetic"),
        target=data["target"],
        before_persist=lambda session: store.dispatch_guard(session, ctx, spec, permit, payload),
    )
    assert result.exchange is not None
    fetched = result.exchange.received_at
    page = SyncPage(
        (MirrorRecord(native_id="456", values={"budget_minor": 100}),),
        None,
        True,
        fetched,
        fetched - timedelta(seconds=1) if known_source else None,
    )
    if status >= 400:
        store.stop(ctx, spec, lease, "PARTIAL")
    else:
        store.commit_page(ctx, spec, permit, page)
    return lease, page


def configure(data: dict[str, Any], policy: ResourcePolicy, *, register: bool = True) -> None:
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        configure_policy(owner, data["source"], policy, "synthetic/M1-16/policy")
        if register:
            register_requirement(
                owner, data["ctx"].binding_id, data["source"], "456", "synthetic/M1-16/requirement"
            )


@pytest.fixture
async def health_ready(
    sync_ready: dict[str, Any], oauth_ready: dict[str, Any], governance: Any
) -> dict[str, Any]:
    data = sync_ready
    oauth = oauth_ready
    start = oauth["service"].start(
        oauth["identity"], oauth["session"], oauth["application"], uuid4()
    )
    await oauth["service"].complete(
        start.state, SecretStr("synthetic-code"), oauth["application"], SyntheticExchange(oauth)
    )
    write_ctx, write_spec, target, archive = setup(data)
    read_ctx = replace(data["ctx"], authorization_id=start.authorization_id)
    write_ctx = replace(write_ctx, authorization_id=start.authorization_id)
    data = data | {
        "ctx": write_ctx,
        "spec": replace(write_spec, preflight_resources=("plan_state",)),
        "readctx": read_ctx,
        "readspec": data["spec"],
        "target": target,
        "archive": archive,
        "application": oauth["application"],
        "governance": governance,
        "writes": [],
    }
    lease, page = await snapshot(data)
    data["source"] = lease.source_id
    data["good_lease"] = lease
    data["good_page"] = page
    policy = ResourcePolicy(
        resource_key="plan_state",
        max_age_seconds=60,
        refresh_seconds=10,
        target_mode="OBJECT",
        required_fields=(("budget_minor", "INTEGER"),),
    )
    data["policy"] = policy
    configure(data, policy)
    service = ResourceFreshness(data["engine"])
    data["health"] = service

    def platform(request: httpx.Request) -> httpx.Response:
        data["writes"].append(request)
        return httpx.Response(200, json={"code": 0})

    recorded = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(platform)),
        archive,
        decoder=decoder,
    )
    data["recorded"] = recorded
    data["guard"] = ResourceWriteGuard(service, data["application"].id)
    data["client"] = GovernedPlatformClient(
        recorded, governance, data["application"].id, write_preflight=data["guard"]
    )
    return data


async def send(data: dict[str, Any], *, outbound: UUID | None = None, target: Any = None) -> Any:
    actual = target or data["target"]
    payload = parameters(data["ctx"]) | {"object_id": actual.native_id}
    return await data["client"].send(
        data["ctx"],
        data["spec"],
        outbound or uuid4(),
        payload,
        SecretStr("synthetic-credential"),
        lane="EXECUTE",
        target=actual,
    )


async def test_healthy_write_and_query_time_does_not_change_data_time(
    health_ready: dict[str, Any],
) -> None:
    data = health_ready
    view = data["health"].view(data["ctx"], data["source"])
    next_view = data["health"].view(data["ctx"], data["source"])
    assert view["status"] == "FRESH" and view["sourceUpdatedAt"] == data["good_page"].source_at
    assert view["fetchedAt"] == next_view["fetchedAt"] == data["good_page"].fetched_at
    assert next_view["queriedAt"] >= view["queriedAt"] and view["queriedAt"] != view["fetchedAt"]
    result = await send(data)
    assert result.metadata["externalOutcome"] == "ACCEPTED" and len(data["writes"]) == 1
    assert "synthetic-private-access" not in str(view)


@pytest.mark.parametrize(
    "mode",
    [
        "stale",
        "future",
        "unknown",
        "missing_field",
        "auth_expired",
        "wrong_app",
        "paused",
        "policy_changed",
        "fetch_unknown",
    ],
)
async def test_backend_guard_denies_without_new_request_or_quota(
    health_ready: dict[str, Any], mode: str
) -> None:
    data = health_ready
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if mode in {"stale", "future", "unknown"}:
            values = {
                "stale": data["good_page"].fetched_at - timedelta(seconds=120),
                "future": data["good_page"].fetched_at + timedelta(days=1),
                "unknown": None,
            }
            owner.execute(
                text("UPDATE sync_pages SET source_updated_at=:at WHERE run_id=:id"),
                {"at": values[mode], "id": data["good_lease"].run_id},
            )
        elif mode == "auth_expired":
            owner.execute(
                text(
                    "UPDATE provider_authorizations SET "
                    "expires_at=clock_timestamp()-interval '1 second' "
                    "WHERE id=:id"
                ),
                {"id": data["ctx"].authorization_id},
            )
        elif mode == "wrong_app":
            owner.execute(
                text("UPDATE provider_authorizations SET application_id=:app WHERE id=:id"),
                {"app": uuid4(), "id": data["ctx"].authorization_id},
            )
        elif mode == "paused":
            owner.execute(
                text("UPDATE sync_sources SET enabled=false WHERE id=:id"), {"id": data["source"]}
            )
        elif mode == "fetch_unknown":
            owner.execute(
                text("UPDATE sync_pages SET fetched_at=NULL WHERE run_id=:id"),
                {"id": data["good_lease"].run_id},
            )
    if mode == "missing_field":
        configure(
            data,
            data["policy"].model_copy(update={"required_fields": (("balance_minor", "INTEGER"),)}),
        )
    if mode == "policy_changed":
        configure(data, data["policy"].model_copy(update={"max_age_seconds": 120}), register=False)
    before = data["health"].view(data["ctx"], data["source"])
    with pytest.raises(ApiError):
        await send(data)
    assert not data["writes"] and before["dataAvailable"]
    keys = data["governance"].keys(QuotaScope(data["application"].id, data["ctx"]))
    assert data["governance"].client.exists(*keys[:2]) == 0
    with business_transaction(data["engine"], data["ctx"].identity) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM platform_requests WHERE effect='WRITE'")
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text("SELECT projection FROM sync_resource_mirrors WHERE source_id=:id"),
                {"id": data["source"]},
            ).scalar_one()["data"]["budget_minor"]
            == 100
        )
    if mode == "auth_expired":
        assert before["authorizationState"] == "EXPIRED" and before["status"] == "DEGRADED"
    if mode in {"unknown", "future"}:
        assert before["status"] == "UNKNOWN"
    if mode == "stale":
        assert before["status"] == "STALE"
    if mode == "fetch_unknown":
        assert before["status"] == "UNKNOWN" and before["fetchedAt"] is None


async def test_upstream_failure_shared_safe_summary_keeps_old_values_and_recovers(
    health_ready: dict[str, Any],
) -> None:
    data = health_ready
    old = data["health"].view(data["ctx"], data["source"])
    await snapshot(data, status=500)
    other = replace(
        data["ctx"],
        identity=VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"]),
    )
    view = data["health"].view(other, data["source"])
    assert view["status"] == "DEGRADED" and view["reason"] == "UPSTREAM_ERROR"
    assert (
        view["fetchedAt"] == old["fetchedAt"] and view["sourceUpdatedAt"] == old["sourceUpdatedAt"]
    )
    with business_transaction(data["engine"], other.identity) as session:
        assert session.execute(text("SELECT count(*) FROM platform_raw_payloads")).scalar_one() == 0
    with pytest.raises(ApiError):
        await send(data)
    await snapshot(data)
    assert data["health"].view(data["ctx"], data["source"])["status"] == "FRESH"
    await send(data)
    assert len(data["writes"]) == 1


async def test_explicit_fetch_clock_keeps_source_time_null(health_ready: dict[str, Any]) -> None:
    data = health_ready
    await snapshot(data, known_source=False)
    assert data["health"].view(data["ctx"], data["source"])["sourceUpdatedAt"] is None
    with pytest.raises(ApiError):
        await send(data)
    configure(data, data["policy"].model_copy(update={"clock_kind": "FETCHED"}))
    view = data["health"].view(data["ctx"], data["source"])
    assert (
        view["status"] == "FRESH" and view["sourceUpdatedAt"] is None and view["clock"] == "FETCHED"
    )
    await send(data)


async def test_default_missing_guard_target_and_scope_refused(health_ready: dict[str, Any]) -> None:
    data = health_ready
    default = GovernedPlatformClient(data["recorded"], data["governance"], data["application"].id)
    original = data["client"]
    data["client"] = default
    with pytest.raises(ApiError):
        await send(data)
    data["client"] = original
    with pytest.raises(ApiError):
        await send(data, target=data["target"].model_copy(update={"native_id": "789"}))
    with pytest.raises(ApiError):
        data["health"].view(
            replace(data["ctx"], identity=replace(data["ctx"].identity, tenant_id=999999)),
            data["source"],
        )
    assert not data["writes"]


async def test_sync_local_rate_deferral_does_not_freeze_reserved_execution(
    health_ready: dict[str, Any],
) -> None:
    data = health_ready
    ctx = data["readctx"]
    spec = data["readspec"]
    lease = data["store"].begin(ctx, spec, "plan.state", uuid4(), parameters(ctx))
    assert isinstance(lease, SyncLease)
    data["store"].stop(ctx, spec, lease, "RATE_LIMITED")  # 无平台出站；不伪造上游失败。
    assert data["health"].view(data["ctx"], data["source"])["status"] == "FRESH"
    await send(data)
    assert len(data["writes"]) == 1


async def test_original_key_replay_survives_expiry_and_runtime_cannot_change_policy(
    health_ready: dict[str, Any],
) -> None:
    data = health_ready
    key = uuid4()
    first = await send(data, outbound=key)
    with business_transaction(data["engine"], data["ctx"].identity) as session:
        with pytest.raises(ValueError, match="维护身份"):
            configure_policy(
                session.connection(), data["source"], data["policy"], "synthetic/policy"
            )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""UPDATE provider_authorizations
            SET expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"""),
            {"id": data["ctx"].authorization_id},
        )
    replay = await send(data, outbound=key)
    assert replay.metadata["archiveId"] == first.metadata["archiveId"] and len(data["writes"]) == 1
    with pytest.raises(ApiError):
        await send(data)


async def test_data_expiring_during_quota_admission_is_rechecked(
    health_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = health_ready
    configure(
        data,
        data["policy"].model_copy(
            update={"max_age_seconds": 5, "refresh_seconds": 1, "clock_kind": "FETCHED"}
        ),
    )
    await snapshot(data)
    admit = data["governance"].admit

    def delayed_admission(*args: Any, **kwargs: Any) -> Any:
        ticket = admit(*args, **kwargs)  # 真实Redis先准入，注入有界本地延迟，不声称真实Redis故障。
        time.sleep(5.1)
        return ticket

    monkeypatch.setattr(data["governance"], "admit", delayed_admission)
    with pytest.raises(ApiError):
        await send(data)
    assert not data["writes"]
    keys = data["governance"].keys(QuotaScope(data["application"].id, data["ctx"]))
    assert data["governance"].client.hget(keys[0], "used") == "1"  # 保守保留已经预占的配额。
    with business_transaction(data["engine"], data["ctx"].identity) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM platform_requests WHERE effect='WRITE'")
            ).scalar_one()
            == 0
        )
