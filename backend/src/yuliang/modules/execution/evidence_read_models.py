"""内部只读证据白名单；不是前端已冻结HTTP合同。"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from yuliang.modules.command.jobs import JobStatus


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class OrderEvidence(EvidenceModel):
    id: UUID
    status: Literal[
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
    current_attempt: int = Field(strict=True, gt=0)
    version: int = Field(strict=True, gt=0)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AttemptEvidence(EvidenceModel):
    id: UUID
    order_id: UUID
    attempt_no: int = Field(strict=True, gt=0)
    state: Literal["PENDING", "SENT", "ACK_ACCEPTED", "REJECTED", "UNKNOWN"]
    sent_at: AwareDatetime
    finished_at: AwareDatetime | None
    version: int = Field(strict=True, gt=0)


class ReceiptEvidence(EvidenceModel):
    id: UUID
    attempt_id: UUID
    source: Literal["PLATFORM", "SYSTEM"]
    accepted: bool | None = Field(strict=True)
    evidence_level: Literal["E0", "E1"]
    applied: bool = Field(strict=True)
    disposition: Literal[
        "APPLIED", "STALE_FENCE", "STALE_ATTEMPT", "STALE_VERSION", "STALE_EVIDENCE", "TERMINAL"
    ]
    created_at: AwareDatetime


class RecoveryEvidence(EvidenceModel):
    id: UUID
    attempt_id: UUID
    state: Literal["DUE", "WAITING", "DONE", "BLOCKED_AUTH", "MANUAL_REQUIRED"]
    outcome: Literal[
        "PENDING",
        "NOT_SENT",
        "TERMINAL",
        "STALE_GUARD",
        "RECONCILIATION_UNRESOLVED",
        "VERIFYING",
        "UNKNOWN",
        "AUTH_REVOKED",
        "INTEGRITY_REQUIRED",
        "RESUMED",
        "UNRECOGNIZED",
    ]
    checks: int = Field(strict=True, ge=0)
    next_due: AwareDatetime
    created_at: AwareDatetime
    updated_at: AwareDatetime


class RecoveryProofEvidence(EvidenceModel):
    id: UUID
    recovery_id: UUID
    attempt_id: UUID
    kind: Literal["NOT_SENT", "UNKNOWN", "TERMINAL", "STALE_GUARD", "AUTH_BLOCKED", "ERROR_BLOCKED"]
    evidence_level: Literal["E0", "E1"]
    network_attempts: int = Field(strict=True, ge=0)
    created_at: AwareDatetime


class ReadbackEvidence(EvidenceModel):
    id: UUID
    attempt_id: UUID
    created_at: AwareDatetime
    external_outcome: (
        Literal["ACCEPTED", "REJECTED", "UNKNOWN", "NOT_SENT", "READ_SUCCEEDED", "READ_FAILED"]
        | None
    )
    observed_at: AwareDatetime | None


class VerifiedFactEvidence(EvidenceModel):
    id: UUID
    action_type: Literal[
        "CHANGE_DAILY_BUDGET", "CHANGE_BID", "SET_ROI_TARGET", "PAUSE_AD_PLAN", "RESUME_AD_PLAN"
    ]
    verified_at: AwareDatetime


class ExecutionEvidence(EvidenceModel):
    job_id: UUID
    job_status: JobStatus
    job_version: int = Field(strict=True, gt=0)
    job_updated_at: AwareDatetime
    as_of: AwareDatetime
    orders: tuple[OrderEvidence, ...] = Field(max_length=1000)
    attempts: tuple[AttemptEvidence, ...] = Field(max_length=1000)
    receipts: tuple[ReceiptEvidence, ...] = Field(max_length=1000)
    recoveries: tuple[RecoveryEvidence, ...] = Field(max_length=1000)
    recovery_evidence: tuple[RecoveryProofEvidence, ...] = Field(max_length=1000)
    readbacks: tuple[ReadbackEvidence, ...] = Field(max_length=1000)
    verified_facts: tuple[VerifiedFactEvidence, ...] = Field(max_length=1000)
    warnings: tuple[str, ...] = (
        "INTERNAL_READ_MODEL_NO_HTTP_OR_FRONTEND_CONTRACT",
        "HISTORICAL_FACTS_NOT_CURRENT_PLATFORM_VALUES_OR_SEND_AUTHORIZATION",
        "READBACK_TRANSPORT_OBSERVATION_IS_NOT_PERSISTED_MATCH_CONCLUSION",
    )

    @model_validator(mode="after")
    def linked(self) -> "ExecutionEvidence":
        orders = {r.id for r in self.orders}
        attempts = {r.id for r in self.attempts}
        recoveries = {r.id for r in self.recoveries}
        if (
            len(orders) != len(self.orders)
            or len(attempts) != len(self.attempts)
            or any(r.order_id not in orders for r in self.attempts)
            or any(
                referenced not in attempts
                for referenced in (
                    [r.attempt_id for r in self.receipts]
                    + [r.attempt_id for r in self.recoveries]
                    + [r.attempt_id for r in self.recovery_evidence]
                    + [r.attempt_id for r in self.readbacks]
                )
            )
            or any(r.recovery_id not in recoveries for r in self.recovery_evidence)
        ):
            raise ValueError("执行证据关联不完整，不返回混合或截断证据")
        return self
