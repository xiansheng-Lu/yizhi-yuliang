from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=40)

    @field_validator("name")
    @classmethod
    def trimmed_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value.encode("utf-16-le")) > 80:
            raise ValueError("项目名称需要1至40字符")
        return value


class ProjectAssignment(StrictModel):
    projectId: UUID | None


class ReportingScope(StrictModel):
    shop_id: UUID | Literal["all"]
    live_session_id: UUID | Literal["all"]
    connector_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
    date: date
    timezone: Literal["Asia/Shanghai"]


class MessageSend(StrictModel):
    conversationId: UUID | None = None
    projectId: UUID | None = None
    text: str = Field(min_length=1, max_length=500)
    source: Literal["PC_MANUAL", "MOBILE_COMMAND"]
    scope: ReportingScope
    idempotencyKey: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")

    @field_validator("text")
    @classmethod
    def trimmed_text(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value.encode("utf-16-le")) > 1000:
            raise ValueError("消息需要1至500字符")
        return value


class JobAttachment(StrictModel):
    messageId: UUID
    jobId: UUID
