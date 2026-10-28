"""V8执行视图使用V1同一事实源；不改资源镜像、不提供重发资格。"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from yuliang.modules.command.jobs import JobReceipt, JobStatus, JobValue
from yuliang.modules.command.projections import JobProjection

type ExecutionStatus = Literal[
    "PENDING_APPROVAL",
    "PENDING",
    "EXECUTING",
    "VERIFYING",
    "EFFECTIVE",
    "FAILED",
    "UNKNOWN",
    "CANCELLED",
    "PARTIAL",
    "MANUAL",
]

STATUSES: dict[JobStatus, ExecutionStatus] = {
    JobStatus.DRAFT: "PENDING_APPROVAL",
    JobStatus.PENDING_APPROVAL: "PENDING_APPROVAL",
    JobStatus.APPROVED: "PENDING",
    JobStatus.EXECUTING: "EXECUTING",
    JobStatus.VERIFYING: "VERIFYING",
    JobStatus.SUCCEEDED: "EFFECTIVE",
    JobStatus.FAILED: "FAILED",
    JobStatus.REJECTED: "CANCELLED",
    JobStatus.CANCELLED: "CANCELLED",
    JobStatus.EXPIRED: "CANCELLED",
    JobStatus.BLOCKED: "MANUAL",
    JobStatus.MANUAL_REQUIRED: "MANUAL",
}


class TimelineEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    at: AwareDatetime
    status: ExecutionStatus
    detail: str


class ExecutionTask(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, alias_generator=to_camel, populate_by_name=True
    )
    id: UUID
    native_id: str | None
    platform: Literal["adq"]
    shop_id: str
    account_id: str
    plan_id: str
    goal_id: None = None
    decision_id: None = None
    budget_id: None = None
    approval_id: str | None
    action: str
    initiator: Literal["MANUAL", "AI", "RULE"]
    actor_id: str | None
    approver_id: str | None
    status: ExecutionStatus
    before: JobValue | None
    after: JobValue
    version: int
    created_at: AwareDatetime
    updated_at: AwareDatetime
    request_status: str
    response: JobReceipt | None
    verification: str
    failure: str | None
    safe_retry: Literal[False] = False
    idempotency_key: str
    history: tuple[TimelineEntry, ...]
    legacy_job_id: str


def execution_view(projection: JobProjection, *, platform: Literal["adq"]) -> ExecutionTask | None:
    job, receipt = projection.job, projection.job.execution_receipt
    # 人工开关播申请无广告账户，不伪造accountId或平台执行单来满足V8字段。
    if job.target_type != "AD_PLAN" or job.account_id is None:
        return None
    timeline = tuple(
        TimelineEntry(
            at=event.at,
            status="UNKNOWN"
            if event.control_event in {"SUBMIT_UNKNOWN", "READ_UNKNOWN"}
            else STATUSES[event.status],
            detail=event.detail,
        )
        for event in projection.events
    )
    if projection.unknown and (
        timeline[-1].status != "UNKNOWN"
        or (projection.last_checked_at is not None and projection.last_checked_at > timeline[-1].at)
    ):
        # 当前持久执行事实可能领先投影事件；仅补只读解释，不回写历史或冒充平台核验。
        timeline = (
            *timeline,
            TimelineEntry(
                at=projection.last_checked_at or job.updated_at,
                status="UNKNOWN",
                detail="结果未知，仅核对原请求与独立回读，不自动重发",
            ),
        )
    return ExecutionTask(
        id=projection.execution_id or UUID(job.job_id),
        native_id=receipt.platform_receipt_id if receipt else None,
        platform=platform,
        shop_id=job.shop_id,
        account_id=job.account_id,
        plan_id=job.target_id,
        approval_id=job.job_id if job.approval.state != "NOT_REQUESTED" else None,
        action=job.action_type,
        initiator="RULE" if job.source == "AUTOPILOT" else "MANUAL",
        actor_id=str(projection.requester_id) if projection.requester_id else None,
        approver_id=job.approval.actor_id,
        status="UNKNOWN" if projection.unknown else STATUSES[job.status],
        before=job.old_value,
        after=job.new_value,
        version=job.version,
        created_at=job.created_at,
        updated_at=job.updated_at,
        request_status="平台已受理"
        if receipt and receipt.accepted is True
        else "结果未知，仅核对原请求"
        if projection.unknown
        else "未取得受理证明",
        response=receipt,
        verification=projection.verification,
        failure=job.error.message if job.error else None,
        idempotency_key=job.idempotency_key,
        history=timeline,
        legacy_job_id=job.job_id,
    )
