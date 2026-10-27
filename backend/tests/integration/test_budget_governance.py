"""真实PG外部预算漂移/告警/证据解冻；上游与初始审批明确合成。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.execution.attempt_models import BusinessKind, BusinessRef
from yuliang.modules.execution.attempts import ExecutionOrders
from yuliang.modules.execution.budget_governance import BudgetGovernance, UnsentBudgetRejection
from yuliang.modules.execution.receipts import ExecutionReceipts
from yuliang.modules.execution.settlement import BudgetSettlement
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_budget_initialization import budget_ready as budget_ready
from .test_budget_initialization import full_snapshot
from .test_budget_settlement import independent_match, ledger, matched_input
from .test_capability_evidence import capability as capability
from .test_execution_attempts import SyntheticBudgetSerializer
from .test_execution_receipts import observation
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import risk_ready as risk_ready
from .test_risk_rules import application
from .test_sync_checkpoints import sync_ready as sync_ready
from .test_target_guards import approved

pytestmark = pytest.mark.integration


def governor(data: dict[str, Any]) -> BudgetGovernance:
    return BudgetGovernance(data["engine"], data["budget_identity"], uuid4())


def summary(data: dict[str, Any]) -> dict[str, Any]:
    with business_transaction(data["engine"], data["identity"]) as session:
        identity = session.execute(text("SELECT id FROM budget_ledgers")).scalar_one()
    return governor(data).summary(identity)


async def test_external_over_limit_truth_preserves_r_and_blocks_new_and_approved(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    job = approved(data)
    await full_snapshot(data, paused=10000)
    result = data["budget_service"].refresh(1)
    assert result["snapshotApplied"] and ledger(data) == (10100, 10, "OVER_LIMIT")
    view = summary(data)
    assert any(row["kind"] == "OVER_LIMIT" and row["state"] == "OPEN" for row in view["alerts"])
    assert any(row["classification"] == "EXTERNAL_CHANGE" for row in view["discrepancies"])
    blocked = application(data)
    assert blocked.status.value == "BLOCKED"
    with pytest.raises(ApiError) as denied:
        ExecutionOrders(data["applications"], SyntheticBudgetSerializer(data["spec"])).prepare(
            BusinessRef(kind=BusinessKind.COMMAND_JOB, id=UUID(job.job_id)),
            data["ctx"],
            job.version,
            0,
            uuid4(),
        )
    assert denied.value.code == ErrorCode.RISK_BLOCKED
    assert data["applications"].get(UUID(job.job_id)).status.value == "APPROVED"
    assert ledger(data)[:2] == (10100, 10)


async def test_inflight_mismatch_is_separate_fact_not_release(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data)
    ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    await full_snapshot(data, active=115)
    result = data["budget_service"].refresh(1)
    assert result["needsReconciliation"] and ledger(data) == (10005, 10, "RECONCILIATION")
    view = summary(data)
    row = next(
        item for item in view["discrepancies"] if item["classification"] == "INFLIGHT_MISMATCH"
    )
    assert row["observedMinor"] == 115 and row["state"] == "OPEN"
    assert "related_job_id" not in str(view) and "intent" not in str(view)
    with pytest.raises(ApiError):
        BudgetSettlement(data["applications"]).release(prepared.attempt.business.id)
    with pytest.raises(ApiError):
        governor(data).resolve(UUID(row["id"]), uuid4())
    assert ledger(data)[:2] == (10005, 10)
    assert data["applications"].get(prepared.attempt.business.id).status.value == "VERIFYING"
    reducing = application(data, 90)
    assert next(c for c in reducing.risk_summary.checks if c.code == "AMOUNT_LIMIT").passed


@pytest.mark.parametrize("missing", [False, True])
async def test_older_source_cannot_erase_verified_component(
    budget_ready: dict[str, Any], missing: bool
) -> None:
    data = budget_ready
    _, fact = await matched_input(data)
    finalization = BudgetSettlement(data["applications"]).match(fact, 5)
    await full_snapshot(data, empty=missing, source_at=datetime.now(UTC) - timedelta(seconds=5))
    rejected = data["budget_service"].refresh(1)
    assert not rejected["snapshotApplied"] and rejected["needsReconciliation"]
    assert ledger(data) == (10000, 0, "RECONCILIATION")
    view = summary(data)
    item = next(row for row in view["discrepancies"] if row["classification"] == "STALE_SOURCE")
    assert any(row["kind"] == "STALE_SOURCE" and row["state"] == "OPEN" for row in view["alerts"])
    with pytest.raises(ApiError):
        governor(data).resolve(UUID(item["id"]), finalization.finalization_id)
    # 新完整来源重新证明110，仍由明确E2终结证明定性后解冻。
    await full_snapshot(data, active=110)
    data["budget_service"].refresh(1)
    assert ledger(data)[2] == "RECONCILIATION"
    resolved = governor(data).resolve(UUID(item["id"]), finalization.finalization_id)
    assert resolved["resolved"] and not resolved["replayed"]
    assert governor(data).resolve(UUID(item["id"]), finalization.finalization_id)["replayed"]
    assert ledger(data) == (10000, 0, None)
    assert all(row["state"] == "RESOLVED" for row in summary(data)["alerts"])


async def test_repeat_snapshot_does_not_duplicate_differences_or_alerts(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    data["budget_service"].refresh(1)
    await full_snapshot(data, paused=10000)
    first = data["budget_service"].refresh(1)
    before = summary(data)
    second = data["budget_service"].refresh(1)
    after = summary(data)
    assert first["observationId"] == second["observationId"] and second["replayed"]
    assert before["discrepancies"] == after["discrepancies"] and before["alerts"] == after["alerts"]


async def test_governance_audit_failure_rolls_back_snapshot_and_difference(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = budget_ready
    data["budget_service"].refresh(1)
    await full_snapshot(data, paused=10000)
    before = ledger(data)

    def failed(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic governance audit failure")

    monkeypatch.setattr("yuliang.modules.policy.budget.record", failed)
    with pytest.raises(RuntimeError, match="synthetic governance audit failure"):
        data["budget_service"].refresh(1)
    assert ledger(data) == before
    assert summary(data)["discrepancyCount"] == 0


async def test_exact_manager_scope_and_private_tables(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    initialized = data["budget_service"].refresh(1)
    identity = UUID(str(initialized["ledgerId"]))
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE data_scopes SET permissions_mask=ARRAY['task.create'] "
                "WHERE membership_id=:id"
            ),
            {"id": data["identity"].membership_id},
        )
    with pytest.raises(ApiError) as denied:
        BudgetGovernance(data["engine"], data["identity"], uuid4()).summary(identity)
    assert denied.value.code == ErrorCode.FORBIDDEN
    with (
        pytest.raises(DBAPIError),
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(text("SELECT * FROM budget_discrepancies"))
    with (
        pytest.raises(DBAPIError),
        business_transaction(data["engine"], data["budget_identity"]) as session,
    ):
        session.execute(
            text("SELECT access.budget_initialize_snapshot(:member,:user,:request,1)"),
            {
                "member": data["budget_identity"].membership_id,
                "user": data["budget_identity"].user_id,
                "request": uuid4(),
            },
        )


async def test_unclaimed_approved_increase_rejected_and_released_atomically(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    job = approved(data)
    await full_snapshot(data, paused=10000)
    data["budget_service"].refresh(1)
    service = UnsentBudgetRejection(data["applications"])
    result = service.reject(UUID(job.job_id), job.version)
    assert result.kind == "RELEASE" and result.proof_kind == "UNCLAIMED_TERMINAL"
    assert service.reject(UUID(job.job_id), job.version) == result
    after = data["applications"].get(UUID(job.job_id))
    assert after.status.value == "FAILED" and after.version == 4
    assert after.execution_receipt is None and after.error.code == "RISK_BLOCKED"
    assert not after.error.retryable and ledger(data) == (10100, 0, "OVER_LIMIT")
    if evidence := os.getenv("YULIANG_BUDGET_GOVERNANCE_EVIDENCE"):
        Path(evidence).write_text(
            json.dumps(
                {
                    **result.model_dump(mode="json"),
                    "jobStatus": after.status.value,
                    "jobVersion": after.version,
                    "B": 10100,
                    "R": 0,
                    "freezeReason": "OVER_LIMIT",
                    "receipt": None,
                    "executionOrders": 0,
                    "platformWrites": 0,
                    "input": "SYNTHETIC_VENDOR_REAL_PG_CURRENT_RISK_AND_UNCLAIMED_PROOF",
                    "platformCalls": 0,
                    "paidCalls": 0,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM execution_orders")).scalar_one() == 0
        assert (
            session.execute(
                text("SELECT count(*) FROM platform_requests WHERE effect='WRITE'")
            ).scalar_one()
            == 0
        )
        assert session.execute(
            text("SELECT claimed_at,dispatch_started_at FROM command_jobs")
        ).one() == (None, None)


def test_unfailed_budget_cannot_be_closed_as_unsent_failure(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    job = approved(data)
    with pytest.raises(ApiError) as denied:
        UnsentBudgetRejection(data["applications"]).reject(UUID(job.job_id), job.version)
    assert denied.value.code == ErrorCode.ILLEGAL_TRANSITION
    assert ledger(data)[:2] == (9990, 10)
    assert data["applications"].get(UUID(job.job_id)).status.value == "APPROVED"


async def test_already_sent_increase_cannot_use_unclaimed_proof(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    prepared, _, _ = await observation(data)
    await full_snapshot(data, paused=10000)
    data["budget_service"].refresh(1)
    with pytest.raises(ApiError) as denied:
        UnsentBudgetRejection(data["applications"]).reject(prepared.attempt.business.id, 4)
    assert denied.value.code == ErrorCode.VERSION_CONFLICT
    assert ledger(data)[:2] == (10100, 10)
    assert data["applications"].get(prepared.attempt.business.id).status.value == "EXECUTING"


async def test_unsent_rejection_audit_failure_rolls_back_job_money_and_risk(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = budget_ready
    job = approved(data)
    await full_snapshot(data, paused=10000)
    data["budget_service"].refresh(1)

    def failed(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic unsent audit failure")

    monkeypatch.setattr("yuliang.modules.execution.budget_governance.record", failed)
    with pytest.raises(RuntimeError, match="synthetic unsent audit failure"):
        UnsentBudgetRejection(data["applications"]).reject(UUID(job.job_id), job.version)
    assert ledger(data)[:2] == (10100, 10)
    assert data["applications"].get(UUID(job.job_id)).status.value == "APPROVED"
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(text("SELECT count(*) FROM budget_job_finalizations")).scalar_one() == 0
        )


async def reconcilable(data: dict[str, Any]) -> tuple[UUID, UUID]:
    prepared, _, raw = await observation(data)
    ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    await full_snapshot(data, active=115)
    data["budget_service"].refresh(1)
    difference = next(
        row
        for row in summary(data)["discrepancies"]
        if row["classification"] == "INFLIGHT_MISMATCH"
    )
    fact = await independent_match(data, prepared, 110)
    await full_snapshot(data, active=110)
    data["budget_service"].refresh(1)
    result = BudgetSettlement(data["applications"]).match(fact, 5)
    assert ledger(data) == (10000, 0, "RECONCILIATION")
    return UUID(difference["id"]), result.finalization_id


async def test_new_e2_finalization_resolves_mismatch_once(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    difference, finalization = await reconcilable(data)
    service = governor(data)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: service.resolve(difference, finalization), range(4)))
    assert sum(not item["replayed"] for item in values) == 1
    assert ledger(data) == (10000, 0, None)
    assert all(item["state"] == "RESOLVED" for item in summary(data)["alerts"])


async def test_resolution_audit_failure_keeps_discrepancy_and_freeze(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = budget_ready
    difference, finalization = await reconcilable(data)

    def failed(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic resolution audit failure")

    monkeypatch.setattr("yuliang.modules.execution.budget_governance.record", failed)
    with pytest.raises(RuntimeError, match="synthetic resolution audit failure"):
        governor(data).resolve(difference, finalization)
    assert ledger(data) == (10000, 0, "RECONCILIATION")
    state = next(item for item in summary(data)["discrepancies"] if item["id"] == str(difference))[
        "state"
    ]
    assert state == "OPEN"
