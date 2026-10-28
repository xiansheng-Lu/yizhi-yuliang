"""真实PG资金终结；规范E2由实际留档+维护校验导入，供应商是显式Mock。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.modules.command.applications import JobApplications
from yuliang.modules.command.state import JobEvent, StateFacts, evolve
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.execution.approval_models import ReviewJobRequest
from yuliang.modules.execution.approvals import Approvals
from yuliang.modules.execution.attempt_models import BusinessKind, BusinessRef
from yuliang.modules.execution.attempts import ExecutionOrders, PreparedExecution
from yuliang.modules.execution.guards import TargetGuards
from yuliang.modules.execution.receipts import ExecutionReceipts
from yuliang.modules.execution.settlement import BudgetSettlement
from yuliang.modules.policy.action_facts import record_verified_action
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_budget_initialization import budget_ready as budget_ready
from .test_budget_initialization import full_snapshot
from .test_budget_reservations import hold
from .test_capability_evidence import capability as capability
from .test_execution_attempts import SyntheticBudgetSerializer, send
from .test_execution_receipts import observation
from .test_identity_database import database as database
from .test_intent_binding import stage_only
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_platform_payloads import decoder, signer
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import risk_ready as risk_ready
from .test_risk_rules import CanonicalRead, application
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


def ledger(data: dict[str, Any]) -> tuple[int, int, str | None]:
    with business_transaction(data["engine"], data["identity"]) as session:
        row = session.execute(
            text("""SELECT confirmed_total_minor,reserved_increase_minor,freeze_reason
        FROM budget_ledgers WHERE bucket_key='TENANT_DAILY'""")
        ).one()
        return row[0], row[1], row[2]


async def matched_input(data: dict[str, Any], amount: int = 110) -> tuple[PreparedExecution, UUID]:
    if amount == 110:
        prepared, _, raw = await observation(data)
    else:
        data["budget_service"].refresh(1)
        draft = application(data, amount)
        hold(data, draft)
        job = stage_only(data, stage_only(data, draft, JobEvent.CONFIRM), JobEvent.APPROVE)
        orders = ExecutionOrders(data["applications"], SyntheticBudgetSerializer(data["spec"]))
        prepared = orders.prepare(
            BusinessRef(kind=BusinessKind.COMMAND_JOB, id=UUID(job.job_id)),
            data["ctx"],
            job.version,
            0,
            uuid4(),
        )
        await send(data, orders, prepared)
        with business_transaction(data["engine"], data["identity"]) as session:
            raw = session.execute(
                text(
                    "SELECT id FROM platform_raw_payloads WHERE outbound_id=:id "
                    "AND phase='RESPONSE'"
                ),
                {"id": prepared.attempt.outbound_id},
            ).scalar_one()
    ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    return prepared, await independent_match(data, prepared, amount)


async def independent_match(data: dict[str, Any], prepared: PreparedExecution, amount: int) -> UUID:
    """仅测试技术输入：独立规范回读和维护验证，不冒充M2-17运行生产者。"""
    read = uuid4()
    safe = ArchivePolicy(ArchivePolicy().fields | {"source_updated_at"})
    client = RecordedPlatformClient(
        PlatformHttpClient(
            signer,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "id": "456",
                            "budget_minor": amount,
                            "currency": "CNY",
                            "source_updated_at": datetime.now(UTC).isoformat(),
                        },
                    },
                )
            ),
        ),
        data["archive"],
        policy=safe,
        decoder=decoder,
    )
    await client.send(
        data["readctx"],
        replace(data["readspec"], parameters_model=CanonicalRead, cursor_parameter=None),
        read,
        {"account_id": data["readctx"].native_account_id, "object_id": "456"},
        SecretStr("synthetic"),
        target=data["target"],
        execution_id=prepared.attempt.execution_id,
        attempt_id=prepared.attempt.attempt_id,
    )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        fact = record_verified_action(
            owner,
            prepared.attempt.business.id,
            prepared.attempt.outbound_id,
            read,
            "synthetic/M2-10c/independent-match",
        )
    return fact


@pytest.mark.parametrize("already_in_full_snapshot", [False, True])
async def test_match_atomic_settlement_without_double_counting(
    budget_ready: dict[str, Any], already_in_full_snapshot: bool
) -> None:
    data = budget_ready
    prepared, fact = await matched_input(data)
    if already_in_full_snapshot:
        await full_snapshot(data, active=110)
        data["budget_service"].refresh(1)
        assert ledger(data) == (10000, 10, "OVER_LIMIT")
    else:
        assert ledger(data) == (9990, 10, None)
    service = BudgetSettlement(data["applications"])
    result = service.match(fact, 5)
    assert ledger(data) == (10000, 0, None)
    assert service.match(fact, 1) == result
    job = data["applications"].get(prepared.attempt.business.id)
    assert job.status.value == "SUCCEEDED" and job.version == 6
    assert job.execution_receipt.verification == "MATCHED"
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(text("SELECT state FROM budget_reservations")).scalar_one() == "SETTLED"
        )
        assert (
            TargetGuards(data["applications"]).describe(session, prepared.attempt.business.id)[
                "state"
            ]
            == "IDLE"
        )
        delta = session.execute(text("SELECT budget_delta FROM budget_adjustments")).scalar_one()
        assert delta == (0 if already_in_full_snapshot else 10)
    if not already_in_full_snapshot and (evidence := os.getenv("YULIANG_SETTLEMENT_EVIDENCE")):
        Path(evidence).write_text(
            json.dumps(
                {
                    **result.model_dump(mode="json"),
                    "B": 10000,
                    "R": 0,
                    "platformCalls": 0,
                    "paidCalls": 0,
                    "proof": "MAINTENANCE_VALIDATED_MOCK_ARCHIVE_E2_REAL_PG_KERNEL",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


async def test_concurrent_match_only_one_settlement(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    _, fact = await matched_input(data)
    service = BudgetSettlement(data["applications"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: service.match(fact, 5), range(4)))
    assert all(value == values[0] for value in values)
    assert ledger(data) == (10000, 0, None)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM budget_adjustments")).scalar_one() == 1


async def test_decrease_after_independent_match_without_negative_reservation(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    prepared, fact = await matched_input(data, 90)
    assert ledger(data) == (9990, 0, None)
    BudgetSettlement(data["applications"]).match(fact, 5)
    assert ledger(data) == (9980, 0, None)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM budget_reservations")).scalar_one() == 0
    assert data["applications"].get(prepared.attempt.business.id).status.value == "SUCCEEDED"


async def test_e1_rejection_releases_once(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data, "rejected")
    receipt = ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    service = BudgetSettlement(data["applications"])
    first = service.release(prepared.attempt.business.id, receipt.receipt_id)
    assert service.release(prepared.attempt.business.id, receipt.receipt_id) == first
    assert ledger(data) == (9990, 0, None)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(text("SELECT state FROM budget_reservations")).scalar_one()
            == "RELEASED"
        )
        assert (
            TargetGuards(data["applications"]).describe(session, prepared.attempt.business.id)[
                "state"
            ]
            == "IDLE"
        )


@pytest.mark.parametrize("mode", ["timeout", "unconfirmed", "accepted"])
async def test_unknown_or_acceptance_does_not_release_held(
    budget_ready: dict[str, Any], mode: str
) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data, mode)
    receipt = ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    for proof in (None, receipt.receipt_id):
        with pytest.raises(ApiError):
            BudgetSettlement(data["applications"]).release(prepared.attempt.business.id, proof)
    assert ledger(data)[:2] == (9990, 10)


@pytest.mark.parametrize("event", [JobEvent.CANCEL, JobEvent.REJECT, JobEvent.EXPIRE])
def test_terminal_kernel_and_release_same_transaction(
    budget_ready: dict[str, Any], event: JobEvent
) -> None:
    data = budget_ready
    data["budget_service"].refresh(1)
    job = application(data)
    hold(data, job)
    if event == JobEvent.EXPIRE:
        # 仅推进初始时钟，不假称M2-07过期扫描已经接通。
        job = job.model_copy(
            update={
                "created_at": job.created_at - timedelta(minutes=30),
                "expires_at": job.created_at - timedelta(seconds=1),
            }
        )
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(
                text("""UPDATE command_jobs SET created_at=:created,expires_at=:expires,
            document=CAST(:document AS jsonb) WHERE id=:id"""),
                {
                    "created": job.created_at,
                    "expires": job.expires_at,
                    "document": job.model_dump_json(),
                    "id": job.job_id,
                },
            )
    apps = data["applications"]
    if event == JobEvent.REJECT:
        job = stage_only(data, job, JobEvent.CONFIRM)
        Approvals(apps).open(UUID(job.job_id), job.version)
        apps = JobApplications(
            data["engine"],
            VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"]),
            uuid4(),
            apps.key,
        )
    with business_transaction(data["engine"], apps.identity) as session:
        if event == JobEvent.REJECT:
            # M2-14实际审批门已上线，驳回必须留决定，并在同一Session释放预占。
            Approvals(apps).review(
                ReviewJobRequest(
                    job_id=UUID(job.job_id),
                    expected_version=job.version,
                    decision="REJECT",
                ),
                unit_of_work=session,
            )
        else:
            at = session.execute(text("SELECT clock_timestamp()")).scalar_one()
            after = evolve(
                job,
                event,
                StateFacts(),
                at=at,
                actor_id=apps.identity.user_id,
                requester_id=data["user"],
            )
            session.execute(
                text("""SELECT access.job_apply_transition(:member,:user,:job,:version,
            :event,CAST(:document AS jsonb),:request,:proof)"""),
                {
                    "member": apps.identity.membership_id,
                    "user": apps.identity.user_id,
                    "job": job.job_id,
                    "version": job.version,
                    "event": event.value,
                    "document": after.model_dump_json(),
                    "request": apps.request_id,
                    "proof": "a" * 64,
                },
            )
            BudgetSettlement(apps).release(UUID(job.job_id), unit_of_work=session)
    assert ledger(data)[:2] == (9990, 0)
    assert BudgetSettlement(apps).release(UUID(job.job_id)).proof_kind == "UNCLAIMED_TERMINAL"


async def test_settlement_audit_failure_rolls_back_money_job_and_guard(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = budget_ready
    prepared, fact = await matched_input(data)

    def failed(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic settlement audit failure")

    monkeypatch.setattr("yuliang.modules.execution.settlement.record", failed)
    with pytest.raises(RuntimeError, match="synthetic settlement audit failure"):
        BudgetSettlement(data["applications"]).match(fact, 5)
    assert ledger(data)[:2] == (9990, 10)
    assert data["applications"].get(prepared.attempt.business.id).status.value == "VERIFYING"
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM budget_adjustments")).scalar_one() == 0


@pytest.mark.parametrize("fault", ["fence", "version", "newer_snapshot", "drift"])
async def test_stale_match_cannot_change_money(budget_ready: dict[str, Any], fault: str) -> None:
    data = budget_ready
    _, fact = await matched_input(data)
    if fault in {"newer_snapshot", "drift"}:
        await full_snapshot(data, active=115 if fault == "drift" else 100)
        data["budget_service"].refresh(1)
    elif fault == "fence":
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(text("UPDATE target_execution_guards SET fence=fence+1"))
    before = ledger(data)
    with pytest.raises(ApiError):
        BudgetSettlement(data["applications"]).match(fact, 999 if fault == "version" else 5)
    assert ledger(data) == before


async def test_settled_job_cannot_be_released_with_a_new_proof(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    prepared, fact = await matched_input(data)
    service = BudgetSettlement(data["applications"])
    service.match(fact, 5)
    with pytest.raises(ApiError) as raised:
        service.release(prepared.attempt.business.id)
    assert raised.value.code == ErrorCode.IDEMPOTENCY_MISMATCH
    assert ledger(data)[:2] == (10000, 0)


async def test_old_fence_e1_cannot_release_current_held(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data, "rejected")
    receipt = ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(text("UPDATE target_execution_guards SET fence=fence+1"))
    with pytest.raises(ApiError) as raised:
        BudgetSettlement(data["applications"]).release(
            prepared.attempt.business.id, receipt.receipt_id
        )
    assert raised.value.code == ErrorCode.VERSION_CONFLICT
    assert ledger(data)[:2] == (9990, 10)


async def test_failed_string_without_e1_does_not_release(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data, "timeout")
    receipt = ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    # 故障注入FAILED标签，明确不能当平台未执行证明。
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""UPDATE command_jobs SET status='FAILED',
        document=jsonb_set(jsonb_set(document,'{status}','"FAILED"'::jsonb),
                          '{history,-1,status}','"FAILED"'::jsonb)""")
        )
    for proof in (None, receipt.receipt_id):
        with pytest.raises(ApiError):
            BudgetSettlement(data["applications"]).release(prepared.attempt.business.id, proof)
    assert ledger(data)[:2] == (9990, 10)


