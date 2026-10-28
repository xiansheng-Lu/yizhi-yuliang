"""真实PG四时点检查与版本/审计；规则缺项拒绝，不注入全通过假规则。"""

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.command.applications import JobApplications
from yuliang.modules.command.jobs import (
    CommandJob,
    JobApproval,
    JobHistory,
    JobStatus,
    RiskCheck,
    RiskSummary,
)
from yuliang.modules.policy.models import PolicyConfig, RiskStage
from yuliang.modules.policy.repository import install_policy
from yuliang.modules.policy.service import RiskFramework
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_capability_evidence import capability as capability
from .test_capability_evidence import populate
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_job_transitions import synthetic_initial_notice
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import health_ready as health_ready
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


def policy() -> PolicyConfig:
    return PolicyConfig(
        daily_budget_minor=10000,
        target_roi="2",
        max_change_percent="10",
        cooldown_minutes=5,
        max_changes_per_day=10,
        mode="MANUAL",
    )


@pytest.fixture
def risk_ready(applications: dict[str, Any]) -> dict[str, Any]:
    data = applications
    populate(data, execution=True)  # 六阶段均是显式合成证据；不称真实平台验收。
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        key, version = install_policy(
            owner,
            data["tenant"],
            policy(),
            expected_version=0,
            proof_ref="synthetic/M2-08/policy-v1",
        )
    return data | {"policy_id": key, "policy_version": version}


def seed(data: dict[str, Any], stage: RiskStage) -> CommandJob:
    job: CommandJob = data["applications"].create(data["input"])
    status = {
        RiskStage.CREATE: JobStatus.BLOCKED,
        RiskStage.CONFIRM: JobStatus.DRAFT,
        RiskStage.APPROVE: JobStatus.PENDING_APPROVAL,
        RiskStage.EXECUTE: JobStatus.APPROVED,
    }[stage]
    if stage == RiskStage.CREATE:
        return job
    # 仅为该检查时点布置初始状态，不假称此前缺失的风险/资金/批准流程已经执行。
    approval = (
        JobApproval(state="PENDING")
        if stage == RiskStage.APPROVE
        else JobApproval(
            state="APPROVED",
            actor_id=str(data["approver"]),
            decided_at=job.created_at,
            method="MANUAL",
            approved_version=1,
        )
        if stage == RiskStage.EXECUTE
        else JobApproval()
    )
    summary = RiskSummary(
        result="REVIEW_REQUIRED",
        level="HIGH",
        checks=(
            RiskCheck(
                code="SYNTHETIC_STAGE_SETUP",
                passed=True,
                message="仅测试初始状态，不证明真实批准或风险规则通过",
            ),
        ),
        policy_version=1,
        checked_at=job.created_at,
    )
    job = CommandJob.model_validate(
        job.model_dump()
        | {
            "status": status,
            "risk_summary": summary,
            "approval": approval,
            "history": [
                JobHistory(status=status, at=job.created_at, detail="合成检查阶段初始状态")
            ],
        }
    )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        synthetic_initial_notice(owner, job)
        owner.execute(
            text(
                "UPDATE command_jobs SET status=:status,document=CAST(:document AS jsonb) "
                "WHERE id=:id"
            ),
            {"id": job.job_id, "status": status.value, "document": job.model_dump_json()},
        )
        owner.execute(
            text(
                "UPDATE job_events SET to_status=:status,detail='合成检查阶段初始状态' "
                "WHERE job_id=:id"
            ),
            {"status": status.value, "id": job.job_id},
        )
    return job


def framework(data: dict[str, Any], stage: RiskStage) -> RiskFramework:
    identity = (
        VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"])
        if stage == RiskStage.APPROVE
        else data["identity"]
    )
    return RiskFramework(
        JobApplications(data["engine"], identity, uuid4(), data["applications"].key)
    )


def assess(data: dict[str, Any], job: CommandJob, stage: RiskStage) -> Any:
    engine = framework(data, stage)
    with business_transaction(data["engine"], engine.applications.identity) as session:
        return engine.assess_job(session, UUID(job.job_id), stage)


