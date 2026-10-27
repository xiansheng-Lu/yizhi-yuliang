"""真实PG四时点组件：预算为合成组件输入，阶段布置不代表资金/审批主流程完成。"""

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.command.jobs import CommandJob, JobStatus, MoneyValue
from yuliang.modules.command.state import JobEvent, StateFacts, evolve
from yuliang.modules.policy.models import RiskDecision, RiskStage
from yuliang.modules.policy.repository import install_policy
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import STATUS, ApiError, ErrorCode

from .test_capability_evidence import capability as capability
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import framework, policy
from .test_risk_framework import risk_ready as risk_ready
from .test_risk_rules import application, ledger
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


def assess(data: dict[str, Any], job: CommandJob, stage: RiskStage) -> RiskDecision:
    engine = framework(data, stage)
    with business_transaction(data["engine"], engine.applications.identity) as session:
        return engine.assess_job(session, UUID(job.job_id), stage, expected_version=job.version)


def store_stage(data: dict[str, Any], job: CommandJob) -> None:
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""UPDATE command_jobs SET status=:status,version=:version,
            updated_at=:at,document=CAST(:document AS jsonb) WHERE id=:id"""),
            {
                "id": job.job_id,
                "status": job.status.value,
                "version": job.version,
                "at": job.updated_at,
                "document": job.model_dump_json(),
            },
        )
        # 阶段布置同步事件投影；这不是资金/审批参与者已接通的证据。
        owner.execute(text("DELETE FROM job_events WHERE job_id=:id"), {"id": job.job_id})
        for index, event in enumerate(job.history):
            owner.execute(
                text("""INSERT INTO job_events(tenant_id,job_id,to_status,actor_id,request_id,
                reason_code,detail,created_at,job_version) VALUES(:tenant,:job,:status,:actor,
                :request,'SYNTHETIC_STAGE_SETUP',:detail,:at,:version)"""),
                {
                    "tenant": data["tenant"],
                    "job": job.job_id,
                    "status": event.status.value,
                    "actor": data["user"],
                    "request": uuid4(),
                    "detail": event.detail,
                    "at": event.at,
                    "version": index + 1,
                },
            )


def stage_only(data: dict[str, Any], job: CommandJob, event: JobEvent) -> CommandJob:
    """用实际检查和纯状态归约生成阶段，再由维护身份布置；没有资金参与者或审批记录。"""
    stage = RiskStage.CONFIRM if event == JobEvent.CONFIRM else RiskStage.APPROVE
    decision = assess(data, job, stage)
    decision.require_allowed()
    actor = data["user"] if event == JobEvent.CONFIRM else data["approver"]
    updated = evolve(
        job,
        event,
        StateFacts(risk=decision.summary),
        at=decision.summary.checked_at,
        actor_id=actor,
        requester_id=data["user"],
    )
    store_stage(data, updated)
    return updated


def approved_stage(data: dict[str, Any]) -> CommandJob:
    ledger(data)
    draft = application(data)
    assert draft.status == JobStatus.DRAFT
    pending = stage_only(data, draft, JobEvent.CONFIRM)
    return stage_only(data, pending, JobEvent.APPROVE)


def test_real_checks_survive_normal_versions_and_refresh_clock(risk_ready: dict[str, Any]) -> None:
    data = risk_ready
    approved = approved_stage(data)
    assert approved.version == 3 and approved.approval.approved_version == 2
    # 新批次时间可更新；仍重验现值，却不得用新抓取时钟重算原批准意图。
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE sync_pages SET source_updated_at=clock_timestamp(),"
                "fetched_at=clock_timestamp() WHERE run_id=:run"
            ),
            {"run": data["good_lease"].run_id},
        )
    decision = assess(data, approved, RiskStage.EXECUTE)
    decision.require_allowed()
    assert all(item.passed for item in decision.summary.checks)
    with business_transaction(data["engine"], data["identity"]) as session:
        row = (
            session.execute(
                text("SELECT intent_hash,target_proof FROM command_jobs WHERE id=:id"),
                {"id": approved.job_id},
            )
            .mappings()
            .one()
        )
    if evidence := os.getenv("YULIANG_INTENT_EVIDENCE"):
        Path(evidence).write_text(
            json.dumps(
                {
                    "jobId": approved.job_id,
                    "assessmentId": str(decision.assessment_id),
                    "jobVersion": approved.version,
                    "approvedVersion": approved.approval.approved_version,
                    "intentHash": row["intent_hash"],
                    "policyVersion": decision.summary.policy_version,
                    "checks": {item.code: item.passed for item in decision.summary.checks},
                    "stageSetup": "MAINTENANCE_SETUP_NO_BUDGET_OR_APPROVAL_PARTICIPANTS",
                    "platformCalls": 0,
                    "paidCalls": 0,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


@pytest.mark.parametrize("stage", [RiskStage.CONFIRM, RiskStage.APPROVE])
def test_old_policy_has_specific_409(risk_ready: dict[str, Any], stage: RiskStage) -> None:
    data = risk_ready
    ledger(data)
    job = application(data)
    if stage == RiskStage.APPROVE:
        job = stage_only(data, job, JobEvent.CONFIRM)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        install_policy(
            owner,
            data["tenant"],
            policy().model_copy(update={"daily_budget_minor": 11000}),
            expected_version=1,
            proof_ref="synthetic/M2-09b/policy-v2",
        )
    decision = assess(data, job, stage)
    with pytest.raises(ApiError) as error:
        decision.require_allowed()
    assert error.value.code == ErrorCode.POLICY_VERSION_CHANGED and STATUS[error.value.code] == 409
    assert data["applications"].get(UUID(job.job_id)).version == job.version


@pytest.mark.parametrize("fault", ["new", "old", "intent", "expiry", "proof"])
def test_approved_payload_tampering_blocks(risk_ready: dict[str, Any], fault: str) -> None:
    data = risk_ready
    job = approved_stage(data)
    if fault == "proof":
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(
                text(
                    "UPDATE command_jobs SET target_proof=jsonb_set(target_proof,"
                    "'{policyRevision}','99'::jsonb) WHERE id=:id"
                ),
                {"id": job.job_id},
            )
    else:
        update: dict[str, Any] = (
            {"new_value": MoneyValue(amount_minor=109)}
            if fault == "new"
            else (
                {"old_value": MoneyValue(amount_minor=99)}
                if fault == "old"
                else (
                    {"intent": "已批准后修改的文本"}
                    if fault == "intent"
                    else {"expires_at": job.expires_at + timedelta(minutes=1)}
                )
            )
        )
        job = CommandJob.model_validate(job.model_dump() | update)
        store_stage(data, job)
    outcome = assess(data, job, RiskStage.EXECUTE)
    assert not next(item.passed for item in outcome.summary.checks if item.code == "INTENT_BINDING")
    with pytest.raises(ApiError) as error:
        outcome.require_allowed()
    assert error.value.code == ErrorCode.VERSION_CONFLICT


@pytest.mark.parametrize(
    "fault", ["current", "capability", "approval_role", "approval_scope", "approval_user"]
)
def test_execution_uses_current_value_capability_and_reviewer_qualification(
    risk_ready: dict[str, Any], fault: str
) -> None:
    data = risk_ready
    job = approved_stage(data)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if fault == "current":
            owner.execute(
                text(
                    "UPDATE sync_resource_mirrors SET projection=jsonb_set(projection,"
                    "'{data,budget_minor}','101'::jsonb) WHERE source_id=:id"
                ),
                {"id": data["source"]},
            )
        elif fault == "capability":
            owner.execute(
                text("UPDATE capability_bindings SET active=false WHERE id=:id"),
                {"id": data["ctx"].binding_id},
            )
        elif fault == "approval_user":
            owner.execute(
                text("UPDATE users SET status='DISABLED' WHERE id=:id"), {"id": data["approver"]}
            )
        else:
            query = (
                "DELETE FROM role_grants WHERE membership_id=:id"
                if fault == "approval_role"
                else "DELETE FROM data_scopes WHERE membership_id=:id"
            )
            owner.execute(
                text(query),
                {"id": data["approver_member"]},
            )
    outcome = assess(data, job, RiskStage.EXECUTE)
    with pytest.raises(ApiError):
        outcome.require_allowed()
    assert not next(
        c.passed
        for c in outcome.summary.checks
        if c.code
        == (
            "CURRENT_VALUE_UNCHANGED"
            if fault == "current"
            else "ACTION_CAPABILITY"
            if fault == "capability"
            else "APPROVAL_CURRENT"
        )
    )


def test_runtime_cannot_replace_seal_or_use_stale_version(risk_ready: dict[str, Any]) -> None:
    data = risk_ready
    ledger(data)
    job = application(data)
    with (
        pytest.raises(DBAPIError),
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(
            text("UPDATE command_jobs SET intent_hash=repeat('a',64) WHERE id=:id"),
            {"id": job.job_id},
        )
    instance = framework(data, RiskStage.CONFIRM)
    with (
        pytest.raises(ApiError) as error,
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        instance.require_job(session, UUID(job.job_id), RiskStage.CONFIRM, expected_version=99)
    assert error.value.code == ErrorCode.VERSION_CONFLICT
