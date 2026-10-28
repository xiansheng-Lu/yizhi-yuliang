"""整数分/ROI参数精度/真实布尔枚举及两时钟；比较结果始终E0。"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from yuliang.modules.command.jobs import BooleanValue, MoneyValue, RatioValue
from yuliang.modules.execution.readback_values import ParameterReadContract, compare_parameter


def contract(action: str = "CHANGE_DAILY_BUDGET", **changes: Any) -> ParameterReadContract:
    field = (
        "target_roi"
        if action == "SET_ROI_TARGET"
        else "enabled"
        if action == "PAUSE_AD_PLAN"
        else "budget_minor"
    )
    values: dict[str, Any] = dict(
        action=action,
        canonical_field=field,
        object_path=("data",),
        object_id_field="id",
        value_field=field,
        currency_field="currency",
        clock_kind="FETCHED",
        max_age_seconds=30,
        proof_ref="synthetic/M2-17a/parameter",
        roi_decimal_places=2,
        precision_ref="synthetic/platform/two-decimal-places",
        true_native="ENABLED",
        false_native="DISABLED",
    )
    return ParameterReadContract.model_validate(values | changes)


@pytest.mark.parametrize(
    "value,status",
    [
        (110, "MATCHED"),
        (100, "MISMATCH"),
        (0, "MISMATCH"),
        (None, "UNAVAILABLE"),
        (110.0, "UNAVAILABLE"),
        (True, "UNAVAILABLE"),
        ("110", "UNAVAILABLE"),
        (9007199254740992, "UNAVAILABLE"),
        (-1, "UNAVAILABLE"),
    ],
)
def test_money_is_explicit_cny_integer_minor_not_truthiness(value: Any, status: str) -> None:
    now = datetime.now(UTC)
    result = compare_parameter(
        {"data": {"id": "456", "budget_minor": value, "currency": "CNY"}},
        contract(),
        MoneyValue(amount_minor=110),
        "456",
        fetched_at=now,
        checked_at=now,
    )
    assert result.status == status and result.evidence_level == "E0"
    if value is not None and type(value) is int and value == 0:
        assert result.observed_value == MoneyValue(amount_minor=0)


@pytest.mark.parametrize("currency", [None, "BEAN", "USD"])
def test_missing_or_other_currency_cannot_be_inferred(currency: Any) -> None:
    now = datetime.now(UTC)
    result = compare_parameter(
        {"data": {"id": "456", "budget_minor": 110, "currency": currency}},
        contract(),
        MoneyValue(amount_minor=110),
        "456",
        fetched_at=now,
        checked_at=now,
    )
    assert result.reason == "CURRENCY_UNKNOWN" and result.status == "UNAVAILABLE"


@pytest.mark.parametrize(
    "value,expected,status",
    [
        (1.23000000001, 1.23, "MATCHED"),
        (1.235, 1.23, "MISMATCH"),
        (0, 1.23, "MISMATCH"),
        (1.23, 1.234, "UNAVAILABLE"),
        (float("nan"), 1.23, "UNAVAILABLE"),
        (True, 1.23, "UNAVAILABLE"),
        (1e308, 1.23, "MISMATCH"),
        (10**500, 1.23, "UNAVAILABLE"),
    ],
)
def test_target_roi_uses_confirmed_precision_and_refuses_unrepresentable_request(
    value: Any,
    expected: float,
    status: str,
) -> None:
    now = datetime.now(UTC)
    result = compare_parameter(
        {"data": {"id": "456", "target_roi": value}},
        contract("SET_ROI_TARGET"),
        RatioValue(value=expected),
        "456",
        fetched_at=now,
        checked_at=now,
    )
    assert result.status == status


@pytest.mark.parametrize(
    "value,status",
    [
        ("DISABLED", "MATCHED"),
        ("ENABLED", "MISMATCH"),
        (False, "UNAVAILABLE"),
        (0, "UNAVAILABLE"),
        ("UNKNOWN", "UNAVAILABLE"),
    ],
)
def test_boolean_follows_confirmed_typed_mapping(value: Any, status: str) -> None:
    now = datetime.now(UTC)
    result = compare_parameter(
        {"data": {"id": "456", "enabled": value}},
        contract("PAUSE_AD_PLAN"),
        BooleanValue(value=False),
        "456",
        fetched_at=now,
        checked_at=now,
    )
    assert result.status == status


def test_source_and_fetch_time_distinguish_unavailable_stale_and_unrefreshed() -> None:
    now = datetime.now(UTC)
    spec = contract(clock_kind="SOURCE", source_updated_field="source_updated_at")
    body = {"data": {"id": "456", "budget_minor": 110, "currency": "CNY"}}
    missing = compare_parameter(
        body, spec, MoneyValue(amount_minor=110), "456", fetched_at=now, checked_at=now
    )
    assert missing.reason == "SOURCE_TIME_MISSING" and missing.source_updated_at is None
    for timestamp, reason in (
        (now - timedelta(seconds=2), "SOURCE_BEFORE_ACK"),
        (now - timedelta(minutes=2), "SOURCE_STALE"),
        (now + timedelta(seconds=1), "SOURCE_TIME_FUTURE"),
    ):
        body["data"]["source_updated_at"] = timestamp.isoformat()
        result = compare_parameter(
            body,
            spec,
            MoneyValue(amount_minor=110),
            "456",
            fetched_at=now,
            checked_at=now,
            accepted_at=now - timedelta(seconds=1),
        )
        assert result.reason == reason
    fetched = compare_parameter(
        {"data": {"id": "456", "budget_minor": 110, "currency": "CNY"}},
        contract(),
        MoneyValue(amount_minor=110),
        "456",
        fetched_at=now,
        checked_at=now,
    )
    assert fetched.status == "MATCHED" and fetched.source_updated_at is None
    replay = compare_parameter(
        body,
        contract(),
        MoneyValue(amount_minor=110),
        "456",
        fetched_at=now - timedelta(hours=1),
        checked_at=now,
    )
    assert replay.status == "UNREFRESHED" and replay.reason == "READ_STALE"


def test_effect_roi_missing_parameter_and_wrong_target_do_not_match() -> None:
    now = datetime.now(UTC)
    for body, reason in (
        ({"data": {"id": "456", "roi": 1.23}}, "VALUE_MISSING"),
        ({"data": {"id": "other", "target_roi": 1.23}}, "TARGET_MISMATCH"),
    ):
        result = compare_parameter(
            body,
            contract("SET_ROI_TARGET"),
            RatioValue(value=1.23),
            "456",
            fetched_at=now,
            checked_at=now,
        )
        assert result.status == "UNAVAILABLE" and result.reason == reason
    for changes in (
        {"value_field": "roi"},
        {"roi_decimal_places": None},
        {"precision_ref": None},
        {"canonical_field": "budget_minor"},
    ):
        with pytest.raises(ValidationError):
            contract("SET_ROI_TARGET", **changes)
