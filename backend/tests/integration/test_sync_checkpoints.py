"""真实PG分页事务/故障/租约恢复；HTTP为合成平台，非真实同步验收。"""

import secrets
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretBytes, SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.capability.service import register_binding
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.outcomes import BusinessVerdict
from yuliang.modules.connectors.sync_cursor import CursorCodec
from yuliang.modules.connectors.sync_store import (
    MirrorRecord,
    PagePermit,
    SyncConflict,
    SyncLease,
    SyncPage,
    SyncStore,
)
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_identity_database import database as database
from .test_password_sessions import login_api as login_api
from .test_platform_payloads import Parameters, decoder, parameters, setup, signer

pytestmark = pytest.mark.integration


class PagedParameters(Parameters):
    cursor: str | None = None


@pytest.fixture
def sync_ready(capability: dict[str, Any]) -> Iterator[dict[str, Any]]:
    cap = capability
    ctx, spec, target, archive = setup(cap)
    binding = cap["binding"].model_copy(
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
        register_binding(owner, binding)
    ctx = replace(ctx, binding_id=binding.id, catalog_id=binding.catalog_id)
    spec = replace(
        spec,
        effect="READ",
        method="GET",
        path=binding.normalized_path,
        catalog_id=binding.catalog_id,
        idempotent_read=True,
        cursor_parameter="cursor",
        parameters_model=PagedParameters,
    )
    codec = CursorCodec(
        {1: SecretBytes(secrets.token_bytes(32))}, 1, SecretStr(secrets.token_urlsafe(48))
    )
    yield cap | {
        "ctx": ctx,
        "spec": spec,
        "target": target,
        "archive": archive,
        "codec": codec,
        "store": SyncStore(cap["engine"], codec),
    }


def begin(data: dict[str, Any], run_id: Any = None) -> SyncLease:
    lease = data["store"].begin(
        data["ctx"], data["spec"], "adgroups", run_id or uuid4(), parameters(data["ctx"])
    )
    assert isinstance(lease, SyncLease)
    return lease


def page(
    *, cursor: str | None = "private-page-A", terminal: bool = False, value: int = 0
) -> SyncPage:
    return SyncPage(
        (MirrorRecord(native_id="456", values={"budget_minor": value, "phone": "13800000000"}),),
        SecretStr(cursor) if cursor else None,
        terminal,
        datetime.now(UTC),
    )


async def fetch(
    data: dict[str, Any],
    permit: PagePermit,
    *,
    status: int = 200,
    confirmed: bool = True,
    alter_filters: bool = False,
    omit_cursor: bool = False,
) -> None:
    ctx, spec, store = data["ctx"], data["spec"], data["store"]

    def platform(request: httpx.Request) -> httpx.Response:
        data.setdefault("calls", []).append(request)
        return httpx.Response(status, json={"code": 0})

    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(platform)),
        data["archive"],
        decoder=decoder if confirmed else lambda response: BusinessVerdict(),
    )
    payload = parameters(ctx) | {
        "cursor": permit.cursor.get_secret_value() if permit.cursor else None
    }
    if alter_filters:
        payload["value"] = 999
    if omit_cursor:
        payload["cursor"] = None
    await client.send(
        ctx,
        spec,
        permit.outbound_id,
        payload,
        SecretStr("synthetic"),
        target=data["target"],
        before_persist=lambda session: store.dispatch_guard(session, ctx, spec, permit, payload),
    )


async def test_page_atomic_cursor_mirror_and_duplicate_batch(sync_ready: dict[str, Any]) -> None:
    data = sync_ready
    store = data["store"]
    ctx = data["ctx"]
    spec = data["spec"]
    lease = begin(data)
    permit = store.prepare(ctx, spec, lease)
    with pytest.raises(SyncConflict):
        await fetch(data, replace(permit, cursor=SecretStr("wrong-page")))
    with pytest.raises(SyncConflict):
        await fetch(data, permit, alter_filters=True)
    assert (
        store.prepare(ctx, spec, lease).outbound_id == permit.outbound_id and permit.cursor is None
    )
    await fetch(data, permit)
    first = page()
    next_lease = store.commit_page(ctx, spec, permit, first)
    assert isinstance(next_lease, SyncLease) and next_lease.page_no == 1
    with business_transaction(data["engine"], ctx.identity) as session:
        source = (
            session.execute(
                text("SELECT * FROM sync_sources WHERE id=:id"), {"id": lease.source_id}
            )
            .mappings()
            .one()
        )
        assert source["version"] == 1 and b"private-page-A" not in source["cursor_enc"]
        mirror = session.execute(
            text("SELECT projection FROM sync_resource_mirrors WHERE source_id=:id"),
            {"id": lease.source_id},
        ).scalar_one()
        assert mirror["data"]["budget_minor"] == 0 and "phone" not in str(mirror)
    assert store.commit_page(ctx, spec, permit, first)["confirmedPages"] == 1
    with pytest.raises(SyncConflict) as error:
        store.commit_page(
            ctx,
            spec,
            permit,
            replace(first, records=(MirrorRecord(native_id="456", values={"budget_minor": 99}),)),
        )
    assert error.value.reason == "PAGE_MISMATCH"
    second = store.prepare(ctx, spec, next_lease)
    assert second.cursor and second.cursor.get_secret_value() == "private-page-A"
    with pytest.raises(SyncConflict):
        await fetch(data, second, omit_cursor=True)
    await fetch(data, second)
    result = store.commit_page(ctx, spec, second, page(cursor=None, terminal=True, value=10))
    assert (
        result["state"] == "SUCCESS" and result["confirmedPages"] == 2 and result["itemsCount"] == 2
    )
    assert store.begin(ctx, spec, "adgroups", lease.run_id, parameters(ctx)) == result
    assert len(data["calls"]) == 2


