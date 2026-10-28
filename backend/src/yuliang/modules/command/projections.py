"""单一已持久任务投影；UNKNOWN是事实表达，不能扩为V1第十三态。"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.command.jobs import CommandJob, JobError, JobHistory, JobStatus, JobValue


class StoredJobEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: JobStatus
    at: AwareDatetime
    detail: str
    control_event: str | None = None


class JobProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    job: CommandJob
    execution_id: UUID | None = None
    requester_id: UUID | None = None
    unknown: bool = False
    verification: Literal["PENDING", "MATCHED", "MISMATCH", "UNKNOWN"]
    last_checked_at: AwareDatetime | None = None
    # 只表示本次动作已确认的结果，不能当成当前平台现值或覆盖资源镜像。
    confirmed_change: JobValue | None = None
    events: tuple[StoredJobEvent, ...]


def project_job(
    document: dict[str, Any],
    events: tuple[StoredJobEvent, ...],
    *,
    execution_id: UUID | None = None,
    execution_status: str | None = None,
    requester_id: UUID | None = None,
    last_checked_at: datetime | None = None,
) -> JobProjection:
    history = tuple(JobHistory(status=e.status, at=e.at, detail=e.detail) for e in events)
    job = CommandJob.model_validate(document | {"history": history})
    receipt = job.execution_receipt
    inflight = job.status in {JobStatus.EXECUTING, JobStatus.VERIFYING}
    unknown = inflight and (
        execution_status == "UNKNOWN"
        or (job.error is not None and job.error.code == "RESULT_UNKNOWN")
        or (receipt is not None and receipt.verification == "UNKNOWN")
    )
    if execution_status == "UNKNOWN" and not inflight:
        raise ValueError("执行未知不能被投影成业务终态")
    if unknown:
        accepted = receipt is not None and receipt.accepted is True
        expected = JobStatus.VERIFYING if accepted else JobStatus.EXECUTING
        if job.status != expected:
            raise ValueError("申请阶段与明确受理事实不一致")
        changes: dict[str, Any] = {}
        if receipt is not None:
            changes["execution_receipt"] = receipt.model_copy(update={"verification": "UNKNOWN"})
        # 无响应时保留null，不能为了展示UNKNOWN造PLATFORM/SYSTEM公共回执。
        if accepted:
            changes["error"] = None
        elif job.error is None or job.error.code != "RESULT_UNKNOWN" or job.error.retryable:
            changes["error"] = JobError(
                code="RESULT_UNKNOWN",
                message="提交结果未知，仅核对原请求",
                retryable=False,
                at=job.updated_at,
            )
        job = CommandJob.model_validate(job.model_dump() | changes)
    receipt = job.execution_receipt
    return JobProjection(
        job=job,
        execution_id=execution_id,
        requester_id=requester_id,
        unknown=unknown,
        verification="UNKNOWN" if unknown else receipt.verification if receipt else "PENDING",
        last_checked_at=last_checked_at,
        confirmed_change=receipt.observed_value
        if job.status == JobStatus.SUCCEEDED and receipt is not None
        else None,
        events=events,
    )


def load_projection(session: Session, row: dict[str, Any]) -> JobProjection:
    """调用端先完成当前权限检查；一次SQL使用同一MVCC快照，不扩大更新权限。"""
    snapshot = (
        session.execute(
            text("""SELECT j.*,
            (SELECT coalesce(jsonb_agg(jsonb_build_object('status',e.to_status,'at',e.created_at,
             'detail',e.detail,'control_event',e.control_event) ORDER BY e.id),'[]'::jsonb)
             FROM job_events e WHERE e.job_id=j.id) AS projection_events,
            (SELECT jsonb_build_object('id',o.id,'status',o.status) FROM execution_orders o
             WHERE o.biz_type='COMMAND_JOB' AND o.command_job_id=j.id) AS projection_execution,
            (SELECT max(p.created_at) FROM execution_recovery_evidence p WHERE p.job_id=j.id)
             AS projection_checked FROM command_jobs j WHERE j.id=:id"""),
            {"id": row["id"]},
        )
        .mappings()
        .one()
    )
    row = dict(snapshot)
    events = tuple(StoredJobEvent.model_validate(event) for event in row["projection_events"])
    execution, checked = row["projection_execution"], row["projection_checked"]
    receipt = row["document"].get("execution_receipt")
    verified = (
        datetime.fromisoformat(receipt["verified_at"])
        if receipt and receipt.get("verified_at")
        else None
    )
    last = max(filter(None, (checked, verified)), default=None)
    return project_job(
        row["document"],
        events,
        execution_id=execution["id"] if execution else None,
        execution_status=execution["status"] if execution else None,
        requester_id=row["created_by"],
        last_checked_at=last,
    )
