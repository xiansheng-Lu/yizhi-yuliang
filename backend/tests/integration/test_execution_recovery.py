"""真实PG恢复线性点与资金；供应商明确Mock，未以测试代替真实平台验收。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.connectors.archive import ArchiveUnavailable, RecordedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.outcomes import Outcome
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.execution.attempt_models import BusinessKind, BusinessRef
from yuliang.modules.execution.attempts import ExecutionOrders
from yuliang.modules.execution.guards import TargetGuards
from yuliang.modules.execution.recovery import ExecutionRecovery, RecoveryDeferred
from yuliang.modules.execution.recovery_schedule import publish_schedule
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_budget_initialization import budget_ready as budget_ready
from .test_budget_settlement import ledger
from .test_capability_evidence import capability as capability
from .test_execution_attempts import SyntheticBudgetSerializer, prepare
from .test_execution_receipts import observation
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_platform_payloads import decoder, signer
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import risk_ready as risk_ready
from .test_sync_checkpoints import sync_ready as sync_ready
from .test_target_guards import approved

pytestmark = pytest.mark.integration


def event(data: dict[str, Any], attempt: UUID) -> UUID:
    with business_transaction(data["engine"], data["identity"]) as session:
        return UUID(
            str(
                session.execute(
                    text("SELECT current_event_id FROM execution_recoveries WHERE attempt_id=:id"),
                    {"id": attempt},
                ).scalar_one()
            )
        )


def expire(data: dict[str, Any]) -> None:
    """仅将租约初始时钟提前，实际服务器到期谓词与锁真实运行。"""
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE target_execution_guards "
                "SET lease_until=clock_timestamp()-interval '1 second'"
            )
        )
        owner.execute(
            text("UPDATE execution_recoveries SET next_due=clock_timestamp()-interval '1 second'")
        )
        owner.execute(
            text(
                "UPDATE execution_recovery_events SET due_at=clock_timestamp()-interval '1 second'"
            )
        )
        owner.execute(
            text(
                "UPDATE outbox_events SET not_before=clock_timestamp()-interval '1 second' "
                "WHERE event_type='yuliang.system.execution_recovery'"
            )
        )
        owner.execute(
            text(
                "UPDATE scheduled_tasks SET due_at=clock_timestamp()-interval '1 second' "
                "WHERE kind='execution.recover'"
            )
        )


@pytest.mark.parametrize("with_root", [False, True])
def test_sent_registration_and_not_sent_e1_atomic_finalization(
    budget_ready: dict[str, Any], with_root: bool
) -> None:
    data = budget_ready
    orders, prepared, job = prepare(data)
    ticket = prepared.attempt
    current = event(data, ticket.attempt_id)
    assert prepared.spec is not None and prepared.parameters is not None
    if with_root:
        data["archive"].begin(
            data["ctx"],
            prepared.spec,
            ticket.outbound_id,
            prepared.parameters,
            ArchivePolicy(),
            data["target"],
            ticket.execution_id,
            ticket.attempt_id,
            before_verify=orders.before_verify(ticket),
            after_admission=orders.after_admission(
                ticket, data["ctx"], prepared.spec, prepared.parameters
            ),
        )
    service = ExecutionRecovery(data["applications"])
    with pytest.raises(RecoveryDeferred):
        service.inspect(current)
    assert ledger(data)[:2] == (9990, 10)
    expire(data)
    result = service.inspect(current)
    assert result.state == "DONE" and result.outcome == "NOT_SENT" and result.evidence_level == "E1"
    assert result.network_attempts == 0 and result.job_status == "FAILED"
    assert service.inspect(current) == result and ledger(data)[:2] == (9990, 0)
    after = data["applications"].get(UUID(job.job_id))
    assert (
        after.execution_receipt is None
        and after.error.code == "NOT_SENT"
        and not after.error.retryable
    )
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            TargetGuards(data["applications"]).describe(session, UUID(job.job_id))["state"]
            == "IDLE"
        )
        claimed = session.execute(
            text("SELECT claimed_at,dispatch_started_at FROM command_jobs WHERE id=:id"),
            {"id": job.job_id},
        ).one()
        assert all(value is not None for value in claimed)
        assert (
            session.execute(text("SELECT state FROM job_budget_holds")).scalar_one() == "RELEASED"
        )
        assert (
            session.execute(text("SELECT count(*) FROM execution_recovery_evidence")).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text("SELECT state FROM scheduled_tasks WHERE kind='execution.recover'")
            ).scalar_one()
            == "DONE"
        )


@pytest.mark.parametrize("mode", ["accepted", "rejected", "timeout"])
async def test_network_evidence_never_becomes_missing_send(
    budget_ready: dict[str, Any], mode: str
) -> None:
    data = budget_ready
    prepared, _, _ = await observation(data, mode)
    current = event(data, prepared.attempt.attempt_id)
    expire(data)
    result = ExecutionRecovery(data["applications"]).inspect(current)
    assert result.evidence_level == "E0" and result.network_attempts == 1
    if mode == "rejected":
        assert result.state == "DONE" and result.job_status == "FAILED"
        assert ledger(data)[:2] == (9990, 0)
    else:
        assert result.state == "WAITING" and result.next_event_id is not None
        assert result.next_event_id != current and ledger(data)[:2] == (9990, 10)
        with business_transaction(data["engine"], data["identity"]) as session:
            assert (
                session.execute(text("SELECT state FROM target_execution_guards")).scalar_one()
                == "QUARANTINED"
            )
        if mode == "accepted":
            assert result.job_status == "VERIFYING"
        else:
            assert result.job_status == "EXECUTING"


def test_no_attempt_but_unknown_error_is_not_not_sent(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    orders, prepared, _ = prepare(data)
    ticket = prepared.attempt
    assert prepared.spec is not None and prepared.parameters is not None
    data["archive"].begin(
        data["ctx"],
        prepared.spec,
        ticket.outbound_id,
        prepared.parameters,
        ArchivePolicy(),
        data["target"],
        ticket.execution_id,
        ticket.attempt_id,
        before_verify=orders.before_verify(ticket),
        after_admission=orders.after_admission(
            ticket, data["ctx"], prepared.spec, prepared.parameters
        ),
    )
    data["archive"].append(
        data["ctx"],
        prepared.spec,
        ticket.outbound_id,
        0,
        "ERROR",
        "UNKNOWN",
        outcome=Outcome("UPSTREAM_ERROR", "UNKNOWN"),
    )
    expire(data)
    result = ExecutionRecovery(data["applications"]).inspect(event(data, ticket.attempt_id))
    assert (
        result.evidence_level == "E0" and result.state == "WAITING" and result.network_attempts == 0
    )
    assert ledger(data)[:2] == (9990, 10)


def test_old_sender_after_root_commit_cannot_resume_after_recovery_and_new_claim(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = budget_ready
    orders, prepared, _ = prepare(data)
    ticket = prepared.attempt
    spec, parameters = prepared.spec, prepared.parameters
    assert spec is not None and parameters is not None
    arrived, proceed = Event(), Event()
    original = data["archive"].append
    calls: list[str] = []

    def paused(*args: Any, **kwargs: Any) -> Any:
        if args[2] == ticket.outbound_id and args[4] == "ATTEMPT":
            arrived.set()
            assert proceed.wait(20)
        return original(*args, **kwargs)

    monkeypatch.setattr(data["archive"], "append", paused)

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append("HTTP")
        return httpx.Response(200, json={"code": 0})

    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(upstream)),
        data["archive"],
        decoder=decoder,
    )

    def old_sender() -> Any:
        return asyncio.run(
            client.send(
                data["ctx"],
                spec,
                ticket.outbound_id,
                parameters,
                SecretStr("synthetic"),
                target=data["target"],
                execution_id=ticket.execution_id,
                attempt_id=ticket.attempt_id,
                before_verify=orders.before_verify(ticket),
                after_admission=orders.after_admission(ticket, data["ctx"], spec, parameters),
            )
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(old_sender)
        try:
            assert arrived.wait(10)
            expire(data)
            result = ExecutionRecovery(data["applications"]).inspect(event(data, ticket.attempt_id))
            assert result.outcome == "NOT_SENT" and ledger(data)[:2] == (9990, 0)
            # 原Root由恢复E1排除在途后，真实风险/HELD/守卫可为新申请领取fence=2。
            fresh = approved(data)
            next_order = ExecutionOrders(
                data["applications"], SyntheticBudgetSerializer(data["spec"])
            )
            second = next_order.prepare(
                BusinessRef(kind=BusinessKind.COMMAND_JOB, id=UUID(fresh.job_id)),
                data["ctx"],
                fresh.version,
                1,
                uuid4(),
            )
            assert second.attempt.guard.fence == 2
        finally:
            proceed.set()
        with pytest.raises(ArchiveUnavailable):
            pending.result(timeout=10)
    assert calls == [] and ledger(data)[:2] == (9990, 10)


def test_recovery_audit_failure_rolls_back_proof_money_and_guard(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = budget_ready
    _, prepared, _ = prepare(data)
    expire(data)

    def failed(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic recovery audit failure")

    monkeypatch.setattr("yuliang.modules.execution.recovery.record", failed)
    with pytest.raises(RuntimeError, match="synthetic recovery audit failure"):
        ExecutionRecovery(data["applications"]).inspect(event(data, prepared.attempt.attempt_id))
    assert ledger(data)[:2] == (9990, 10)
    assert data["applications"].get(prepared.attempt.business.id).status.value == "EXECUTING"
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(text("SELECT count(*) FROM execution_recovery_evidence")).scalar_one()
            == 0
        )
        assert (
            session.execute(text("SELECT state FROM target_execution_guards")).scalar_one()
            == "LEASED"
        )
        assert session.execute(text("SELECT state FROM execution_recoveries")).scalar_one() == "DUE"


def test_permission_revoke_is_visible_blocked_then_explicit_resume(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    _, prepared, _ = prepare(data)
    current = event(data, prepared.attempt.attempt_id)
    expire(data)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE data_scopes SET permissions_mask=ARRAY['overview.read'] "
                "WHERE membership_id=:id"
            ),
            {"id": data["identity"].membership_id},
        )
    service = ExecutionRecovery(data["applications"])
    with pytest.raises(ApiError) as error:
        service.inspect(current)
    assert error.value.code == ErrorCode.FORBIDDEN
    blocked = service.blocked(current, "AUTH_REVOKED")
    assert blocked.state == "BLOCKED_AUTH" and blocked.evidence_level == "E0"
    assert ledger(data)[:2] == (9990, 10)
    from yuliang.modules.command.applications import JobApplications

    manager = ExecutionRecovery(
        JobApplications(data["engine"], data["budget_identity"], uuid4(), data["applications"].key)
    )
    assert manager.manager_summary()["items"][0]["state"] == "BLOCKED_AUTH"
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE data_scopes SET permissions_mask=NULL WHERE membership_id=:id"),
            {"id": data["identity"].membership_id},
        )
    resumed = service.resume(current)
    assert resumed.next_event_id != current and resumed.next_event_id is not None
    assert service.inspect(resumed.next_event_id).outcome == "NOT_SENT"
    assert ledger(data)[:2] == (9990, 0)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(text("SELECT count(*) FROM execution_recovery_evidence")).scalar_one()
            == 2
        )


def test_publish_schedule_ack_loss_preserves_pending_fact(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    _, prepared, _ = prepare(data)
    current = event(data, prepared.attempt.attempt_id)
    sent: list[str] = []

    def failed(identity: UUID, name: str, payload: dict[str, Any], due: Any) -> None:
        # 另一连接已看到发布租约，证明发布前PG事务已提交；没有业务HTTP。
        with business_transaction(data["engine"], data["identity"]) as session:
            assert session.execute(
                text("SELECT lease_owner IS NOT NULL FROM outbox_events WHERE event_id=:id"),
                {"id": current},
            ).scalar_one()
        sent.append(name)
        raise RuntimeError("synthetic lost broker confirmation")

    assert not publish_schedule(data["engine"], data["identity"], current, failed)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(
            text("SELECT state,last_error_code FROM outbox_events WHERE event_id=:id"),
            {"id": current},
        ).one() == ("PENDING", "MQ_UNAVAILABLE")
    assert sent == ["yuliang.system.execution_recovery"] and ledger(data)[:2] == (9990, 10)


def test_tracked_outbound_cannot_omit_execution_links(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    _, prepared, _ = prepare(data)
    assert prepared.spec is not None and prepared.parameters is not None
    with pytest.raises(DBAPIError):
        data["archive"].begin(
            data["ctx"],
            prepared.spec,
            prepared.attempt.outbound_id,
            prepared.parameters,
            ArchivePolicy(),
            data["target"],
            None,
            None,
        )
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM platform_requests WHERE id=:id"),
                {"id": prepared.attempt.outbound_id},
            ).scalar_one()
            == 0
        )
    expire(data)
    assert (
        ExecutionRecovery(data["applications"])
        .inspect(event(data, prepared.attempt.attempt_id))
        .outcome
        == "NOT_SENT"
    )


async def test_unknown_is_bounded_and_proofs_are_runtime_immutable(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    prepared, _, _ = await observation(data, "timeout")
    current = event(data, prepared.attempt.attempt_id)
    service = ExecutionRecovery(data["applications"])
    for check in range(8):
        expire(data)
        result = service.inspect(current)
        assert result.network_attempts == 1 and result.evidence_level == "E0"
        if check < 7:
            assert result.state == "WAITING" and result.next_event_id is not None
            current = result.next_event_id
    assert result.state == "MANUAL_REQUIRED" and result.outcome == "RECONCILIATION_UNRESOLVED"
    assert ledger(data)[:2] == (9990, 10)
    assert data["applications"].get(prepared.attempt.business.id).status.value == "EXECUTING"
    assert service.inspect(current) == result
    for statement in (
        "UPDATE execution_recovery_evidence SET evidence_level='E1'",
        "DELETE FROM execution_recovery_evidence",
        "UPDATE execution_recoveries SET state='DONE'",
    ):
        with (
            pytest.raises(DBAPIError),
            business_transaction(data["engine"], data["identity"]) as session,
        ):
            session.execute(text(statement))


async def test_terminal_cleanup_does_not_quarantine_a_new_owner(
    budget_ready: dict[str, Any],
) -> None:
    from yuliang.modules.execution.receipts import ExecutionReceipts
    from yuliang.modules.execution.settlement import BudgetSettlement

    data = budget_ready
    prepared, _, raw = await observation(data, "rejected")
    service = ExecutionRecovery(data["applications"])
    receipt = ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    BudgetSettlement(data["applications"]).release(prepared.attempt.business.id, receipt.receipt_id)
    fresh = approved(data)
    next_order = ExecutionOrders(data["applications"], SyntheticBudgetSerializer(data["spec"]))
    second = next_order.prepare(
        BusinessRef(kind=BusinessKind.COMMAND_JOB, id=UUID(fresh.job_id)),
        data["ctx"],
        fresh.version,
        1,
        uuid4(),
    )
    assert second.attempt.guard.fence == 2
    assert service.inspect(event(data, prepared.attempt.attempt_id)).state == "DONE"
    with business_transaction(data["engine"], data["identity"]) as session:
        guard = TargetGuards(data["applications"]).describe(session, UUID(fresh.job_id))
        assert guard["state"] == "LEASED" and guard["fence"] == 2
    assert ledger(data)[:2] == (9990, 10)