@pytest.mark.parametrize("stage", list(RiskStage))
def test_four_entries_actual_basis_and_append_audit(
    risk_ready: dict[str, Any], stage: RiskStage
) -> None:
    data = risk_ready
    job = seed(data, stage)
    decision = None if stage == RiskStage.CREATE else assess(data, job, stage)
    summary = job.risk_summary if decision is None else decision.summary
    values = {c.code: c.passed for c in summary.checks}
    assert (
        summary.source == "SERVER" and summary.policy_version == 1 and summary.result == "BLOCKED"
    )
    for code in (
        "TARGET_SCOPE",
        "POLICY_CONFIGURED",
        "POLICY_VERSION",
        "STAGE_STATUS",
        "JOB_WINDOW",
        "REQUESTER_AUTHORIZATION",
        "ACTION_CAPABILITY",
        "CURRENT_VALUE_CONFIRMED",
        "CURRENT_VALUE_UNCHANGED",
    ):
        assert values[code], code
    assert not values["AMOUNT_LIMIT"] and values["INTENT_BINDING"]
    assert values["POLICY_ENGINE_READY"]  # 规则齐全与事实条件已通过分别校验。
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        row = (
            owner.execute(
                text(
                    "SELECT * FROM risk_assessments WHERE job_id=:id AND stage=:stage "
                    "ORDER BY checked_at DESC LIMIT 1"
                ),
                {"id": job.job_id, "stage": stage.value},
            )
            .mappings()
            .one()
        )
        assert RiskSummary.model_validate(row["summary"]) == summary and row["policy_version"] == 1
        assert row["expires_at"] - row["checked_at"] == timedelta(seconds=10)
        assert row["job_version"] == (None if stage == RiskStage.CREATE else 1)
        count = owner.execute(
            text("SELECT count(*) FROM audit_logs WHERE object_id=:id AND action='risk.assess'"),
            {"id": job.job_id},
        ).scalar_one()
        assert count == (1 if stage == RiskStage.CREATE else 2)
        assert owner.execute(
            text("SELECT status,version FROM command_jobs WHERE id=:id"), {"id": job.job_id}
        ).one() == (job.status.value, 1)
    if stage == RiskStage.EXECUTE and (evidence := os.getenv("YULIANG_RISK_EVIDENCE")):
        Path(evidence).write_text(
            json.dumps(
                {
                    "assessmentId": str(row["id"]),
                    "requestId": str(row["request_id"]),
                    "jobId": job.job_id,
                    "stage": stage.value,
                    "policyId": str(row["policy_id"]),
                    "policyVersion": row["policy_version"],
                    "summary": row["summary"],
                    "evidence": row["evidence"],
                    "stageSetup": "SYNTHETIC_PRECONDITIONS_ONLY",
                    "platformCalls": 0,
                    "paidCalls": 0,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )


def test_policy_change_rechecks_and_each_call_new_assessment(risk_ready: dict[str, Any]) -> None:
    data = risk_ready
    job = seed(data, RiskStage.CONFIRM)
    first = assess(data, job, RiskStage.CONFIRM)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        install_policy(
            owner,
            data["tenant"],
            policy().model_copy(update={"daily_budget_minor": 20000}),
            expected_version=1,
            proof_ref="synthetic/M2-08/policy-v2",
        )
    second = assess(data, job, RiskStage.CONFIRM)
    assert (
        first.assessment_id != second.assessment_id
        and second.summary.checked_at > first.summary.checked_at
    )
    assert first.summary.policy_version == 1 and second.summary.policy_version == 2
    assert not next(c.passed for c in second.summary.checks if c.code == "POLICY_VERSION")
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert (
            owner.execute(
                text("SELECT count(*) FROM policy_versions WHERE tenant_id=:tenant"),
                {"tenant": data["tenant"]},
            ).scalar_one()
            == 2
        )


@pytest.mark.parametrize(
    "change,code",
    [
        ("authorization", "CURRENT_VALUE_CONFIRMED"),
        ("stale", "CURRENT_VALUE_CONFIRMED"),
        ("current_changed", "CURRENT_VALUE_UNCHANGED"),
        ("capability", "ACTION_CAPABILITY"),
        ("requester_revoked", "REQUESTER_AUTHORIZATION"),
    ],
)
def test_actual_changes_cannot_be_overridden_by_old_approval(
    risk_ready: dict[str, Any], change: str, code: str
) -> None:
    data = risk_ready
    stage = RiskStage.APPROVE if change == "requester_revoked" else RiskStage.EXECUTE
    job = seed(data, stage)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if change == "authorization":
            owner.execute(
                text(
                    "UPDATE provider_authorizations SET expires_at=clock_timestamp()"
                    "-interval '1 second' WHERE id=:id"
                ),
                {"id": data["ctx"].authorization_id},
            )
        elif change == "stale":
            owner.execute(
                text(
                    "UPDATE sync_pages SET source_updated_at=source_updated_at-interval '1 hour',"
                    "fetched_at=fetched_at-interval '1 hour' WHERE run_id=:id"
                ),
                {"id": data["good_lease"].run_id},
            )
        elif change == "current_changed":
            owner.execute(
                text(
                    "UPDATE sync_resource_mirrors SET projection=jsonb_set(projection,"
                    "'{data,budget_minor}','150'::jsonb) WHERE source_id=:id"
                ),
                {"id": data["source"]},
            )
        elif change == "capability":
            owner.execute(
                text("UPDATE capability_bindings SET active=false WHERE id=:id"),
                {"id": data["ctx"].binding_id},
            )
        else:
            owner.execute(
                text("DELETE FROM role_grants WHERE membership_id=:id"), {"id": data["member"]}
            )
    outcome = assess(data, job, stage)
    assert outcome.summary.result == "BLOCKED" and not next(
        c.passed for c in outcome.summary.checks if c.code == code
    )
    assert job.approval.state in {"PENDING", "APPROVED"}


def test_out_of_scope_and_cross_tenant_return_no_object_details(risk_ready: dict[str, Any]) -> None:
    data = risk_ready
    job = seed(data, RiskStage.CONFIRM)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:id"), {"id": data["member"]}
        )
        other = owner.execute(
            text("INSERT INTO tenants(name) VALUES('Other risk tenant') RETURNING id")
        ).scalar_one()
    for identity in (data["identity"], VerifiedIdentity(other, data["member"], data["user"])):
        instance = RiskFramework(
            JobApplications(data["engine"], identity, uuid4(), data["applications"].key)
        )
        with (
            pytest.raises(ApiError) as denied,
            business_transaction(data["engine"], identity) as session,
        ):
            instance.assess_job(session, UUID(job.job_id), RiskStage.CONFIRM)
        assert denied.value.code == ErrorCode.FORBIDDEN and job.job_id not in str(denied.value)


def test_runtime_policy_and_assessment_history_immutable(risk_ready: dict[str, Any]) -> None:
    data = risk_ready
    seed(data, RiskStage.CREATE)
    for query in (
        "UPDATE risk_policies SET version=99",
        "DELETE FROM policy_versions",
        "UPDATE risk_assessments SET summary='{}'::jsonb",
    ):
        with (
            pytest.raises(DBAPIError),
            business_transaction(data["engine"], data["identity"]) as session,
        ):
            session.execute(text(query))


def test_risk_audit_failure_rolls_back_creation_and_assessment(
    risk_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = risk_ready

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic risk audit failure")

    monkeypatch.setattr("yuliang.modules.policy.service.record", fail)
    with pytest.raises(RuntimeError):
        data["applications"].create(data["input"])
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM risk_assessments")).scalar_one() == 0
        assert (
            session.execute(
                text("SELECT count(*) FROM command_jobs WHERE document IS NOT NULL")
            ).scalar_one()
            == 0
        )


def test_approval_risk_record_stays_private_to_its_actor(risk_ready: dict[str, Any]) -> None:
    data = risk_ready
    job = seed(data, RiskStage.APPROVE)
    decision = assess(data, job, RiskStage.APPROVE)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM risk_assessments WHERE id=:id"),
                {"id": decision.assessment_id},
            ).scalar_one()
            == 0
        )
