"""参数核验只比较规范值，效果ROI不能冒充目标ROI。比较本身不是E2或状态转移。"""

import math
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from yuliang.modules.command.jobs import Action, BooleanValue, JobValue, MoneyValue, RatioValue

PARAMETER_FIELDS = {
    "CHANGE_DAILY_BUDGET": "budget_minor",
    "CHANGE_BID": "bid_minor",
    "SET_ROI_TARGET": "target_roi",
    "PAUSE_AD_PLAN": "enabled",
    "RESUME_AD_PLAN": "enabled",
}
FORBIDDEN_FIELDS = frozenset(
    {
        "access_token",
        "refresh_token",
        "token",
        "password",
        "secret",
        "authorization",
        "roi",
        "actual_roi",
        "effect_roi",
        "performance_roi",
        "return_on_ad_spend",
    }
)


class ParameterReadContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    schema_version: Literal["parameter-read-v1"] = "parameter-read-v1"
    action: Action
    canonical_field: Literal["budget_minor", "bid_minor", "target_roi", "enabled"]
    object_path: tuple[str, ...] = Field(min_length=1, max_length=8)
    object_id_field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,59}$")
    value_field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,59}$")
    currency_field: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,59}$")
    clock_kind: Literal["SOURCE", "FETCHED"]
    source_updated_field: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_]{0,59}$")
    max_age_seconds: int = Field(strict=True, gt=0, le=86400)
    roi_decimal_places: int | None = Field(default=None, strict=True, ge=0, le=8)
    precision_ref: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:/-]{1,180}$")
    true_native: str | int | bool | None = None
    false_native: str | int | bool | None = None
    proof_ref: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,180}$")

    @model_validator(mode="after")
    def explicit_semantics(self) -> Self:
        if PARAMETER_FIELDS.get(self.action) != self.canonical_field:
            raise ValueError("仅允许该动作对应的参数字段，不比较效果指标")
        names = (
            self.object_id_field,
            self.value_field,
            self.currency_field,
            self.source_updated_field,
        )
        if any(name is not None and name.lower() in FORBIDDEN_FIELDS for name in names):
            raise ValueError("参数回读不能使用敏感字段或效果ROI")
        if any(
            not part or len(part) > 60 or not part.replace("_", "").isalnum()
            for part in self.object_path
        ):
            raise ValueError("回读对象路径只能来自服务端固定字段")
        if any(
            part.lower() in FORBIDDEN_FIELDS | {"metrics", "performance", "effects"}
            for part in self.object_path
        ):
            raise ValueError("不能从效果或敏感对象路径核验参数")
        if self.clock_kind == "SOURCE" and self.source_updated_field is None:
            raise ValueError("来源时钟必须明确来源字段，不能以抓取时间代替")
        if self.canonical_field in {"budget_minor", "bid_minor"} and self.currency_field is None:
            raise ValueError("整数分回读必须有明确币种字段")
        if self.canonical_field == "target_roi" and (
            self.roi_decimal_places is None or not self.precision_ref
        ):
            raise ValueError("目标ROI必须有已确认的平台精度和依据")
        if self.canonical_field == "enabled" and (
            self.true_native is None
            or self.false_native is None
            or type(self.true_native) is not type(self.false_native)
            or self.true_native == self.false_native
        ):
            raise ValueError("布尔状态必须提供同类型、可区分的真实映射")
        return self

    @property
    def archive_fields(self) -> frozenset[str]:
        return frozenset(part for part in self.object_path if not part.isdecimal()) | frozenset(
            value
            for value in (
                self.object_id_field,
                self.value_field,
                self.currency_field,
                self.source_updated_field,
            )
            if value is not None
        )


class ParameterComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["MATCHED", "MISMATCH", "UNREFRESHED", "UNAVAILABLE"]
    reason: Literal[
        "PARAMETER_MATCHED",
        "PARAMETER_DIFFERS",
        "OBJECT_MISSING",
        "TARGET_MISMATCH",
        "VALUE_MISSING",
        "VALUE_INVALID",
        "CURRENCY_UNKNOWN",
        "STATE_UNMAPPED",
        "SOURCE_TIME_MISSING",
        "SOURCE_TIME_INVALID",
        "SOURCE_TIME_FUTURE",
        "SOURCE_STALE",
        "SOURCE_BEFORE_ACK",
        "EXPECTED_PRECISION_UNSUPPORTED",
        "READ_UNAVAILABLE",
        "READ_STALE",
        "FETCH_TIME_FUTURE",
    ]
    observed_value: JobValue | None = None
    source_updated_at: AwareDatetime | None = None
    fetched_at: AwareDatetime | None = None
    evidence_level: Literal["E0"] = "E0"


