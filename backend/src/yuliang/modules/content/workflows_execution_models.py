"""SOP人工执行动作输入；受控字段，不接受任意JSON路径。"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    actionKey: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    expectedRunVersion: int = Field(ge=1, le=2147483646)
    expectedAttempt: int = Field(ge=1, le=2147483646)


class Execute(Action):
    documentId: str
    documentVersion: int = Field(ge=1, le=2147483646)
    patch: dict[str, Any] = Field(default_factory=dict)
    reviewNote: str = Field(default="", max_length=1000)
    deriveKind: Literal["planning", "scripts", "graphics", "videos", "editing"] | None = None

    @model_validator(mode="after")
    def bounded(self) -> "Execute":
        UUID(self.documentId)
        if not set(self.patch) <= {"brief", "body", "scenes", "prompt"}:
            raise ValueError("人工候选仅允许brief/body/scenes/prompt字段")
        return self


class Accept(Action):
    candidateId: str
    documentVersion: int = Field(ge=1, le=2147483646)

    @model_validator(mode="after")
    def identifier(self) -> "Accept":
        UUID(self.candidateId)
        return self


class Skip(Action):
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def nonblank(self) -> "Skip":
        if not self.reason.strip():
            raise ValueError("跳过原因不可空白")
        return self


class Redo(Action):
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def nonblank(self) -> "Redo":
        if not self.reason.strip():
            raise ValueError("重做原因不可空白")
        return self
