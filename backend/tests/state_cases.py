"""状态内核的显式合成事实；绝不作为真实策略/平台/资金证据。"""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from yuliang.modules.command.jobs import (
    BooleanValue,
    CommandJob,
    HandoffValue,
    JobApproval,
    JobError,
    JobHistory,
    JobReceipt,
    JobStatus,
    RiskCheck,
    RiskSummary,
)
from yuliang.modules.command.state import JobEvent, StateFacts


def state_job(status: JobStatus, at: datetime, *, base: CommandJob | None = None) -> CommandJob:
    manual = status == JobStatus.MANUAL_REQUIRED
    risk = RiskSummary(
        result="MANUAL_ONLY"
        if manual
        else "BLOCKED"
        if status == JobStatus.BLOCKED
        else "REVIEW_REQUIRED",
        level="HIGH",
        checks=(
            RiskCheck(
                code="SYNTHETIC_STATE_INPUT",
                passed=status not in {JobStatus.BLOCKED, JobStatus.MANUAL_REQUIRED},
                message="仅状态内核合成检查，不证明策略/平台已验",
            ),
        ),
        policy_version=1,
        checked_at=at - timedelta(seconds=1),
    )
    approved = status in {
        JobStatus.APPROVED,
        JobStatus.EXECUTING,
        JobStatus.VERIFYING,
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
    }
    approval = (
        JobApproval(
            state="APPROVED",
            actor_id=str(uuid4()),
            decided_at=at - timedelta(seconds=2),
            method="MANUAL",
            approved_version=1,
        )
        if approved
        else JobApproval(state="PENDING")
        if status == JobStatus.PENDING_APPROVAL
        else JobApproval(
            state="REJECTED",
            actor_id=str(uuid4()),
            decided_at=at - timedelta(seconds=2),
            method="MANUAL",
            approved_version=1,
        )
        if status == JobStatus.REJECTED
        else JobApproval()
    )
    new = HandoffValue(value="START") if manual else BooleanValue(value=False)
    receipt = (
        JobReceipt(
            receipt_id=str(uuid4()),
            platform_receipt_id=None,
            accepted=True,
            received_at=at - timedelta(seconds=2),
            verification="MATCHED" if status == JobStatus.SUCCEEDED else "PENDING",
            observed_value=new if status == JobStatus.SUCCEEDED else None,
            verified_at=at - timedelta(seconds=1) if status == JobStatus.SUCCEEDED else None,
        )
        if status in {JobStatus.VERIFYING, JobStatus.SUCCEEDED}
        else None
    )
    fields = dict(
        source="PC_MANUAL",
        tenant_id="1",
        shop_id=str(uuid4()),
        account_id=None if manual else str(uuid4()),
        live_session_id=None,
        target_type="LIVE_SESSION" if manual else "AD_PLAN",
        target_id=str(uuid4()),
        action_type="REQUEST_LIVE_START" if manual else "PAUSE_AD_PLAN",
        old_value=None if manual else BooleanValue(value=True),
        new_value=new,
        intent="合成状态内核申请",
        status=status,
        created_at=at - timedelta(minutes=2),
        updated_at=at - timedelta(seconds=1),
        expires_at=at + timedelta(minutes=15),
        risk_summary=risk,
        approval=approval,
        execution_receipt=receipt,
        error=JobError(
            code="SYNTHETIC_REJECTION",
            message="合成拒绝输入",
            retryable=True,
            at=at - timedelta(seconds=1),
        )
        if status == JobStatus.FAILED
        else None,
        version=1,
        idempotency_key=uuid4().hex,
        history=(JobHistory(status=status, at=at - timedelta(seconds=1), detail="合成初始状态"),),
    )
    if base:
        fields.update(
            tenant_id=base.tenant_id,
            shop_id=base.shop_id,
            account_id=base.account_id,
            target_id=base.target_id,
            idempotency_key=base.idempotency_key,
            created_at=base.created_at,
            updated_at=at,
        )
        fields["history"] = (JobHistory(status=status, at=at, detail="合成初始状态"),)
    if manual:
        fields["live_session_id"] = fields["target_id"]
    return CommandJob.model_validate(fields | {"job_id": base.job_id if base else str(uuid4())})


def facts_for(event: JobEvent, job: CommandJob, at: datetime) -> StateFacts:
    if event == JobEvent.UNSENT_REJECT:
        return StateFacts(
            risk=RiskSummary(
                result="BLOCKED",
                level="HIGH",
                policy_version=1,
                checked_at=at,
                checks=(
                    RiskCheck(code="AMOUNT_LIMIT", passed=False, message="仅状态内核合成额度拒绝"),
                ),
            ),
            error=JobError(code="RISK_BLOCKED", message="合成额度拒绝", retryable=False, at=at),
            evidence_level="E1",
            proof_ref="synthetic/state-kernel/UNSENT_REJECT",
        )
    risk = (
        job.risk_summary.model_copy(update={"checked_at": at})
        if event in {JobEvent.CONFIRM, JobEvent.APPROVE, JobEvent.CLAIM, JobEvent.SAFE_RETRY}
        else None
    )
    receipt = None
    accepted = (
        None
        if event == JobEvent.SUBMIT_UNKNOWN
        else False
        if event in {JobEvent.REJECT_SEND, JobEvent.CONFIRMED_NOT_EXECUTED}
        else True
    )
    verification = (
        "MATCHED"
        if event == JobEvent.MATCH
        else "MISMATCH"
        if event == JobEvent.MISMATCH
        else "UNKNOWN"
        if event in {JobEvent.SUBMIT_UNKNOWN, JobEvent.READ_UNKNOWN}
        else "PENDING"
    )
    if event in {
        JobEvent.ACCEPT,
        JobEvent.MATCH,
        JobEvent.MISMATCH,
        JobEvent.READ_UNKNOWN,
        JobEvent.REJECT_SEND,
        JobEvent.SUBMIT_UNKNOWN,
        JobEvent.CONFIRMED_NOT_EXECUTED,
    }:
        receipt = JobReceipt(
            receipt_id=str(uuid4()),
            platform_receipt_id=None,
            accepted=accepted,
            received_at=at if accepted is not None else None,
            verification=verification,
            observed_value=job.new_value
            if event == JobEvent.MATCH
            else job.old_value
            if event == JobEvent.MISMATCH
            else None,
            verified_at=at
            if event in {JobEvent.MATCH, JobEvent.MISMATCH, JobEvent.READ_UNKNOWN}
            else None,
        )
    return StateFacts(
        risk=risk,
        receipt=receipt,
        error=JobError(code="SYNTHETIC_REJECTION", message="合成错误分类", retryable=True, at=at)
        if event in {JobEvent.REJECT_SEND, JobEvent.MISMATCH, JobEvent.CONFIRMED_NOT_EXECUTED}
        else None,
        evidence_level="E2"
        if event == JobEvent.MATCH
        else "E1"
        if event in {JobEvent.REJECT_SEND, JobEvent.CONFIRMED_NOT_EXECUTED, JobEvent.SAFE_RETRY}
        else "E0",
        proof_ref="synthetic/state-kernel/" + event.value,
        retry_current=job.old_value,
    )


REQUESTER = UUID("00000000-0000-4000-8000-000000000001")
APPROVER = UUID("00000000-0000-4000-8000-000000000002")
