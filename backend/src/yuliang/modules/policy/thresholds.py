"""精确数值边界；UNKNOWN与0分开，预算包含暂停计划的基数不在此临时重算。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from yuliang.modules.command.jobs import Action, BooleanValue, JobValue, MoneyValue, RatioValue


@dataclass(frozen=True)
class Verdict:
    passed: bool
    reason: str


def amount_limit(
    action: Action,
    old: JobValue | None,
    new: JobValue,
    *,
    confirmed: int | None,
    reserved: int | None,
    limit: int,
    currency: str,
    covered_paused: bool,
) -> Verdict:
    if (
        currency != "CNY"
        or not covered_paused
        or any(type(value) is not int or value < 0 for value in (confirmed, reserved))
        or type(limit) is not int
        or limit <= 0
    ):
        return Verdict(False, "BUDGET_BASIS_UNKNOWN")
    if confirmed is None or reserved is None:
        return Verdict(False, "BUDGET_BASIS_UNKNOWN")
    if (
        action == "CHANGE_DAILY_BUDGET"
        and isinstance(old, MoneyValue)
        and confirmed < old.amount_minor
    ):
        return Verdict(False, "BUDGET_COMPONENT_INCONSISTENT")
    delta = 0
    reducing = action == "PAUSE_AD_PLAN"
    if action in {"CHANGE_DAILY_BUDGET", "CHANGE_BID"}:
        if not isinstance(old, MoneyValue) or not isinstance(new, MoneyValue):
            return Verdict(False, "CURRENT_VALUE_UNKNOWN")
        reducing = new.amount_minor < old.amount_minor
        if action == "CHANGE_DAILY_BUDGET":
            delta = max(new.amount_minor - old.amount_minor, 0)
    # 外部已超限事实仍可入库；明确减额/暂停不因现有超限而失去纠偏出口。
    if reducing:
        return Verdict(True, "REDUCING_EXPOSURE")
    return Verdict(
        confirmed + reserved + delta <= limit,
        "WITHIN_LIMIT" if confirmed + reserved + delta <= limit else "LIMIT_EXCEEDED",
    )


def change_bound(
    action: Action, old: JobValue | None, new: JobValue, maximum_percent: Decimal
) -> Verdict:
    if not maximum_percent.is_finite() or not Decimal(0) < maximum_percent <= Decimal(100):
        return Verdict(False, "INVALID_BOUND")
    if action in {"CHANGE_DAILY_BUDGET", "CHANGE_BID"}:
        if not isinstance(old, MoneyValue) or not isinstance(new, MoneyValue):
            return Verdict(False, "CURRENT_VALUE_UNKNOWN")
        if old.amount_minor == 0:
            return Verdict(False, "ZERO_PERCENT_REFERENCE")
        allowed = (
            Decimal(abs(new.amount_minor - old.amount_minor)) * 100
            <= Decimal(old.amount_minor) * maximum_percent
        )
        return Verdict(allowed, "WITHIN_CHANGE_BOUND" if allowed else "CHANGE_BOUND_EXCEEDED")
    if action == "SET_ROI_TARGET":
        return Verdict(isinstance(new, RatioValue) and new.value > 0, "POSITIVE_ROI_REQUIRED")
    if action in {"PAUSE_AD_PLAN", "RESUME_AD_PLAN"}:
        return Verdict(
            isinstance(old, BooleanValue)
            and isinstance(new, BooleanValue)
            and new.value == (action == "RESUME_AD_PLAN"),
            "EXPLICIT_BOOLEAN_REQUIRED",
        )
    return Verdict(False, "MANUAL_ONLY")


def frequency_bound(
    *,
    at: datetime,
    last_verified_at: datetime | None,
    verified_today: int | None,
    unresolved_writes: int,
    cooldown_minutes: int,
    maximum_per_day: int,
) -> Verdict:
    if (
        type(verified_today) is not int
        or verified_today < 0
        or type(unresolved_writes) is not int
        or unresolved_writes < 0
    ):
        return Verdict(False, "FREQUENCY_BASIS_UNKNOWN")
    if unresolved_writes:
        return Verdict(False, "UNRESOLVED_WRITE_EVIDENCE")
    if at.tzinfo is None or (
        last_verified_at is not None and (last_verified_at.tzinfo is None or last_verified_at > at)
    ):
        return Verdict(False, "VERIFICATION_CLOCK_UNKNOWN")
    if last_verified_at is not None and at - last_verified_at < timedelta(minutes=cooldown_minutes):
        return Verdict(False, "COOLDOWN_ACTIVE")
    if verified_today + 1 > maximum_per_day:
        return Verdict(False, "DAILY_CHANGE_LIMIT")
    return Verdict(True, "WITHIN_FREQUENCY_BOUND")
