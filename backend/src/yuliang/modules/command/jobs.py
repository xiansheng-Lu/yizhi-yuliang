"""V1蛇形任务契约；申请输入不能声明旧值、风险、审批或执行结果。"""

import math
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictJobModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)


class JobStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    BLOCKED = "BLOCKED"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"


type Action = Literal[
    "CHANGE_DAILY_BUDGET",
    "CHANGE_BID",
    "SET_ROI_TARGET",
    "PAUSE_AD_PLAN",
    "RESUME_AD_PLAN",
    "REQUEST_LIVE_START",
    "REQUEST_LIVE_END",
]


class MoneyValue(StrictJobModel):
    kind: Literal["MONEY"] = "MONEY"
    currency: Literal["CNY"] = "CNY"
    amount_minor: int = Field(strict=True, ge=0, le=9007199254740991)


class RatioValue(StrictJobModel):
    kind: Literal["RATIO"] = "RATIO"
    value: float = Field(strict=True, ge=0, allow_inf_nan=False)


class BooleanValue(StrictJobModel):
    kind: Literal["BOOLEAN"] = "BOOLEAN"
    value: bool = Field(strict=True)


class HandoffValue(StrictJobModel):
    kind: Literal["HANDOFF"] = "HANDOFF"
    value: Literal["START", "END"]


type JobValue = Annotated[
    MoneyValue | RatioValue | BooleanValue | HandoffValue, Field(discriminator="kind")
]


def matches(action: Action, value: JobValue, *, old: bool = False) -> bool:
    if action in {"CHANGE_DAILY_BUDGET", "CHANGE_BID"}:
        return isinstance(value, MoneyValue) and (old or value.amount_minor > 0)
    if action == "SET_ROI_TARGET":
        return (
            isinstance(value, RatioValue)
            and math.isfinite(value.value)
            and (old or value.value > 0)
        )
    if action in {"PAUSE_AD_PLAN", "RESUME_AD_PLAN"}:
        return isinstance(value, BooleanValue) and (
            old or value.value == (action == "RESUME_AD_PLAN")
        )
    return isinstance(value, HandoffValue) and value.value == (
        "START" if action == "REQUEST_LIVE_START" else "END"
    )


class CreateJobRequest(StrictJobModel):
    source: Literal["PC_MANUAL", "MOBILE_COMMAND"]
    shop_id: UUID
    target_type: Literal["AD_PLAN", "LIVE_SESSION"]
    target_id: UUID
    action_type: Action
    new_value: JobValue
    intent: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")

    @field_validator("intent")
    @classmethod
    def intent_length(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value.encode("utf-16-le")) > 1000:
            raise ValueError("意图需1至500字符")
        return value

    @model_validator(mode="after")
    def action_value(self) -> Self:
        if (self.action_type in {"REQUEST_LIVE_START", "REQUEST_LIVE_END"}) != (
            self.target_type == "LIVE_SESSION"
        ) or not matches(self.action_type, self.new_value):
            raise ValueError("目标类型或新值不符合动作")
        return self


class RiskCheck(StrictJobModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,59}$")
    passed: bool = Field(strict=True)
    message: str = Field(min_length=1, max_length=300)


class RiskSummary(StrictJobModel):
    source: Literal["SERVER"] = "SERVER"
    result: Literal["REVIEW_REQUIRED", "BLOCKED", "MANUAL_ONLY"]
    level: Literal["LOW", "MEDIUM", "HIGH"]
    checks: tuple[RiskCheck, ...] = Field(min_length=1, max_length=64)
    policy_version: int = Field(strict=True, ge=0)
    checked_at: AwareDatetime

    @model_validator(mode="after")
    def truthful(self) -> Self:
        if (self.result == "REVIEW_REQUIRED" and not all(c.passed for c in self.checks)) or (
            self.result == "BLOCKED" and all(c.passed for c in self.checks)
        ):
            raise ValueError("风险结论与实际检查不一致")
        return self


