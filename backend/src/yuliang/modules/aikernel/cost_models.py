"""首次付费前的报价/确认/一次性许可协议；未知账单不等于零。"""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class FeeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class BillableRequest(FeeModel):
    request_key: UUID
    kind: Literal["LLM", "GENERATION"]
    scenario: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,39}$")
    price_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,79}$")
    input_units: int = Field(strict=True, ge=0, le=1000000000)
    output_units: int = Field(strict=True, ge=0, le=1000000000)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class CostQuote(FeeModel):
    quote_id: UUID
    request_key: UUID
    max_cost_minor: int = Field(ge=0)
    currency: Literal["CNY", "USD"]
    expires_at: datetime
    quote_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CallPermit(FeeModel):
    permit_id: UUID
    request_key: UUID
    token: SecretStr = Field(min_length=32)


class BillingOutcome(FeeModel):
    state: Literal["UNKNOWN", "KNOWN"] = "UNKNOWN"
    amount_minor: int | None = Field(default=None, strict=True, ge=0, le=9223372036854775807)
    currency: Literal["CNY", "USD"] | None = None
    proof_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def known_requires_receipt(self) -> "BillingOutcome":
        values = (self.amount_minor, self.currency, self.proof_hash)
        if self.state == "KNOWN" and any(value is None for value in values):
            raise ValueError("已知费用必须有金额、币种和回执摘要")
        if self.state == "UNKNOWN" and any(value is not None for value in values):
            raise ValueError("未知费用不能填零或伪造账单")
        return self
