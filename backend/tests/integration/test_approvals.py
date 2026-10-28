"""真实PG审批/资金/风险；前置确认阶段及平台来源明确合成，不冒充HTTP/真实人审。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.command.applications import JobApplications
from yuliang.modules.command.state import JobEvent
from yuliang.modules.execution.approval_models import ReviewJobRequest, ServerApprovalPermit
from yuliang.modules.execution.approvals import (
    ApprovalDecisionResult,
    Approvals,
    register_server_permit,
)
from yuliang.modules.execution.attempt_models import BusinessKind, BusinessRef
from yuliang.modules.execution.attempts import ExecutionOrders
from yuliang.modules.policy.repository import install_policy
from yuliang.modules.policy.service import RiskFramework
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_budget_initialization import budget_ready as budget_ready
from .test_budget_reservations import hold
from .test_budget_settlement import ledger
from .test_capability_evidence import capability as capability
from .test_execution_attempts import SyntheticBudgetSerializer
from .test_identity_database import database as database
from .test_intent_binding import stage_only
from .test_job_applications import applications as applications
from .test_job_transitions import service as synthetic_state_service
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import policy
from .test_risk_framework import risk_ready as risk_ready
from .test_risk_rules import application
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


def reviewer(data: dict[str, Any]) -> Approvals:
    return Approvals(
        JobApplications(
            data["engine"],
            VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"]),
            uuid4(),
            data["applications"].key,
        )
    )


def pending(data: dict[str, Any], amount: int = 110, version: int = 1) -> Any:
    data["budget_service"].refresh(version)
    draft = application(data, amount)
    hold(data, draft)
    job = stage_only(data, draft, JobEvent.CONFIRM)
    ticket = Approvals(data["applications"]).open(UUID(job.job_id), job.version)
    assert Approvals(data["applications"]).open(UUID(job.job_id), job.version) == ticket
    return job


def request(job: Any, decision: str = "APPROVE") -> ReviewJobRequest:
    return ReviewJobRequest.model_validate(
        {"job_id": job.job_id, "expected_version": job.version, "decision": decision}
    )


def trace(
    data: dict[str, Any], result: ApprovalDecisionResult, name: str, permit_id: UUID | None = None
) -> None:
    if directory := os.getenv("YULIANG_APPROVAL_EVIDENCE"):
        with business_transaction(data["engine"], data["identity"]) as session:
            proof = session.execute(
                text("SELECT request_id,risk_assessment_id FROM approval_decisions WHERE id=:id"),
                {"id": result.decision_id},
            ).one()
        Path(directory, name).write_text(
            json.dumps(
                {
                    **result.model_dump(mode="json", exclude={"job"}),
                    "jobId": result.job.job_id,
                    "jobStatus": result.job.status,
                    "jobVersion": result.job.version,
                    "permitId": str(permit_id) if permit_id else None,
                    "requestId": str(proof.request_id),
                    "riskAssessmentId": str(proof.risk_assessment_id)
                    if proof.risk_assessment_id
                    else None,
                    "B": ledger(data)[0],
                    "R": ledger(data)[1],
                    "platformCalls": 0,
                    "paidCalls": 0,
                    "input": "SYNTHETIC_CONFIRM_REAL_PG_APPROVAL_BUDGET_NO_HUMAN_SIGNOFF",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


@pytest.mark.parametrize("decision", ["APPROVE", "REJECT"])
def test_actual_manual_decision_and_rejection_releases_same_transaction(
    budget_ready: dict[str, Any],
    decision: str,
) -> None:
    data = budget_ready
    job = pending(data)
    result = reviewer(data).review(request(job, decision))
    assert result.job.status.value == ("APPROVED" if decision == "APPROVE" else "REJECTED")
    assert result.job.version == 3 and result.approval_version == 2 and result.method == "MANUAL"
    assert result.job.approval.actor_id == str(data["approver"])
    assert ledger(data)[:2] == (9990, 10 if decision == "APPROVE" else 0)
    trace(data, result, "manual-" + decision.lower() + ".json")
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM approval_decisions")).scalar_one() == 1
        assert session.execute(text("SELECT comment FROM approval_decisions")).scalar_one() is None
        assert session.execute(text("SELECT count(*) FROM execution_orders")).scalar_one() == 0
        assert (
            session.execute(
                text("SELECT count(*) FROM audit_logs WHERE action='approval.request'")
            ).scalar_one()
            == 1
        )


def test_requester_cannot_self_approve(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    job = pending(data)
    with pytest.raises(ApiError) as denied:
        Approvals(data["applications"]).review(request(job))
    assert denied.value.code == ErrorCode.FORBIDDEN and ledger(data)[:2] == (9990, 10)


def test_two_deciders_only_one_persistent_decision(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    job = pending(data)
    other_user, other_member = uuid4(), uuid4()
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO users(id,email,display_name) VALUES(:id,:email,'Synthetic reviewer')"
            ),
            {"id": other_user, "email": f"{other_user}@example.invalid"},
        )
        owner.execute(
            text("INSERT INTO memberships(id,tenant_id,user_id) VALUES(:id,:tenant,:user)"),
            {"id": other_member, "tenant": data["tenant"], "user": other_user},
        )
        owner.execute(
            text("""INSERT INTO role_grants(tenant_id,membership_id,role_id)
            SELECT :tenant,:member,id FROM roles WHERE tenant_id=:tenant AND code='OWNER'"""),
            {"tenant": data["tenant"], "member": other_member},
        )
        owner.execute(
            text(
                "INSERT INTO data_scopes(tenant_id,membership_id,scope_type) "
                "VALUES(:tenant,:member,'ALL_TENANT')"
            ),
            {"tenant": data["tenant"], "member": other_member},
        )
    second = Approvals(
        JobApplications(
            data["engine"],
            VerifiedIdentity(data["tenant"], other_member, other_user),
            uuid4(),
            data["applications"].key,
        )
    )
    barrier = Barrier(2)

    def decide(service: Approvals) -> ApprovalDecisionResult | ErrorCode:
        barrier.wait(timeout=5)
        try:
            return service.review(request(job))
        except ApiError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, [reviewer(data), second]))
    assert sum(isinstance(result, ApprovalDecisionResult) for result in results) == 1
    assert results.count(ErrorCode.VERSION_CONFLICT) == 1
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM approval_decisions")).scalar_one() == 1


@pytest.mark.parametrize("decision", ["APPROVE", "REJECT"])
def test_audit_failure_rolls_back_decision_state_and_money(
    budget_ready: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
) -> None:
    data = budget_ready
    job = pending(data)

    def failed(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic approval audit failure")

    monkeypatch.setattr("yuliang.modules.execution.approvals.record", failed)
    with pytest.raises(RuntimeError, match="synthetic approval audit failure"):
        reviewer(data).review(request(job, decision))
    assert data["applications"].get(UUID(job.job_id)).status.value == "PENDING_APPROVAL"
    assert ledger(data)[:2] == (9990, 10)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT state FROM approvals")).scalar_one() == "PENDING"
        assert session.execute(text("SELECT count(*) FROM approval_decisions")).scalar_one() == 0
        assert (
            session.execute(text("SELECT count(*) FROM budget_job_finalizations")).scalar_one() == 0
        )


@pytest.mark.parametrize("amount", [90, 110])
def test_backend_policy_permit_only_reduces_and_keeps_actual_execute_qualification(
    budget_ready: dict[str, Any],
    amount: int,
) -> None:
    data = budget_ready
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        policy_id, version = install_policy(
            owner,
            data["tenant"],
            policy().model_copy(update={"mode": "AUTOPILOT"}),
            expected_version=1,
            proof_ref="synthetic/M2-14/autopilot",
        )
    job = pending(data, amount, version)
    now = datetime.now(UTC)
    permit = ServerApprovalPermit(
        id=uuid4(),
        tenant_id=data["tenant"],
        policy_id=policy_id,
        policy_version=version,
        policy_actor_membership_id=data["approver_member"],
        account_id=data["ctx"].account_id,
        campaign_id=data["campaign"],
        action="CHANGE_DAILY_BUDGET",
        max_change_minor=10,
        max_change_percent="10",
        valid_from=now - timedelta(seconds=5),
        valid_until=now + timedelta(hours=1),
        proof_ref="synthetic/M2-14/low-risk-permit",
        active=True,
    )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_server_permit(owner, permit)
    service = reviewer(data)
    if amount > 100:
        with pytest.raises(ApiError) as denied:
            service.approve_by_policy(UUID(job.job_id), job.version, permit.id)
        assert denied.value.code == ErrorCode.FORBIDDEN and ledger(data)[:2] == (9990, 10)
        return
    result = service.approve_by_policy(UUID(job.job_id), job.version, permit.id)
    assert result.method == "SERVER_POLICY" and result.job.approval.method == "SERVER_POLICY"
    trace(data, result, "policy-decision.json", permit.id)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(text("SELECT actor_kind FROM approval_decisions")).scalar_one()
            == "POLICY"
        )
    prepared = ExecutionOrders(
        data["applications"], SyntheticBudgetSerializer(data["spec"])
    ).prepare(
        BusinessRef(kind=BusinessKind.COMMAND_JOB, id=UUID(job.job_id)),
        data["ctx"],
        result.job.version,
        0,
        uuid4(),
    )
    assert prepared.attempt.send_allowed and prepared.attempt.attempt_state == "SENT"
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE server_approval_permits SET active=false WHERE id=:id"), {"id": permit.id}
        )
    with business_transaction(data["engine"], data["identity"]) as session:
        latest = RiskFramework(data["applications"]).assess_dispatch(
            session, prepared.attempt.guard
        )
        assert not next(
            check.passed for check in latest.summary.checks if check.code == "APPROVAL_CURRENT"
        )


@pytest.mark.parametrize("decision", ["APPROVE", "REJECT"])
def test_agency_operator_has_no_default_approval_right(
    budget_ready: dict[str, Any], decision: str
) -> None:
    data = budget_ready
    job = pending(data)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM role_grants WHERE membership_id=:id"), {"id": data["approver_member"]}
        )
        owner.execute(
            text("""INSERT INTO role_grants(tenant_id,membership_id,role_id)
            SELECT :tenant,:member,id FROM roles
            WHERE tenant_id=:tenant AND code='AGENCY_OPERATOR'"""),
            {"tenant": data["tenant"], "member": data["approver_member"]},
        )
    with pytest.raises(ApiError) as denied:
        reviewer(data).review(request(job, decision))
    assert denied.value.code == ErrorCode.FORBIDDEN and ledger(data)[:2] == (9990, 10)


@pytest.mark.parametrize("decision", ["APPROVE", "REJECT"])
def test_policy_change_keeps_pending_and_requires_new_confirmation(
    budget_ready: dict[str, Any], decision: str
) -> None:
    data = budget_ready
    job = pending(data)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        install_policy(
            owner,
            data["tenant"],
            policy(),
            expected_version=1,
            proof_ref="synthetic/M2-14/new-policy",
        )
    with pytest.raises(ApiError) as changed:
        reviewer(data).review(request(job, decision))
    assert changed.value.code == ErrorCode.POLICY_VERSION_CHANGED and ledger(data)[:2] == (9990, 10)


@pytest.mark.parametrize("mutation", ["intent", "payload"])
def test_frozen_resource_tampering_never_decides(
    budget_ready: dict[str, Any], mutation: str
) -> None:
    data = budget_ready
    job = pending(data)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if mutation == "intent":
            owner.execute(
                text("UPDATE command_jobs SET intent_hash=:hash WHERE id=:id"),
                {"hash": "a" * 64, "id": job.job_id},
            )
        else:
            owner.execute(
                text(
                    "UPDATE command_jobs SET "
                    "document=jsonb_set(document,'{new_value,amount_minor}','111') WHERE id=:id"
                ),
                {"id": job.job_id},
            )
    with pytest.raises(ApiError) as changed:
        reviewer(data).review(request(job))
    assert changed.value.code == ErrorCode.INVALID_INPUT and ledger(data)[:2] == (9990, 10)


def test_empty_policy_permits_and_old_kernel_bypass_remain_closed(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    job = pending(data)
    with pytest.raises(ApiError) as denied:
        reviewer(data).approve_by_policy(UUID(job.job_id), job.version, uuid4())
    assert denied.value.code == ErrorCode.FORBIDDEN
    with pytest.raises(ApiError) as bypass:
        synthetic_state_service(data, JobEvent.APPROVE).apply(
            UUID(job.job_id), job.version, JobEvent.APPROVE
        )
    assert bypass.value.code == ErrorCode.FORBIDDEN
    for sql in (
        "UPDATE approvals SET state='APPROVED'",
        "DELETE FROM approval_decisions",
        "UPDATE server_approval_permits SET active=true",
    ):
        with (
            pytest.raises(DBAPIError),
            business_transaction(data["engine"], data["identity"]) as session,
        ):
            session.execute(text(sql))
    assert ledger(data)[:2] == (9990, 10)


@pytest.mark.parametrize("fault", ["expired", "actor", "target", "amount", "action", "inactive"])
def test_actual_policy_boundary_never_mints_approval(
    budget_ready: dict[str, Any],
    fault: str,
) -> None:
    data = budget_ready
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        policy_id, version = install_policy(
            owner,
            data["tenant"],
            policy().model_copy(update={"mode": "AUTOPILOT"}),
            expected_version=1,
            proof_ref="synthetic/M2-14/permit-boundary-policy",
        )
    job = pending(data, 90, version)
    now = datetime.now(UTC)
    values: dict[str, Any] = dict(
        id=uuid4(),
        tenant_id=data["tenant"],
        policy_id=policy_id,
        policy_version=version,
        policy_actor_membership_id=data["approver_member"],
        account_id=data["ctx"].account_id,
        campaign_id=data["campaign"],
        action="CHANGE_DAILY_BUDGET",
        max_change_minor=10,
        max_change_percent="10",
        valid_from=now - timedelta(seconds=5),
        valid_until=now + timedelta(hours=1),
        proof_ref="synthetic/M2-14/boundary-permit",
        active=True,
    )
    if fault == "expired":
        values.update(valid_from=now - timedelta(hours=2), valid_until=now - timedelta(hours=1))
    elif fault == "actor":
        values["policy_actor_membership_id"] = data["identity"].membership_id
    elif fault == "target":
        other = uuid4()
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(
                text("""INSERT INTO campaigns(id,tenant_id,shop_id,account_id,
                provider,product_line,native_id)
                VALUES(:id,:tenant,:shop,:account,'tencent','adq','policy-other')"""),
                {
                    "id": other,
                    "tenant": data["tenant"],
                    "shop": data["shop"],
                    "account": data["ctx"].account_id,
                },
            )
        values["campaign_id"] = other
    elif fault == "amount":
        values["max_change_minor"] = 5
    elif fault == "action":
        values["action"] = "CHANGE_BID"
    else:
        values["active"] = False
    permit = ServerApprovalPermit.model_validate(values)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_server_permit(owner, permit)
    with pytest.raises(ApiError) as denied:
        reviewer(data).approve_by_policy(UUID(job.job_id), job.version, permit.id)
    assert denied.value.code == ErrorCode.FORBIDDEN
    assert data["applications"].get(UUID(job.job_id)).status.value == "PENDING_APPROVAL"
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM approval_decisions")).scalar_one() == 0