class JobApproval(StrictJobModel):
    state: Literal["NOT_REQUESTED", "PENDING", "APPROVED", "REJECTED"] = "NOT_REQUESTED"
    actor_id: str | None = Field(default=None, min_length=1)
    decided_at: AwareDatetime | None = None
    method: Literal["MANUAL", "SERVER_POLICY"] | None = None
    approved_version: int | None = Field(default=None, strict=True, gt=0)

    @model_validator(mode="after")
    def decision(self) -> Self:
        fields = (self.actor_id, self.decided_at, self.method, self.approved_version)
        if (
            (self.state == "APPROVED" and any(v is None for v in fields))
            or (self.state in {"NOT_REQUESTED", "PENDING"} and any(v is not None for v in fields))
            or (
                self.state == "REJECTED"
                and (
                    self.decided_at is None
                    or (
                        self.method == "MANUAL"
                        and (self.actor_id is None or self.approved_version is None)
                    )
                )
            )
        ):
            raise ValueError("审批状态与决定字段不一致")
        return self


class JobReceipt(StrictJobModel):
    receipt_id: str = Field(min_length=1)
    source: Literal["PLATFORM"] = "PLATFORM"
    platform_receipt_id: str | None
    accepted: bool | None = Field(strict=True)
    received_at: AwareDatetime | None
    verification: Literal["PENDING", "MATCHED", "MISMATCH", "UNKNOWN"]
    observed_value: JobValue | None
    verified_at: AwareDatetime | None

    @model_validator(mode="after")
    def matched(self) -> Self:
        if self.verification == "MATCHED" and (
            self.accepted is not True or self.observed_value is None or self.verified_at is None
        ):
            raise ValueError("匹配核验需要明确受理、观测值与核验时间")
        return self


class JobError(StrictJobModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,59}$")
    message: str = Field(min_length=1, max_length=300)
    retryable: bool = Field(strict=True)
    at: AwareDatetime


class JobHistory(StrictJobModel):
    status: JobStatus
    at: AwareDatetime
    detail: str = Field(min_length=1, max_length=300)


class CommandJob(StrictJobModel):
    schema_version: Literal["1.0"] = "1.0"
    job_id: str = Field(min_length=1)
    source: Literal["AUTOPILOT", "PC_MANUAL", "MOBILE_COMMAND"]
    tenant_id: str = Field(pattern=r"^[1-9][0-9]*$")
    shop_id: str = Field(min_length=1)
    account_id: str | None
    live_session_id: str | None
    target_type: Literal["AD_PLAN", "LIVE_SESSION"]
    target_id: str = Field(min_length=1)
    action_type: Action
    old_value: JobValue | None
    new_value: JobValue
    intent: str = Field(min_length=1, max_length=500)
    status: JobStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime
    expires_at: AwareDatetime
    risk_summary: RiskSummary
    approval: JobApproval
    execution_receipt: JobReceipt | None
    error: JobError | None
    version: int = Field(strict=True, gt=0)
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    mode: Literal["API"] = "API"
    history: tuple[JobHistory, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        handoff = self.action_type in {"REQUEST_LIVE_START", "REQUEST_LIVE_END"}
        if (
            handoff != (self.target_type == "LIVE_SESSION")
            or not matches(self.action_type, self.new_value)
            or (
                self.old_value is not None
                and not matches(self.action_type, self.old_value, old=True)
            )
            or (not handoff and (self.account_id is None or self.old_value is None))
            or (handoff and (self.live_session_id != self.target_id or self.old_value is not None))
            or self.created_at > self.updated_at
            or self.expires_at <= self.created_at
            or self.history[-1].status != self.status
            or any(a.at > b.at for a, b in zip(self.history, self.history[1:], strict=False))
            or (self.status == JobStatus.BLOCKED and self.risk_summary.result != "BLOCKED")
            or (
                self.status == JobStatus.MANUAL_REQUIRED
                and self.risk_summary.result != "MANUAL_ONLY"
            )
        ):
            raise ValueError("任务归属、值、风险或历史不一致")
        if self.status == JobStatus.SUCCEEDED and (
            self.execution_receipt is None
            or self.execution_receipt.accepted is not True
            or self.execution_receipt.verification != "MATCHED"
            or self.execution_receipt.observed_value is None
            or self.execution_receipt.observed_value.kind != self.new_value.kind
        ):
            raise ValueError("成功必须有平台明确受理和独立匹配回执")
        return self