def test_release_audit_failure_rolls_back_terminal_and_held(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = budget_ready
    data["budget_service"].refresh(1)
    job = application(data)
    hold(data, job)

    def failed(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic release audit failure")

    monkeypatch.setattr("yuliang.modules.execution.settlement.record", failed)
    with (
        pytest.raises(RuntimeError, match="synthetic release audit failure"),
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        at = session.execute(text("SELECT clock_timestamp()")).scalar_one()
        after = evolve(
            job,
            JobEvent.CANCEL,
            StateFacts(),
            at=at,
            actor_id=data["user"],
            requester_id=data["user"],
        )
        session.execute(
            text("""SELECT access.job_apply_transition(:member,:user,:job,:version,
        'CANCEL',CAST(:document AS jsonb),:request,:proof)"""),
            {
                "member": data["identity"].membership_id,
                "user": data["user"],
                "job": job.job_id,
                "version": job.version,
                "document": after.model_dump_json(),
                "request": uuid4(),
                "proof": "a" * 64,
            },
        )
        BudgetSettlement(data["applications"]).release(UUID(job.job_id), unit_of_work=session)
    assert ledger(data)[:2] == (9990, 10)
    assert data["applications"].get(UUID(job.job_id)).status.value == "DRAFT"


@pytest.mark.parametrize("corruption", ["aggregate", "missing_detail"])
async def test_inconsistent_held_journal_cannot_be_marked_released(
    budget_ready: dict[str, Any], corruption: str
) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data, "rejected")
    receipt = ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if corruption == "aggregate":
            owner.execute(text("UPDATE budget_ledgers SET reserved_increase_minor=11"))
        else:
            owner.execute(text("DELETE FROM budget_reservations"))
    before = ledger(data)
    with pytest.raises(ApiError):
        BudgetSettlement(data["applications"]).release(
            prepared.attempt.business.id, receipt.receipt_id
        )
    assert ledger(data) == before
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT state FROM job_budget_holds")).scalar_one() == "HELD"
        assert (
            session.execute(text("SELECT count(*) FROM budget_job_finalizations")).scalar_one() == 0
        )