@pytest.mark.parametrize("stage", ["page", "mirror", "cursor"])
async def test_database_fault_never_advances_uncommitted_cursor(
    sync_ready: dict[str, Any], stage: str
) -> None:
    data = sync_ready
    store = data["store"]
    ctx = data["ctx"]
    spec = data["spec"]
    lease = begin(data)
    permit = store.prepare(ctx, spec, lease)
    await fetch(data, permit)
    first = page()
    actions = {
        "page": ("INSERT", "sync_pages"),
        "mirror": ("INSERT", "sync_resource_mirrors"),
        "cursor": (
            "UPDATE(cursor_enc,cursor_key_version,cursor_digest,version,active_run_id)",
            "sync_sources",
        ),
    }
    action, table = actions[stage]
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(text(f"REVOKE {action} ON {table} FROM app_login"))  # noqa: S608 -- 固定测试故障白名单。
    try:
        with pytest.raises(DBAPIError):
            store.commit_page(ctx, spec, permit, first)
        with business_transaction(data["engine"], ctx.identity) as session:
            assert (
                session.execute(
                    text("SELECT version FROM sync_sources WHERE id=:id"), {"id": lease.source_id}
                ).scalar_one()
                == 0
            )
            assert (
                session.execute(
                    text("SELECT count(*) FROM sync_pages WHERE run_id=:id"), {"id": lease.run_id}
                ).scalar_one()
                == 0
            )
            assert (
                session.execute(
                    text("SELECT count(*) FROM sync_resource_mirrors WHERE source_id=:id"),
                    {"id": lease.source_id},
                ).scalar_one()
                == 0
            )
        assert store.status(ctx, lease.run_id)["state"] == "RUNNING"
    finally:
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(text(f"GRANT {action} ON {table} TO app_login"))  # noqa: S608 -- 同一固定白名单恢复。
    assert isinstance(store.commit_page(ctx, spec, permit, first), SyncLease)
    assert len(data["calls"]) == 1


async def test_resume_from_last_committed_page_and_old_fence_refused(
    sync_ready: dict[str, Any],
) -> None:
    data = sync_ready
    store = data["store"]
    ctx = data["ctx"]
    spec = data["spec"]
    lease = begin(data)
    first = store.prepare(ctx, spec, lease)
    await fetch(data, first)
    next_lease = store.commit_page(ctx, spec, first, page())
    assert isinstance(next_lease, SyncLease)
    uncommitted = store.prepare(ctx, spec, next_lease)
    await fetch(data, uncommitted)
    # 构造崩溃检查点：第二页已读、未提交，租约失效；不称杀真实Worker演练。
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE sync_runs SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE id=:id"
            ),
            {"id": lease.run_id},
        )
    restarted = SyncStore(data["engine"], data["codec"])
    resumed = restarted.begin(ctx, spec, "adgroups", lease.run_id, parameters(ctx))
    assert isinstance(resumed, SyncLease)
    assert resumed.page_no == 1 and resumed.token != next_lease.token
    with pytest.raises(SyncConflict):
        store.commit_page(ctx, spec, uncommitted, page(cursor=None, terminal=True, value=99))
    retry = restarted.prepare(ctx, spec, resumed)
    assert retry.cursor and retry.cursor.get_secret_value() == "private-page-A"
    assert retry.outbound_id != uncommitted.outbound_id
    await fetch(data, retry)
    result = restarted.commit_page(ctx, spec, retry, page(cursor=None, terminal=True, value=2))
    assert isinstance(result, dict)
    assert result["state"] == "SUCCESS" and result["itemsCount"] == 2 and len(data["calls"]) == 3


