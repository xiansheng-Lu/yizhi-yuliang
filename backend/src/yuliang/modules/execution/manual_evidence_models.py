"""人工陈述只作注记；不接受平台受理、状态或金额声明。"""

import re
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

NOTE_KINDS = Literal["FOLLOW_UP", "SUPPORT_REFERENCE", "MANUAL_REVIEW"]
SECRET_PATTERN = re.compile(
    r"bearer\s+\S+|(?:access[_ -]?token|refresh[_ -]?token|api[_ -]?key|appsecret|"
    r"password|passwd|token|secret|authorization|cookie|session|csrf|client[_ -]?secret|"
    r"密钥|口令|密码|授权码)\s*[:=：]\s*\S+|"
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}|-----BEGIN",
    re.I,
)


class ManualEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    request_key: UUID
    attempt_id: UUID
    receipt_id: UUID | None = None
    recovery_id: UUID | None = None
    note_kind: NOTE_KINDS
    reference_code: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
    note_text: str = Field(min_length=1, max_length=2000)

    @field_validator("note_text", "reference_code")
    @classmethod
    def safe_statement(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).strip()
        if (
            not normalized
            or len(normalized) > 2000
            or any(ord(c) < 32 and c not in "\n\t" for c in normalized)
        ):
            raise ValueError("人工注记含不允许的控制字符或空白")
        if SECRET_PATTERN.search(normalized):
            raise ValueError("人工注记含已识别的凭据样式，请移除后重试")
        return normalized


class ManualEvidenceNote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    id: UUID
    job_id: UUID
    attempt_id: UUID
    receipt_id: UUID | None
    recovery_id: UUID | None
    note_kind: NOTE_KINDS
    reference_code: str
    note_text: str
    source: Literal["HUMAN_STATEMENT"]
    evidence_level: Literal["UNVERIFIED_NOTE"]
    created_at: AwareDatetime
