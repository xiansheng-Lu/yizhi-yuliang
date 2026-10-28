"""独立维护计划字段口径；SDK类型不是币种、单位或状态映射依据。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from yuliang.modules.connectors.plan_protocol import PlanLevel


class FieldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,499}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlanMeasureProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    id: UUID = Field(default_factory=uuid4)
    source_id: UUID
    binding_version: int = Field(strict=True, ge=1)
    version: int = Field(strict=True, ge=1)
    level: PlanLevel
    currency: Literal["CNY"]
    budget_field: Literal["daily_budget", "total_budget"]
    budget_unit: Literal["CNY_MINOR", "CNY_YUAN_INTEGER"]
    bid_unit: Literal["CNY_MINOR", "CNY_YUAN_INTEGER"] | None
    billing_events: tuple[str, ...] = ()
    optimization_goals: tuple[str, ...] = ()
    roi_scale: Literal["RATIO", "PERCENT"] | None
    roi_attribution: str | None = Field(default=None, min_length=1, max_length=180)
    source_epoch: Literal["SECONDS", "MILLISECONDS"]
    source_timezone: Literal["UTC"]
    status_map: dict[str, Literal["RUNNING", "PAUSED", "REVIEW", "FAILED", "COMPLETED", "UNKNOWN"]]
    evidence: dict[str, FieldEvidence]
    checked_by: str = Field(min_length=1, max_length=120)
    checked_at: AwareDatetime
    expires_at: AwareDatetime
    max_age_seconds: int = Field(strict=True, ge=1, le=86400)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        required = {"budget", "currency", "source_time", "status"}
        if self.level == "ADGROUP":
            required.add("bid")
            if self.bid_unit is None or not self.billing_events or not self.optimization_goals:
                raise ValueError("广告组出价单位未确认")
        elif self.bid_unit is not None:
            raise ValueError("campaign模型无bid_amount，不造0出价")
        if self.roi_scale is not None:
            required.add("roi")
            if not self.roi_attribution:
                raise ValueError("ROI必须明确归因口径")
        elif self.roi_attribution is not None:
            raise ValueError("ROI归因缺少倍率")
        if (
            not required <= self.evidence.keys()
            or not self.status_map
            or self.expires_at <= self.checked_at
        ):
            raise ValueError("口径依据缺少逐字段出处或有效期")
        return self


def measure_plan(
    values: dict[str, object],
    proof: PlanMeasureProof,
    *,
    source_id: UUID,
    binding_version: int,
    fetched_at: datetime,
    now: datetime,
) -> tuple[dict[str, object] | None, list[str]]:
    gaps = []
    if (
        proof.source_id != source_id
        or proof.binding_version != binding_version
        or values.get("level") != proof.level
    ):
        gaps.append("SOURCE_OR_BINDING_PROOF_MISMATCH")
    if not proof.checked_at <= now < proof.expires_at:
        gaps.append("MEASURE_PROOF_NOT_CURRENT")
    if not fetched_at <= now <= fetched_at + timedelta(seconds=proof.max_age_seconds):
        gaps.append("OBSERVATION_NOT_CURRENT")
    if values.get("is_deleted") is not False:
        gaps.append("DELETION_STATE_NOT_CONFIRMED_ACTIVE")
    if proof.level == "CAMPAIGN":
        gaps.append("CAMPAIGN_HAS_NO_OFFICIAL_BID_FIELD_FOR_FRONTEND_REQUIRED_BID")
    if proof.level == "ADGROUP" and (
        values.get("billing_event") not in proof.billing_events
        or values.get("optimization_goal") not in proof.optimization_goals
    ):
        gaps.append("BID_OR_ROI_APPLICABILITY_NOT_CONFIRMED")
    status = proof.status_map.get(str(values.get("configured_status")))
    if status is None:
        gaps.append("STATUS_MAPPING_MISSING")
    measured: dict[str, object] = {
        "status": status,
        "targetRoi": None,
        "currency": proof.currency,
        "budgetSourceField": proof.budget_field,
        "roiAttribution": proof.roi_attribution,
    }
    for key, source, unit in (
        ("budgetMinor", proof.budget_field, proof.budget_unit),
        ("bidMinor", "bid_amount", proof.bid_unit),
    ):
        value = values.get(source)
        if not isinstance(value, str) or not value.isascii() or not value.isdigit() or unit is None:
            gaps.append(f"{key}_SOURCE_MISSING")
            continue
        number = int(value) * (100 if unit == "CNY_YUAN_INTEGER" else 1)
        if number > 9007199254740991:
            gaps.append(f"{key}_JS_SAFE_INTEGER_EXCEEDED")
        measured[key] = number
    raw_time = values.get("last_modified_time")
    try:
        if not isinstance(raw_time, str) or not raw_time.isascii() or not raw_time.isdigit():
            raise ValueError("missing")
        epoch = int(raw_time)
        updated = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            milliseconds=epoch if proof.source_epoch == "MILLISECONDS" else epoch * 1000
        )
        if updated > fetched_at:
            raise ValueError("future")
        measured["updatedAt"] = updated.isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        gaps.append("SOURCE_UPDATED_TIME_INVALID")
    raw_roi = values.get("deep_conversion_worth_rate")
    if raw_roi is not None:
        if proof.roi_scale is None:
            gaps.append("ROI_MEASURE_UNCONFIRMED")
        else:
            ratio = Decimal(str(raw_roi)) / (100 if proof.roi_scale == "PERCENT" else 1)
            measured["targetRoi"] = float(ratio)
    return (None if gaps else measured), gaps
