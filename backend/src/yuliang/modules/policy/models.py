"""策略阈值无生产默认额度；检查结果只保存安全说明和证明引用。"""

from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from yuliang.modules.command.jobs import (
    CommandJob,
    CreateJobRequest,
    JobValue,
    RiskCheck,
    RiskSummary,
)
from yuliang.platform.db import VerifiedIdentity


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    schema_version: Literal["risk-policy-v1"] = "risk-policy-v1"
    currency: Literal["CNY"] = "CNY"
    daily_budget_minor: int = Field(strict=True, gt=0, le=9007199254740991)
    target_roi: Decimal = Field(gt=0, le=10000, max_digits=10, decimal_places=4)
    max_change_percent: Decimal = Field(gt=0, le=100, max_digits=6, decimal_places=3)
    cooldown_minutes: int = Field(strict=True, ge=0, le=1440)
    max_changes_per_day: int = Field(strict=True, gt=0, le=10000)
    mode: Literal["MANUAL", "COPILOT", "AUTOPILOT"]


class RiskStage(StrEnum):
    CREATE = "CREATE"
    CONFIRM = "CONFIRM"
    APPROVE = "APPROVE"
    EXECUTE = "EXECUTE"


class RiskContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)
    identity: VerifiedIdentity
    requester: VerifiedIdentity
    job_id: UUID
    job: CommandJob | None
    request: CreateJobRequest
    target: dict[str, Any]
    current_value: JobValue | None
    current_proof: dict[str, Any]
    stage: RiskStage
    policy_id: UUID | None
    policy_version: int
    config: PolicyConfig | None
    checked_at: AwareDatetime
    stored: dict[str, Any] | None = None


class CheckEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    check: RiskCheck
    # 仅程序控制的证明引用；不能把供应商原文或请求自由文本放入该字段。
    evidence: dict[str, Any]


class RiskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    assessment_id: UUID
    summary: RiskSummary
    expires_at: AwareDatetime

    def require_allowed(self) -> None:
        """组件准入错误供后续HTTP/同事务参与者使用，不把失败总结当执行资格。"""
        from yuliang.platform.errors import ApiError, ErrorCode

        failures = {item.code for item in self.summary.checks if not item.passed}
        if "POLICY_VERSION" in failures:
            raise ApiError(ErrorCode.POLICY_VERSION_CHANGED, "策略已变化，需要重新创建并确认")
        if "INTENT_BINDING" in failures:
            raise ApiError(ErrorCode.VERSION_CONFLICT, "冻结指令不一致，需要重新创建并确认")
        if self.summary.result != "REVIEW_REQUIRED" or failures:
            raise ApiError(ErrorCode.RISK_BLOCKED, "当前条件不允许推进申请")
