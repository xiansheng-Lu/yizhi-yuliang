"""接口账只持久数据库操作快照，不包含HTTP头、Cookie或供应商请求。"""

from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, model_validator

from yuliang.modules.connectors.redaction import canonical


class ApiOperation(StrEnum):
    CREATE = "JOB_CREATE"
    CONFIRM = "JOB_CONFIRM"
    APPROVE = "JOB_APPROVE"
    REJECT = "JOB_REJECT"
    CANCEL = "JOB_CANCEL"
    SAFE_RETRY = "JOB_SAFE_RETRY"


class MutationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    job_id: UUID
    expected_version: int = Field(strict=True, gt=0)


class ResponseSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: int = Field(strict=True)
    body: dict[str, Any]

    @model_validator(mode="after")
    def bounded_domain_result(self) -> Self:
        if (
            not (200 <= self.status < 300 or 400 <= self.status < 500)
            or len(canonical(self.body)) > 120000
        ):
            raise ValueError("只保存有界业务结果或确定性4xx，不包含传输响应头")
        return self


class OwnershipTicket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    record_id: UUID
    secret: SecretStr = Field(exclude=True, repr=False)


class IdempotencyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    record_id: UUID
    state: Literal["ACQUIRED", "IN_FLIGHT", "COMPLETED"]
    request_id: UUID
    lease_until: AwareDatetime
    ticket: OwnershipTicket | None = Field(default=None, exclude=True, repr=False)
    response: ResponseSnapshot | None = None
