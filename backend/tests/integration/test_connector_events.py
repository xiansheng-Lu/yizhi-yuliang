"""真实PG能力/截止/同步生产者与账户双范围；平台读取仅显式Mock。"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.capability.models import Evidence
from yuliang.modules.capability.service import evaluate_binding, record_evidence
from yuliang.platform.db import business_transaction
from yuliang.platform.events import CoreEventStream

from .test_capability_evidence import capability as capability
from .test_core_sse import client, frames, trace
from .test_core_sse import http_stream as http_stream
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import health_ready as health_ready
from .test_resource_freshness import snapshot
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


def evidence(data: dict[str, Any], *, expires_in: float = 3600, age: int = 1) -> Evidence:
    now = datetime.now(UTC)
    return Evidence(
        id=uuid4(),
        binding_id=data["binding"].id,
        phase="PATH_VERIFIED",
        result="PASS",
        proof_kind="DOCUMENTATION",
        proof_ref="synthetic/M4-09b/documentation",
        checked_by="SYNTHETIC_TEST",
        checked_at=now - timedelta(seconds=age),
        expires_at=now + timedelta(seconds=expires_in),
    )


def add_evidence(data: dict[str, Any], item: Evidence) -> None:
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        record_evidence(owner, data["binding"].id, item)


async def test_actual_capability_change_stream_and_duplicate_evidence(
    http_stream: dict[str, Any],
) -> None:
    data = http_stream
    item = evidence(data)
    add_evidence(data, item)
    async with client(data) as browser, browser.stream("GET", "/v1/events") as response:
        incoming = frames(response)
        assert await anext(incoming) == [": connected"]
        frame = await anext(incoming)
        assert frame[1] == "event: capability.changed" and '"status":"CHANGED"' in frame[2]
        assert str(data["binding"].id) in frame[2]
        trace(
            "capability-frame.json",
            {
                "input": "REAL_LOCAL_HTTP_PG_MAINTENANCE_SYNTHETIC_EVIDENCE",
                "requestId": response.headers["x-request-id"],
                "frame": frame,
                "assertionId": str(item.id),
                "bindingContractRevision": data["binding"].revision,
            },
        )
    current = CoreEventStream(data["engine"], data["identity"])
    baseline = current.read().head_seq
    add_evidence(data, item)
    assert current.project() == 0 and current.read().head_seq == baseline
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT revision FROM capability_bindings WHERE id=:id"),
                {"id": data["binding"].id},
            ).scalar_one()
            == data["binding"].revision
        )


async def test_actual_binding_disable_notifies_inactive(http_stream: dict[str, Any]) -> None:
    data = http_stream
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE capability_bindings SET active=false WHERE id=:id"),
            {"id": data["binding"].id},
        )
    async with client(data) as browser, browser.stream("GET", "/v1/events") as response:
        incoming = frames(response)
        assert await anext(incoming) == [": connected"]
        frame = await anext(incoming)
        assert frame[1] == "event: capability.changed" and '"status":"INACTIVE"' in frame[2]


async def test_expiry_captured_once_at_due_time_with_monotonic_notification_revision(
    http_stream: dict[str, Any],
) -> None:
    data = http_stream
    current = CoreEventStream(data["engine"], data["identity"])
    latest = evidence(data, expires_in=0.3)
    add_evidence(data, latest)
    current.project()
    before = current.read(data["initial_cursor"])
    version = before.events[-1].data.version
    assert current.capture_due() == 0
    # 后到旧证据不覆盖最新checked_at；其到期通知也应在处理时分修订，不能预分配未来版本。
    older = evidence(data, expires_in=0.2, age=10)
    add_evidence(data, older)
    current.project()
    middle = current.read(before.cursor)
    assert middle.events[-1].data.version > version
    await asyncio.sleep(0.4)
    assert current.capture_due(limit=1) == 1
    assert current.capture_due(limit=1) == 1 and current.capture_due() == 0
    assert current.project() == 2
    after = current.read(middle.cursor)
    assert len(after.events) == 2 and after.events[0].data.version > middle.events[-1].data.version
    assert after.events[1].data.version > after.events[0].data.version
    with business_transaction(data["engine"], data["identity"]) as session:
        actual = evaluate_binding(session, data["identity"], data["shop"], data["binding"].id)
    assert not actual["stages"]["PATH_VERIFIED"]["effective"] and actual["operations"] == []


def test_capability_source_and_deadline_rollback_with_real_evidence_insert(
    http_stream: dict[str, Any],
) -> None:
    data = http_stream
    item = evidence(data)
    current = CoreEventStream(data["engine"], data["identity"])
    before = current.read().head_seq
    with pytest.raises(RuntimeError, match="rollback"):
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            record_evidence(owner, data["binding"].id, item)
            raise RuntimeError("rollback")
    assert current.project() == 0 and current.read().head_seq == before
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert (
            owner.execute(
                text("SELECT count(*) FROM capability_event_deadlines WHERE assertion_id=:id"),
                {"id": item.id},
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize("status,state", [(200, "SUCCESS"), (503, "PARTIAL")])
async def test_actual_sync_commit_or_stop_notifies_its_real_state(
    http_stream: dict[str, Any], status: int, state: str
) -> None:
    data = http_stream
    lease, _ = await snapshot(data, status=status)
    async with client(data) as browser, browser.stream("GET", "/v1/events") as response:
        incoming = frames(response)
        assert await anext(incoming) == [": connected"]
        frame = await anext(incoming)
        assert frame[1] == "event: sync.completed" and f'"status":"{state}"' in frame[2]
        assert str(lease.source_id) in frame[2]
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT state FROM sync_runs WHERE id=:id"), {"id": lease.run_id}
            ).scalar_one()
            == state
        )


@pytest.mark.parametrize("scope", ["SHOP", "ACCOUNT", "MASK", "BOTH"])
def test_connector_notifications_require_current_action_and_both_resource_scopes(
    http_stream: dict[str, Any], scope: str
) -> None:
    data = http_stream
    add_evidence(data, evidence(data))
    current = CoreEventStream(data["engine"], data["identity"])
    current.project()
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:id"),
            {"id": data["identity"].membership_id},
        )
        for kind in ("SHOP", "ACCOUNT") if scope in {"MASK", "BOTH"} else (scope,):
            owner.execute(
                text("""INSERT INTO data_scopes(tenant_id,membership_id,scope_type,
                shop_id,account_id,permissions_mask)
                VALUES(:tenant,:member,:kind,:shop,:account,CAST(:mask AS text[]))"""),
                {
                    "tenant": data["tenant"],
                    "member": data["identity"].membership_id,
                    "kind": kind,
                    "shop": data["shop"] if kind == "SHOP" else None,
                    "account": data["ctx"].account_id if kind == "ACCOUNT" else None,
                    "mask": ["overview.read"] if scope == "MASK" else None,
                },
            )
    page = current.read(data["initial_cursor"])
    assert bool(page.events) == (scope == "BOTH")


@pytest.mark.parametrize("table", ["event_resource_heads", "capability_event_deadlines"])
def test_runtime_cannot_forge_notification_revisions_or_deadlines(
    http_stream: dict[str, Any], table: str
) -> None:
    data = http_stream
    with pytest.raises(DBAPIError) as denied:
        with business_transaction(data["engine"], data["identity"]) as session:
            session.execute(text(f"INSERT INTO {table} DEFAULT VALUES"))
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
