"""内部队列信封只承载必要ID，不承载凭据、SQL或平台请求体。"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class WorkMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    event_id: UUID
    tenant_id: int = Field(strict=True, gt=0, le=9223372036854775807)
    job_id: UUID
    membership_id: UUID
    user_id: UUID


def parse_message(value: dict[str, Any]) -> WorkMessage:
    try:
        return WorkMessage.model_validate(value)
    except ValidationError:
        raise ValueError("消息结构不合法，仅允许必要ID字段") from None
