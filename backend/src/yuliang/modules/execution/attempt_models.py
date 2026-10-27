"""通用业务引用不要求伪造命令申请；当前已接入源只有COMMAND_JOB。"""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from yuliang.modules.execution.guards import GuardLease


class BusinessKind(StrEnum):
    COMMAND_JOB = "COMMAND_JOB"
    PLAN_CHANGE = "PLAN_CHANGE"
    CONFIG_TASK = "CONFIG_TASK"
    BUDGET_REQUEST = "BUDGET_REQUEST"
    PLATFORM_COMMAND = "PLATFORM_COMMAND"


class BusinessRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: BusinessKind
    id: UUID


class ExecutionAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    execution_id: UUID
    attempt_id: UUID
    business: BusinessRef
    attempt_no: int = Field(strict=True, gt=0)
    outbound_id: UUID
    platform_request_key: UUID
    execution_status: Literal[
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
    attempt_state: Literal["PENDING", "SENT", "ACK_ACCEPTED", "REJECTED", "UNKNOWN"]
    execution_version: int = Field(strict=True, gt=0)
    guard: GuardLease
    send_allowed: bool = Field(strict=True)
    dispatch_secret: SecretStr | None = Field(default=None, exclude=True, repr=False)
