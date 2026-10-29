"""生成业务七态和独立提交态；客户端不可自报费用或供应商成功。"""

import hashlib
import json
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Status = Literal[
    "QUEUED", "RUNNING", "WAITING_PROVIDER", "WAITING_REVIEW", "SUCCEEDED", "FAILED", "CANCELLED"
]
SubmitState = Literal["NOT_SENT", "SENT", "ACKNOWLEDGED", "UNKNOWN"]


class GenerationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    requestKey: str
    runId: str
    stepCode: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    expectedRunVersion: int = Field(ge=1)
    expectedAttempt: int = Field(ge=1)
    documentId: str
    documentVersion: int = Field(ge=1)
    kind: Literal["IMAGE", "GRAPHIC", "VIDEO", "VIDEO_EDIT", "SCRIPT"]
    provider: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,39}$")
    priceKey: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,79}$")
    prompt: str = Field(min_length=1, max_length=20000)
    durationSeconds: int | None = Field(default=None, strict=True, ge=1, le=600)

    @model_validator(mode="after")
    def identifiers(self) -> Self:
        for value in (self.requestKey, self.runId, self.documentId):
            UUID(value)
        if not self.prompt.strip():
            raise ValueError("生成提示不可为空")
        # 时长只属于视频输入；图片/图文不得借时长伪造计价单位。
        if self.kind != "VIDEO" and self.durationSeconds is not None:
            raise ValueError("仅视频生成可声明时长")
        return self

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()


class GenerationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    input: GenerationInput
    quoteId: str
    quoteHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    maximumMinor: int = Field(strict=True, ge=0)
    currency: Literal["CNY", "USD"]

    @model_validator(mode="after")
    def identifier(self) -> Self:
        UUID(self.quoteId)
        return self
