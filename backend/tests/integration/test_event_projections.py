"""实际业务触发Outbox、提交序并发、回滚/去重/保留及当前RLS；无真实平台调用。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.execution.receipts import ExecutionReceipts
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode
from yuliang.platform.events import CoreEventStream

from .test_approvals import pending, request, reviewer
from .test_budget_initialization import budget_ready as budget_ready
from .test_capability_evidence import capability as capability
from .test_execution_receipts import observation
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import risk_ready as risk_ready
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


def stream(data: dict[str, Any]) -> CoreEventStream:
    return CoreEventStream(data["engine"], data["identity"])


def value(data: dict[str, Any]) -> Any:
    return data["input"].model_copy(update={"idempotency_key": uuid4().hex})


def test_actual_business_and_outbox_rollback_together(applications: dict[str, Any]) -> None:
    data = applications
    with pytest.raises(RuntimeError, match="rollback"):
        with business_transaction(data["engine"], data["identity"]) as session:
            data["applications"].create(value(data), unit_of_work=session)
            raise RuntimeError("rollback")
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert owner.execute(text("SELECT count(*) FROM event_source_outbox")).scalar_one() == 0
        assert owner.execute(text("SELECT count(*) FROM command_jobs")).scalar_one() == 0
    assert stream(data).project() == 0 and stream(data).read().head_seq == 0


def test_lower_identity_late_commit_is_not_skipped_by_cursor(applications: dict[str, Any]) -> None:
    data = applications
    events = stream(data)
    with business_transaction(data["engine"], data["identity"]) as late:
        first = data["applications"].create(value(data), unit_of_work=late)
        second = data["applications"].create(value(data))
        assert events.project() == 1
        before = events.read()
        assert len(before.events) == 1 and str(before.events[0].data.entity_id) == second.job_id
        assert before.events[0].seq == 1
    assert events.project() == 1
    after = events.read(before.cursor)
    assert len(after.events) == 1 and str(after.events[0].data.entity_id) == first.job_id
    assert after.events[0].seq == 2 and events.project() == 0
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        rows = owner.execute(
            text("SELECT id,entity_id,projected_seq FROM event_source_outbox ORDER BY id")
        ).all()
    assert str(rows[0].entity_id) == first.job_id and rows[0].projected_seq == 2
    assert str(rows[1].entity_id) == second.job_id and rows[1].projected_seq == 1
    if evidence := os.getenv("YULIANG_EVENT_EVIDENCE"):
        Path(evidence).write_text(
            json.dumps(
                {
                    "input": "REAL_LOCAL_PG_APPLICATIONS_OUTBOX_AND_PROJECTOR",
                    "lateSourceId": rows[0].id,
                    "earlySourceId": rows[1].id,
                    "beforeLateCommit": before.model_dump(mode="json", by_alias=True),
                    "afterLateCommit": after.model_dump(mode="json", by_alias=True),
                    "requestId": str(data["applications"].request_id),
                    "platformCalls": 0,
                    "paidCalls": 0,
                    "humanSignoff": "PENDING",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def test_projection_rollback_and_repeated_delivery_keep_one_sequence(
    applications: dict[str, Any],
) -> None:
    data = applications
    data["applications"].create(value(data))
    events = stream(data)
    with pytest.raises(RuntimeError, match="rollback"):
        with business_transaction(data["engine"], data["identity"]) as session:
            assert events.project(unit_of_work=session) == 1
            raise RuntimeError("rollback")
    assert events.read().head_seq == 0
    assert events.project() == 1 and events.project() == 0
    first = events.read()
    assert first.head_seq == 1 and len(first.events) == 1
    assert events.read().events == first.events


def test_same_version_conflicting_status_cannot_overwrite_source_notice(
    applications: dict[str, Any],
) -> None:
    data = applications
    job = data["applications"].create(value(data))
    with pytest.raises(DBAPIError) as conflict:
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(
                text("""UPDATE command_jobs SET status='DRAFT',
                document=jsonb_set(jsonb_set(document,'{status}','"DRAFT"'::jsonb),
                  '{history,-1,status}','"DRAFT"'::jsonb) WHERE id=:id"""),
                {"id": job.job_id},
            )
    assert getattr(conflict.value.orig, "sqlstate", "") == "40001"
    events = stream(data)
    assert events.project() == 1
    assert events.read().events[0].data.changes.status == job.status.value


def test_tenant_watermark_lock_held_until_projection_transaction_commits(
    applications: dict[str, Any],
) -> None:
    data = applications
    data["applications"].create(value(data))
    events = stream(data)
    assert events.project(limit=1) == 1
    data["applications"].create(value(data))

    def blocked() -> str:
        try:
            with business_transaction(data["engine"], data["identity"]) as session:
                session.execute(text("SET LOCAL lock_timeout='200ms'"))
                events.project(unit_of_work=session)
        except DBAPIError as error:
            return str(getattr(error.orig, "sqlstate", ""))
        return "UNEXPECTED_COMMIT"

    with ThreadPoolExecutor(max_workers=1) as pool:
        with business_transaction(data["engine"], data["identity"]) as session:
            assert events.project(unit_of_work=session) == 1
            assert events.read().head_seq == 1  # 其他事务不能见未提交的2。
            assert pool.submit(blocked).result(timeout=5) == "55P03"
    assert events.read().head_seq == 2 and events.project() == 0


def test_bounded_pages_and_source_dedupe_survive_seven_day_cleanup(
    applications: dict[str, Any],
) -> None:
    data = applications
    for _ in range(3):
        data["applications"].create(value(data))
    events = stream(data)
    assert events.project(limit=2) == 2 and events.project(limit=2) == 1
    first = events.read(limit=1)
    assert first.has_more and first.events[0].seq == 1
    second = events.read(first.cursor, limit=1)
    assert second.has_more and second.events[0].seq == 2
    third = events.read(second.cursor, limit=1)
    assert not third.has_more and third.events[0].seq == 3
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE event_projections SET created_at=now()-interval '8 days' WHERE tenant_seq<3"
            )
        )
    assert events.prune() == 2 and events.prune() == 0
    old = events.read(f"{data['tenant']}:0")
    assert old.resync_reason == "CURSOR_OLD" and old.events == () and old.retained_from_seq == 3
    current = events.read(f"{data['tenant']}:2")
    assert current.resync_reason is None and current.events[0].seq == 3
    assert events.project() == 0 and events.read().head_seq == 3


def test_current_scope_and_private_owner_filter_event_replay(applications: dict[str, Any]) -> None:
    data = applications
    data["applications"].create(value(data))
    events = stream(data)
    events.project()
    assert len(events.read().events) == 1
    other = CoreEventStream(
        data["engine"],
        VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"]),
    )
    assert other.read().events == ()
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE data_scopes SET permissions_mask=ARRAY['overview.read'] "
                "WHERE membership_id=:id"
            ),
            {"id": data["identity"].membership_id},
        )
    assert events.read().events == ()


@pytest.mark.parametrize("cursor", ["x:1", "1:-1", "01:1", "1:01", "1:9223372036854775808"])
def test_invalid_cursor_rejected_before_query(applications: dict[str, Any], cursor: str) -> None:
    with pytest.raises(ApiError) as invalid:
        stream(applications).read(cursor)
    assert invalid.value.code == ErrorCode.INVALID_INPUT


def test_cross_tenant_and_future_cursor_require_resync(applications: dict[str, Any]) -> None:
    data = applications
    assert stream(data).read(f"{data['tenant'] + 1}:0").resync_reason == "CURSOR_TENANT_CHANGED"
    assert stream(data).read(f"{data['tenant']}:1").resync_reason == "CURSOR_AHEAD"


@pytest.mark.parametrize(
    "table", ["event_source_outbox", "event_projections", "event_stream_heads"]
)
def test_runtime_cannot_forge_events_or_watermarks(
    applications: dict[str, Any], table: str
) -> None:
    data = applications
    for statement in (
        f"INSERT INTO {table} DEFAULT VALUES",
        f"DELETE FROM {table}",  # noqa: S608 -- 固定pytest表枚举，不接受外部标识符。
        f"UPDATE {table} SET tenant_id=tenant_id",  # noqa: S608 -- 同上。
    ):
        with pytest.raises(DBAPIError) as denied:
            with business_transaction(data["engine"], data["identity"]) as session:
                session.execute(text(statement))
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"


def test_actual_approval_events_include_reviewers_without_exposing_job_events(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    job = pending(data)
    reviewer(data).review(request(job))
    events = stream(data)
    assert events.project() > 0
    page = events.read()
    approvals = [item for item in page.events if item.event == "approval.changed"]
    assert [item.data.changes.status for item in approvals] == ["PENDING", "APPROVED"]
    other = CoreEventStream(
        data["engine"], VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"])
    )
    assert [item.event for item in other.read().events] == ["approval.changed", "approval.changed"]
    assert all(
        set(item.data.model_dump(by_alias=True))
        == {"tenantId", "entityType", "entityId", "version", "changes", "at"}
        for item in page.events
    )


async def test_actual_unknown_execution_emits_safe_notification(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data, "timeout")
    ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    events = stream(data)
    assert events.project() > 0
    unknown = [item for item in events.read().events if item.event == "execution.unknown"]
    assert len(unknown) == 1 and unknown[0].data.changes.status == "UNKNOWN"
    assert unknown[0].data.entity_id == prepared.attempt.execution_id
