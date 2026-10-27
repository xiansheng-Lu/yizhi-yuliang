"""金额一分/百分比精度/次数与冷却边界，输入不代表真实平台事实。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from yuliang.modules.command.jobs import BooleanValue, MoneyValue, RatioValue
from yuliang.modules.policy.thresholds import amount_limit, change_bound, frequency_bound


@pytest.mark.parametrize(
    "total,reserved,after,expected",
    [
        (10000, 1000, 2000, True),
        (10000, 1000, 2001, False),
        (13000, 0, 500, True),
        (13000, 0, 1001, False),
    ],
)
def test_budget_boundary_and_external_over_limit_reduction(
    total: int, reserved: int, after: int, expected: bool
) -> None:
    result = amount_limit(
        "CHANGE_DAILY_BUDGET",
        MoneyValue(amount_minor=1000),
        MoneyValue(amount_minor=after),
        confirmed=total,
        reserved=reserved,
        limit=12000,
        currency="CNY",
        covered_paused=True,
    )
    assert result.passed is expected


@pytest.mark.parametrize(
    "total,currency,paused", [(None, "CNY", True), (0, "BEAN", True), (0, "CNY", False)]
)
def test_missing_or_wrong_unit_never_becomes_zero(
    total: int | None, currency: str, paused: bool
) -> None:
    assert not amount_limit(
        "CHANGE_DAILY_BUDGET",
        MoneyValue(amount_minor=100),
        MoneyValue(amount_minor=101),
        confirmed=total,
        reserved=0,
        limit=10000,
        currency=currency,
        covered_paused=paused,
    ).passed


@pytest.mark.parametrize(
    "before,after,bound,expected",
    [
        (10000, 11000, "10", True),
        (10000, 11001, "10", False),
        (10000, 9000, "10", True),
        (10000, 8999, "10", False),
        (3, 4, "33.333", False),
        (0, 1, "100", False),
    ],
)
def test_exact_percentage_without_float_rounding(
    before: int, after: int, bound: str, expected: bool
) -> None:
    assert (
        change_bound(
            "CHANGE_BID",
            MoneyValue(amount_minor=before),
            MoneyValue(amount_minor=after),
            Decimal(bound),
        ).passed
        is expected
    )


def test_roi_and_boolean_are_not_money() -> None:
    assert change_bound(
        "SET_ROI_TARGET", RatioValue(value=0), RatioValue(value=1.25), Decimal(10)
    ).passed


def test_known_zero_is_preserved_but_missing_budget_component_is_refused() -> None:
    assert amount_limit(
        "CHANGE_DAILY_BUDGET",
        MoneyValue(amount_minor=0),
        MoneyValue(amount_minor=1),
        confirmed=0,
        reserved=0,
        limit=10,
        currency="CNY",
        covered_paused=True,
    ).passed
    assert not amount_limit(
        "CHANGE_DAILY_BUDGET",
        MoneyValue(amount_minor=100),
        MoneyValue(amount_minor=101),
        confirmed=0,
        reserved=0,
        limit=1000,
        currency="CNY",
        covered_paused=True,
    ).passed
    assert not change_bound(
        "SET_ROI_TARGET", MoneyValue(amount_minor=10), MoneyValue(amount_minor=20), Decimal(10)
    ).passed
    assert change_bound(
        "PAUSE_AD_PLAN", BooleanValue(value=True), BooleanValue(value=False), Decimal(10)
    ).passed


@pytest.mark.parametrize(
    "count,age,unresolved,expected",
    [
        (9, 300, 0, True),
        (10, 300, 0, False),
        (9, 299.999, 0, False),
        (0, 300, 1, False),
        (None, 300, 0, False),
    ],
)
def test_verified_count_and_cooldown_boundaries(
    count: int | None, age: float, unresolved: int, expected: bool
) -> None:
    at = datetime.now(UTC)
    assert (
        frequency_bound(
            at=at,
            last_verified_at=at - timedelta(seconds=age),
            verified_today=count,
            unresolved_writes=unresolved,
            cooldown_minutes=5,
            maximum_per_day=10,
        ).passed
        is expected
    )
