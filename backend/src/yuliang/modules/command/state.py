"""声明式任务生命周期；只归约已校验事实，外部IO/资金/审批授权由同事务参与者提供。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Never
from uuid import UUID

from yuliang.modules.command.jobs import (
    Action,
    CommandJob,
    JobApproval,
    JobError,
    JobHistory,
    JobReceipt,
    JobStatus,
    JobValue,
    MoneyValue,
    RiskSummary,
)
from yuliang.platform.errors import ApiError, ErrorCode


class JobEvent(StrEnum):
    CONFIRM = "CONFIRM"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    CANCEL = "CANCEL"
    EXPIRE = "EXPIRE"
    CLAIM = "CLAIM"
    ACCEPT = "ACCEPT"
    REJECT_SEND = "REJECT_SEND"
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    READ_UNKNOWN = "READ_UNKNOWN"
    CONFIRMED_NOT_EXECUTED = "CONFIRMED_NOT_EXECUTED"
    SAFE_RETRY = "SAFE_RETRY"
    UNSENT_REJECT = "UNSENT_REJECT"


@dataclass(frozen=True)
class Rule:
    sources: frozenset[JobStatus]
    destination: JobStatus
    detail: str


RULES = {
    JobEvent.CONFIRM: Rule(
        frozenset({JobStatus.DRAFT}), JobStatus.PENDING_APPROVAL, "已确认，等待审批"
    ),
    JobEvent.APPROVE: Rule(
        frozenset({JobStatus.PENDING_APPROVAL}), JobStatus.APPROVED, "审批通过，等待执行"
    ),
    JobEvent.REJECT: Rule(
        frozenset({JobStatus.PENDING_APPROVAL}), JobStatus.REJECTED, "审批已驳回"
    ),
    JobEvent.CANCEL: Rule(
        frozenset({JobStatus.DRAFT, JobStatus.PENDING_APPROVAL, JobStatus.APPROVED}),
        JobStatus.CANCELLED,
        "尚未领取，申请已取消",
    ),
    JobEvent.EXPIRE: Rule(
        frozenset({JobStatus.DRAFT, JobStatus.PENDING_APPROVAL, JobStatus.APPROVED}),
        JobStatus.EXPIRED,
        "未领取的申请已过期",
    ),
    JobEvent.CLAIM: Rule(frozenset({JobStatus.APPROVED}), JobStatus.EXECUTING, "执行内核已领取"),
    JobEvent.ACCEPT: Rule(
        frozenset({JobStatus.EXECUTING}), JobStatus.VERIFYING, "平台明确受理，等待独立核验"
    ),
    JobEvent.REJECT_SEND: Rule(frozenset({JobStatus.EXECUTING}), JobStatus.FAILED, "平台明确拒绝"),
    JobEvent.SUBMIT_UNKNOWN: Rule(
        frozenset({JobStatus.EXECUTING}), JobStatus.EXECUTING, "提交结果未知，仅核对原请求"
    ),
    JobEvent.MATCH: Rule(frozenset({JobStatus.VERIFYING}), JobStatus.SUCCEEDED, "独立回读已匹配"),
    JobEvent.MISMATCH: Rule(
        frozenset({JobStatus.VERIFYING}), JobStatus.FAILED, "核验窗口结束仍不匹配，待对账"
    ),
    JobEvent.READ_UNKNOWN: Rule(
        frozenset({JobStatus.VERIFYING}), JobStatus.VERIFYING, "受理后回读未知，继续核对"
    ),
    JobEvent.CONFIRMED_NOT_EXECUTED: Rule(
        frozenset({JobStatus.EXECUTING, JobStatus.VERIFYING}),
        JobStatus.FAILED,
        "已按持久证据确认未执行",
    ),
    JobEvent.SAFE_RETRY: Rule(
        frozenset({JobStatus.FAILED}), JobStatus.EXECUTING, "显式安全重试，沿用原申请并新增尝试"
    ),
    JobEvent.UNSENT_REJECT: Rule(
        frozenset({JobStatus.APPROVED}), JobStatus.FAILED, "发送前复核未通过，确认未发送"
    ),
}


@dataclass(frozen=True)
class StateFacts:
    risk: RiskSummary | None = None
    receipt: JobReceipt | None = None
    error: JobError | None = None
    evidence_level: Literal["E0", "E1", "E2"] = "E0"
    proof_ref: str | None = None
    retry_current: JobValue | None = None
    transport_unknown: bool = False
    transport_not_sent: bool = False
    retry_actions: frozenset[Action] = frozenset({"PAUSE_AD_PLAN", "RESUME_AD_PLAN"})


def _deny(message: str) -> Never:
    raise ApiError(ErrorCode.ILLEGAL_TRANSITION, message)


def evolve(
    job: CommandJob,
    event: JobEvent,
    facts: StateFacts,
    *,
    at: datetime,
    actor_id: UUID,
    requester_id: UUID,
) -> CommandJob:
    rule = RULES[event]
    if job.status not in rule.sources:
        _deny("当前状态不允许该生命周期动作")
    if at.tzinfo is None or at < job.updated_at:
        raise ValueError("需要不倒退的服务端带时区时间")
    if event == JobEvent.EXPIRE:
        if at < job.expires_at:
            _deny("申请尚未到期")
    elif (
        event
        in {
            JobEvent.CONFIRM,
            JobEvent.APPROVE,
            JobEvent.CLAIM,
            JobEvent.SAFE_RETRY,
            JobEvent.UNSENT_REJECT,
        }
        and at >= job.expires_at
    ):
        _deny("申请已过期")
    changes: dict[str, object] = {}
    if event == JobEvent.UNSENT_REJECT:
        if (
            facts.risk is None
            or facts.risk.checked_at != at
            or facts.risk.result != "BLOCKED"
            or not any(c.code == "AMOUNT_LIMIT" and not c.passed for c in facts.risk.checks)
            or facts.evidence_level != "E1"
            or not facts.proof_ref
            or facts.receipt is not None
            or facts.error is None
            or facts.error.retryable
            or facts.error.code != "RISK_BLOCKED"
        ):
            _deny("未发送终结需要本次额度拒绝、E1未发送证明与不可重试错误")
        changes.update(risk_summary=facts.risk, error=facts.error, execution_receipt=None)
    if event in {JobEvent.CONFIRM, JobEvent.APPROVE, JobEvent.CLAIM, JobEvent.SAFE_RETRY}:
        if (
            facts.risk is None
            or facts.risk.checked_at != at
            or facts.risk.result != "REVIEW_REQUIRED"
            or not facts.risk.checks
            or not all(check.passed for check in facts.risk.checks)
            or facts.risk.policy_version < 1
        ):
            raise ApiError(ErrorCode.RISK_BLOCKED, "需要本次事务实际复核的有效策略结论")
        if facts.risk.policy_version != job.risk_summary.policy_version:
            raise ApiError(ErrorCode.POLICY_VERSION_CHANGED, "策略版本变化，需要重新确认")
        changes["risk_summary"] = facts.risk
    if event == JobEvent.CONFIRM:
        changes["approval"] = JobApproval(state="PENDING")
    elif event in {JobEvent.APPROVE, JobEvent.REJECT}:
        if actor_id == requester_id or job.approval.state != "PENDING":
            _deny("审批须分离且申请处于待审批状态")
        changes["approval"] = JobApproval(
            state="APPROVED" if event == JobEvent.APPROVE else "REJECTED",
            actor_id=str(actor_id),
            decided_at=at,
            method="MANUAL",
            approved_version=job.version,
        )
    elif event in {JobEvent.CANCEL, JobEvent.EXPIRE}:
        if job.approval.state in {"PENDING", "APPROVED"}:
            changes["approval"] = JobApproval(state="REJECTED", decided_at=at)
    elif event == JobEvent.CLAIM and job.approval.state != "APPROVED":
        _deny("申请尚未取得有效审批")
    if event in {
        JobEvent.ACCEPT,
        JobEvent.MATCH,
        JobEvent.MISMATCH,
        JobEvent.READ_UNKNOWN,
        JobEvent.REJECT_SEND,
        JobEvent.SUBMIT_UNKNOWN,
        JobEvent.CONFIRMED_NOT_EXECUTED,
    }:
        receipt = facts.receipt
        system_unknown = (
            event == JobEvent.SUBMIT_UNKNOWN and facts.transport_unknown and receipt is None
        )
        system_not_sent = (
            event == JobEvent.CONFIRMED_NOT_EXECUTED
            and facts.transport_not_sent
            and job.status == JobStatus.EXECUTING
            and job.execution_receipt is None
            and receipt is None
            and facts.evidence_level == "E1"
            and facts.error is not None
            and facts.error.code == "NOT_SENT"
            and not facts.error.retryable
        )
        if not facts.proof_ref or (receipt is None and not system_unknown and not system_not_sent):
            _deny("转移需要已持久化的原请求/回读证据")
        if event in {
            JobEvent.ACCEPT,
            JobEvent.MATCH,
            JobEvent.MISMATCH,
            JobEvent.READ_UNKNOWN,
        } and (receipt is None or receipt.accepted is not True):
            _deny("缺少平台明确受理证据")
        if event == JobEvent.MATCH and (
            receipt is None or receipt.verification != "MATCHED" or facts.evidence_level != "E2"
        ):
            _deny("成功需要E2独立匹配证据")
        if event == JobEvent.MISMATCH and (receipt is None or receipt.verification != "MISMATCH"):
            _deny("核验不匹配事实不足")
        if event == JobEvent.READ_UNKNOWN and (
            receipt is None or receipt.verification != "UNKNOWN"
        ):
            _deny("缺少回读未知事实")
        if (
            event == JobEvent.SUBMIT_UNKNOWN
            and not system_unknown
            and (
                receipt is None or receipt.accepted is not None or receipt.verification != "UNKNOWN"
            )
        ):
            _deny("提交未知不能声明平台已受理")
        if event == JobEvent.REJECT_SEND and (
            receipt is None or receipt.accepted is not False or facts.evidence_level != "E1"
        ):
            _deny("明确拒绝需要E1未执行证据")
        if event == JobEvent.CONFIRMED_NOT_EXECUTED and facts.evidence_level != "E1":
            _deny("定性未执行需要持久E1证据")
        changes["execution_receipt"] = receipt
        if event in {JobEvent.REJECT_SEND, JobEvent.MISMATCH, JobEvent.CONFIRMED_NOT_EXECUTED}:
            if facts.error is None:
                _deny("失败需要已分类的错误")
            changes["error"] = facts.error
        elif event == JobEvent.SUBMIT_UNKNOWN:
            changes["error"] = JobError(
                code="RESULT_UNKNOWN", message="提交结果未知，请核对原请求", retryable=False, at=at
            )
        else:
            changes["error"] = None
    if event == JobEvent.SAFE_RETRY:
        if (
            job.error is None
            or not job.error.retryable
            or job.action_type not in facts.retry_actions
            or facts.evidence_level != "E1"
            or not facts.proof_ref
            or facts.retry_current != job.old_value
        ):
            _deny("不满足显式安全重试的现值/白名单/未执行依据")
        if (
            isinstance(job.old_value, MoneyValue)
            and isinstance(job.new_value, MoneyValue)
            and job.new_value.amount_minor > job.old_value.amount_minor
        ):
            _deny("资金增加动作默认不允许安全重试")
        changes.update(execution_receipt=None, error=None)
    return CommandJob.model_validate(
        job.model_dump()
        | changes
        | {
            "status": rule.destination,
            "version": job.version + 1,
            "updated_at": at,
            "history": (
                *job.history,
                JobHistory(status=rule.destination, at=at, detail=rule.detail),
            ),
        }
    )