@pytest.mark.parametrize("status,confirmed", [(200, False), (429, True), (500, True)])
async def test_unconfirmed_or_upstream_error_cannot_commit(
    sync_ready: dict[str, Any], status: int, confirmed: bool
) -> None:
    data = sync_ready
    store = data["store"]
    ctx = data["ctx"]
    spec = data["spec"]
    lease = begin(data)
    permit = store.prepare(ctx, spec, lease)
    await fetch(data, permit, status=status, confirmed=confirmed)
    with pytest.raises(SyncConflict) as error:
        store.commit_page(ctx, spec, permit, page())
    assert error.value.reason == "NO_CONFIRMED_READ"
    store.stop(ctx, spec, lease, "RATE_LIMITED" if status == 429 else "PARTIAL")
    assert store.status(ctx, lease.run_id)["confirmedPages"] == 0
    resumed = begin(data, lease.run_id)
    assert store.prepare(ctx, spec, resumed).cursor is None


async def test_pause_before_dispatch_no_new_request_but_accepted_page_may_finish(
    sync_ready: dict[str, Any],
) -> None:
    data = sync_ready
    store = data["store"]
    ctx = data["ctx"]
    spec = data["spec"]
    lease = begin(data)
    permit = store.prepare(ctx, spec, lease)
    admin = replace(
        ctx,
        identity=VerifiedIdentity(
            ctx.identity.tenant_id, data["approver_member"], data["approver"]
        ),
    )
    with pytest.raises(ApiError):
        store.set_enabled(ctx, spec, lease.source_id, False)
    store.set_enabled(admin, spec, lease.source_id, False)
    with pytest.raises(SyncConflict):
        await fetch(data, permit)
    assert not data.get("calls")
    with business_transaction(data["engine"], ctx.identity) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM platform_requests WHERE id=:id"),
                {"id": permit.outbound_id},
            ).scalar_one()
            == 0
        )
    store.set_enabled(admin, spec, lease.source_id, True)
    await fetch(data, permit)
    store.set_enabled(admin, spec, lease.source_id, False)
    result = store.commit_page(ctx, spec, permit, page())
    assert result["state"] == "PAUSED" and result["confirmedPages"] == 1
    with pytest.raises(SyncConflict):
        begin(data, lease.run_id)
    store.set_enabled(admin, spec, lease.source_id, True)
    assert begin(data, lease.run_id).page_no == 1


def test_concurrent_batch_claim_and_scope_filter_separation(sync_ready: dict[str, Any]) -> None:
    data = sync_ready
    run = uuid4()
    store = data["store"]
    ctx = data["ctx"]
    spec = data["spec"]

    def claim(_: int) -> bool:
        try:
            begin(data, run)
            return True
        except SyncConflict:
            return False

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(claim, range(12))) == 1
    with pytest.raises(SyncConflict):
        store.begin(ctx, spec, "adgroups", run, parameters(ctx) | {"value": 999})
    other = store.begin(ctx, spec, "adgroups", uuid4(), parameters(ctx) | {"value": 999})
    assert isinstance(other, SyncLease)
    with business_transaction(data["engine"], replace(ctx.identity, tenant_id=999999)) as session:
        assert session.execute(text("SELECT count(*) FROM sync_runs")).scalar_one() == 0
    with pytest.raises(ApiError):
        store.prepare(replace(ctx, identity=replace(ctx.identity, tenant_id=999999)), spec, other)


async def test_cursor_loop_rejected_and_prepared_without_response_not_counted(
    sync_ready: dict[str, Any],
) -> None:
    data = sync_ready
    store = data["store"]
    ctx = data["ctx"]
    spec = data["spec"]
    lease = begin(data)
    first = store.prepare(ctx, spec, lease)
    with pytest.raises(SyncConflict):
        store.commit_page(ctx, spec, first, page())
    await fetch(data, first)
    current = store.commit_page(ctx, spec, first, page())
    assert isinstance(current, SyncLease)
    second = store.prepare(ctx, spec, current)
    await fetch(data, second)
    with pytest.raises(SyncConflict) as error:
        store.commit_page(ctx, spec, second, page())
    assert error.value.reason == "CURSOR_LOOP"
    assert store.status(ctx, lease.run_id)["confirmedPages"] == 1


def test_source_foreign_key_rejects_other_account_binding(sync_ready: dict[str, Any]) -> None:
    data = sync_ready
    ctx = data["ctx"]
    lease = begin(data)
    another = uuid4()
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO ad_accounts(id,tenant_id,provider,product_line,native_id,name)
            VALUES(:id,:tenant,'tencent','adq',:native,'Synthetic other account')"""),
            {"id": another, "tenant": ctx.identity.tenant_id, "native": str(another)},
        )
    with pytest.raises(DBAPIError), business_transaction(data["engine"], ctx.identity) as session:
        session.execute(
            text("""INSERT INTO sync_sources(id,tenant_id,account_id,binding_id,resource_kind,
            context_digest,filter_digest,cursor_digest) SELECT :id,tenant_id,:other,binding_id,
            resource_kind,context_digest,filter_digest,cursor_digest
            FROM sync_sources WHERE id=:source"""),
            {"id": uuid4(), "other": another, "source": lease.source_id},
        )