def compare_parameter(
    body: dict[str, Any],
    contract: ParameterReadContract,
    expected: JobValue,
    native_id: str,
    *,
    fetched_at: datetime,
    checked_at: datetime,
    accepted_at: datetime | None = None,
) -> ParameterComparison:
    """消费已留档白名单载荷；不拿写响应回显或调用方成功标志作核验。"""

    def result(
        status: Any, reason: Any, value: JobValue | None = None, source_at: datetime | None = None
    ) -> ParameterComparison:
        return ParameterComparison(
            status=status,
            reason=reason,
            observed_value=value,
            source_updated_at=source_at,
            fetched_at=fetched_at,
        )

    current: Any = body
    for part in contract.object_path:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdecimal() and int(part) < len(current):
            current = current[int(part)]
        else:
            current = None
    if not isinstance(current, dict):
        return result("UNAVAILABLE", "OBJECT_MISSING")
    if current.get(contract.object_id_field) != native_id:
        return result("UNAVAILABLE", "TARGET_MISMATCH")
    raw = current.get(contract.value_field)
    if raw is None:
        return result("UNAVAILABLE", "VALUE_MISSING")
    observed: JobValue
    if contract.canonical_field in {"budget_minor", "bid_minor"}:
        if (
            not isinstance(expected, MoneyValue)
            or type(raw) is not int
            or not 0 <= raw <= 9007199254740991
        ):
            return result("UNAVAILABLE", "VALUE_INVALID")
        if current.get(contract.currency_field) != "CNY":
            return result("UNAVAILABLE", "CURRENCY_UNKNOWN")
        observed = MoneyValue(amount_minor=raw)
        matched = observed == expected
    elif contract.canonical_field == "target_roi":
        if not isinstance(expected, RatioValue) or type(raw) not in {int, float}:
            return result("UNAVAILABLE", "VALUE_INVALID")
        try:
            numeric = float(raw)
        except (OverflowError, ValueError):
            return result("UNAVAILABLE", "VALUE_INVALID")
        if not math.isfinite(numeric) or numeric < 0:
            return result("UNAVAILABLE", "VALUE_INVALID")
        if contract.roi_decimal_places is None:
            return result("UNAVAILABLE", "VALUE_INVALID")
        with localcontext() as precision:
            # JSON有限double最多309位整数，显式精度避免大数quantize抛出而假装核验成功。
            precision.prec = 400
            quantum = Decimal(1).scaleb(-contract.roi_decimal_places)
            requested = Decimal(str(expected.value))
            if requested.quantize(quantum, rounding=ROUND_HALF_UP) != requested:
                return result("UNAVAILABLE", "EXPECTED_PRECISION_UNSUPPORTED")
            matched = Decimal(str(raw)).quantize(quantum, rounding=ROUND_HALF_UP) == requested
        observed = RatioValue(value=float(raw))
    else:
        if not isinstance(expected, BooleanValue):
            return result("UNAVAILABLE", "VALUE_INVALID")
        if type(raw) is type(contract.true_native) and raw == contract.true_native:
            observed = BooleanValue(value=True)
        elif type(raw) is type(contract.false_native) and raw == contract.false_native:
            observed = BooleanValue(value=False)
        else:
            return result("UNAVAILABLE", "STATE_UNMAPPED")
        matched = observed == expected
    source_at = None
    if contract.source_updated_field is not None:
        timestamp = current.get(contract.source_updated_field)
        if timestamp is not None:
            try:
                source_at = (
                    datetime.fromisoformat(timestamp) if isinstance(timestamp, str) else None
                )
                if source_at is None or source_at.tzinfo is None:
                    return result("UNAVAILABLE", "SOURCE_TIME_INVALID", observed)
            except (TypeError, ValueError):
                return result("UNAVAILABLE", "SOURCE_TIME_INVALID", observed)
    if fetched_at > checked_at:
        return result("UNAVAILABLE", "FETCH_TIME_FUTURE", observed, source_at)
    if (checked_at - fetched_at).total_seconds() > contract.max_age_seconds:
        return result("UNREFRESHED", "READ_STALE", observed, source_at)
    if contract.clock_kind == "SOURCE":
        if source_at is None:
            return result("UNAVAILABLE", "SOURCE_TIME_MISSING", observed)
        if source_at > fetched_at:
            return result("UNAVAILABLE", "SOURCE_TIME_FUTURE", observed, source_at)
        if (checked_at - source_at).total_seconds() > contract.max_age_seconds:
            return result("UNREFRESHED", "SOURCE_STALE", observed, source_at)
        if accepted_at is not None and source_at <= accepted_at:
            return result("UNREFRESHED", "SOURCE_BEFORE_ACK", observed, source_at)
    return result(
        "MATCHED" if matched else "MISMATCH",
        "PARAMETER_MATCHED" if matched else "PARAMETER_DIFFERS",
        observed,
        source_at,
    )
