"""真实审批决定→持久批准事件；前置确认/平台输入明确合成，0平台写。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.infrastructure.outbox import publish_one
from yuliang.modules.execution.approval_outbox import (
    EVENT_TYPE,
    load_approved_event,
    load_approved_origin,
)
from yuliang.platform.db import TENANT_SQL, VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError
from yuliang.platform.messages import WorkMessage

from .test_approvals import pending, request, reviewer
from .test_budget_initialization import budget_ready as budget_ready
from .test_budget_settlement import ledger
from .test_capability_evidence import capability as capability
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import risk_ready as risk_ready
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


def registered(d: dict[str, Any], job_id: str) -> tuple[Any, WorkMessage]:
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        source = (
            owner.execute(
                text("SELECT * FROM approval_execution_events WHERE job_id=:job"),
                dict(job=UUID(job_id)),
            )
            .mappings()
            .one()
        )
        payload = owner.execute(
            text("SELECT payload FROM outbox_events WHERE event_id=:event"),
            dict(event=source["event_id"]),
        ).scalar_one()
    return source, WorkMessage.model_validate(payload)


def count(d: dict[str, Any]) -> tuple[int, int]:
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        source = owner.execute(
            text("SELECT count(*) FROM approval_execution_events WHERE tenant_id=:tenant"),
            dict(tenant=d["tenant"]),
        ).scalar_one()
        notice = owner.execute(
            text("SELECT count(*) FROM outbox_events WHERE tenant_id=:tenant AND event_type=:type"),
            dict(tenant=d["tenant"], type=EVENT_TYPE),
        ).scalar_one()
    return source, notice


def test_real_a_created_b_approved_keeps_original_subject_and_version(
    budget_ready: dict[str, Any],
) -> None:
    d = budget_ready
    job = pending(d)
    result = reviewer(d).review(request(job))
    row, item = registered(d, job.job_id)
    assert item.user_id == d["identity"].user_id != d["approver"]
    assert item.membership_id == d["identity"].membership_id
    assert row["decision_id"] == result.decision_id and row["approval_id"] == result.approval_id
    assert row["approved_job_version"] == result.job.version == job.version + 1
    assert count(d) == (1, 1)
    with business_transaction(d["engine"], d["identity"]) as session:
        source = load_approved_event(session, item)
    assert source.identity == d["identity"] and source.message() == item
    assert source.approved_job_version == result.job.version
    # producer并非执行器，不产生发送/平台请求或伪成功。
    with business_transaction(d["engine"], d["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM execution_attempts")).scalar_one() == 0
        assert (
            session.execute(
                text("SELECT status FROM command_jobs WHERE id=:job"), dict(job=UUID(job.job_id))
            ).scalar_one()
            == "APPROVED"
        )


def test_reject_never_emits_and_releases_same_transaction(budget_ready: dict[str, Any]) -> None:
    d = budget_ready
    job = pending(d)
    result = reviewer(d).review(request(job, "REJECT"))
    assert result.job.status == "REJECTED" and count(d) == (0, 0)
    assert ledger(d)[1] == 0


def test_old_version_replay_cannot_emit_second_event(budget_ready: dict[str, Any]) -> None:
    d = budget_ready
    job = pending(d)
    reviewer(d).review(request(job))
    first = registered(d, job.job_id)[1]
    with pytest.raises(ApiError):
        reviewer(d).review(request(job))
    assert registered(d, job.job_id)[1] == first and count(d) == (1, 1)


def test_two_reviewers_only_one_actual_decision_and_event(budget_ready: dict[str, Any]) -> None:
    d = budget_ready
    job = pending(d)
    gate = Barrier(2)

    def approve() -> bool:
        gate.wait(timeout=10)
        try:
            reviewer(d).review(request(job))
            return True
        except ApiError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve) for _ in range(2)]
        assert sorted(f.result(timeout=15) for f in futures) == [False, True]
    assert count(d) == (1, 1)


def test_audit_failure_rolls_back_approval_source_outbox_and_job(
    budget_ready: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yuliang.modules.execution.approvals as module

    d = budget_ready
    job = pending(d)
    before = ledger(d)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic audit failure")

    with monkeypatch.context() as patch:
        patch.setattr(module, "record", fail)
        with pytest.raises(RuntimeError):
            reviewer(d).review(request(job))
    assert count(d) == (0, 0) and ledger(d) == before
    assert d["applications"].get(UUID(job.job_id)).status == "PENDING_APPROVAL"
    assert reviewer(d).review(request(job)).job.status == "APPROVED"
    assert count(d) == (1, 1)


def test_reject_money_failure_cannot_leave_partial_decision(
    budget_ready: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yuliang.modules.execution.settlement import BudgetSettlement

    d = budget_ready
    job = pending(d)
    before = ledger(d)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic money failure")

    with monkeypatch.context() as patch:
        patch.setattr(BudgetSettlement, "release", fail)
        with pytest.raises(RuntimeError):
            reviewer(d).review(request(job, "REJECT"))
    assert count(d) == (0, 0) and ledger(d) == before
    assert d["applications"].get(UUID(job.job_id)).status == "PENDING_APPROVAL"


def test_core_function_and_source_table_not_runtime_bypasses(budget_ready: dict[str, Any]) -> None:
    d = budget_ready
    job = pending(d)
    with (
        pytest.raises(DBAPIError) as error,
        business_transaction(d["engine"], d["identity"]) as session,
    ):
        session.execute(
            text("""SELECT access.approval_decide_core_0037(
            NULL::uuid,NULL::uuid,NULL::uuid,1::bigint,1::bigint,'APPROVE'::text,'MANUAL'::text,
            NULL::uuid,NULL::uuid,'{}'::jsonb,NULL::uuid,NULL::uuid,NULL::text)""")
        )
    assert getattr(error.value.orig, "sqlstate", None) == "42501"
    with (
        pytest.raises(DBAPIError) as error,
        business_transaction(d["engine"], d["identity"]) as session,
    ):
        session.execute(
            text("""INSERT INTO approval_execution_events(tenant_id,event_id,decision_id,
            approval_id,job_id,approved_job_version,subject_user_id,subject_membership_id)
            VALUES(:tenant,:event,:decision,:approval,:job,1,:user,:member)"""),
            dict(
                tenant=d["tenant"],
                event=uuid4(),
                decision=uuid4(),
                approval=uuid4(),
                job=UUID(job.job_id),
                user=d["identity"].user_id,
                member=d["identity"].membership_id,
            ),
        )
    assert getattr(error.value.orig, "sqlstate", None) == "42501"
    assert count(d) == (0, 0)


def test_tenant_outbox_insert_cannot_forge_approved_origin(budget_ready: dict[str, Any]) -> None:
    d = budget_ready
    job = pending(d)
    forged = WorkMessage(
        event_id=uuid4(),
        tenant_id=d["tenant"],
        job_id=UUID(job.job_id),
        user_id=d["identity"].user_id,
        membership_id=d["identity"].membership_id,
    )
    with (
        pytest.raises(DBAPIError) as error,
        business_transaction(d["engine"], d["identity"]) as session,
    ):
        session.execute(
            text("""INSERT INTO outbox_events(event_id,tenant_id,aggregate_type,
            aggregate_id,event_type,payload) VALUES(:event,:tenant,'command_job',:job,:type,
            CAST(:payload AS jsonb))"""),
            dict(
                event=forged.event_id,
                tenant=d["tenant"],
                job=job.job_id,
                type=EVENT_TYPE,
                payload=forged.model_dump_json(),
            ),
        )
    assert getattr(error.value.orig, "sqlstate", None) == "42501" and count(d) == (0, 0)


@pytest.mark.parametrize("field", ["job_id", "user_id", "membership_id", "event_id", "tenant_id"])
def test_broker_payload_not_an_execution_identity(budget_ready: dict[str, Any], field: str) -> None:
    d = budget_ready
    job = pending(d)
    reviewer(d).review(request(job))
    _, item = registered(d, job.job_id)
    changed = item.model_copy(update={field: d["tenant"] + 1 if field == "tenant_id" else uuid4()})
    with pytest.raises(ApiError), business_transaction(d["engine"], d["identity"]) as session:
        load_approved_event(session, changed)


def test_reviewer_cannot_be_substituted_for_original_requester(
    budget_ready: dict[str, Any],
) -> None:
    d = budget_ready
    job = pending(d)
    reviewer(d).review(request(job))
    _, item = registered(d, job.job_id)
    b = VerifiedIdentity(d["tenant"], d["approver_member"], d["approver"])
    changed = item.model_copy(update=dict(user_id=b.user_id, membership_id=b.membership_id))
    with pytest.raises(ApiError), business_transaction(d["engine"], b) as session:
        load_approved_event(session, changed)


def test_removed_original_member_blocks_event_context(budget_ready: dict[str, Any]) -> None:
    d = budget_ready
    job = pending(d)
    reviewer(d).review(request(job))
    _, item = registered(d, job.job_id)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE memberships SET status='REMOVED' WHERE id=:member"),
            dict(member=d["identity"].membership_id),
        )
    with pytest.raises(ApiError), business_transaction(d["engine"], d["identity"]) as session:
        load_approved_event(session, item)
    with business_transaction(d["engine"], d["identity"]) as session:
        origin = load_approved_origin(session, item)
    assert not origin.subject_active and origin.message() == item
    # 仅允许后续窄blocked事实；来源身份信息不是当前发送资格。
    assert origin.identity == d["identity"]


def test_broker_confirmation_loss_keeps_same_persistent_event(budget_ready: dict[str, Any]) -> None:
    d = budget_ready
    job = pending(d)
    reviewer(d).review(request(job))
    _, item = registered(d, job.job_id)
    calls = []

    def unavailable(event: UUID, name: str, payload: dict[str, Any]) -> None:
        calls.append((event, name, payload))
        raise ConnectionError("synthetic MQ confirmation lost")

    assert not publish_one(d["engine"], d["identity"], item.event_id, unavailable)
    assert publish_one(
        d["engine"],
        d["identity"],
        item.event_id,
        lambda event, name, payload: calls.append((event, name, payload)),
    )
    assert len(calls) == 2 and calls[0] == calls[1]
    assert count(d) == (1, 1)


def test_origin_and_event_identity_are_immutable(budget_ready: dict[str, Any]) -> None:
    d = budget_ready
    job = pending(d)
    reviewer(d).review(request(job))
    _, item = registered(d, job.job_id)
    for statement in (
        "UPDATE approval_execution_events SET subject_user_id=:id WHERE event_id=:event",
        "UPDATE outbox_events SET payload=jsonb_set(payload,'{user_id}',"
        "to_jsonb(CAST(:id AS text))) "
        "WHERE event_id=:event",
    ):
        with pytest.raises(DBAPIError) as error, d["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(text(statement), dict(id=uuid4(), event=item.event_id))
        assert getattr(error.value.orig, "sqlstate", None) in {"22023", "42501"}
    assert registered(d, job.job_id)[1] == item


def test_historical_core_decision_is_not_backfilled_or_reapproved(
    budget_ready: dict[str, Any],
) -> None:
    d = budget_ready
    job = pending(d)
    # 仅维护角色调用保留的真实0037内核模拟迁移前事实；运行角色无此EXECUTE权限。
    with Session(d["maintenance"]) as session, session.begin():
        session.execute(text("SET LOCAL ROLE app_owner"))
        session.execute(TENANT_SQL, dict(tenant_id=str(d["tenant"]), user_id=str(d["approver"])))
        connection = session.connection()

        def old_core(
            conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
        ) -> tuple[str, Any]:
            return statement.replace(
                "access.approval_decide(", "access.approval_decide_core_0037("
            ), parameters

        event.listen(connection, "before_cursor_execute", old_core, retval=True)
        historical = reviewer(d).review(request(job), unit_of_work=session)
        event.remove(connection, "before_cursor_execute", old_core)
    assert historical.job.status == "APPROVED" and count(d) == (0, 0)
    with pytest.raises(ApiError):
        reviewer(d).review(request(job))
    assert count(d) == (0, 0)
