"""策略单位/边界属于配置合同，不能由未知值或豆币推定CNY额度。"""

from typing import Any

import pytest
from pydantic import ValidationError

from yuliang.modules.policy.models import PolicyConfig


def config() -> dict[str, Any]:
    return {
        "daily_budget_minor": 10000,
        "target_roi": "2",
        "max_change_percent": "10",
        "cooldown_minutes": 5,
        "max_changes_per_day": 10,
        "mode": "MANUAL",
    }


def test_policy_config_requires_explicit_limits_and_exact_currency() -> None:
    value = PolicyConfig.model_validate(config())
    assert value.currency == "CNY" and value.daily_budget_minor == 10000
    assert value.model_dump(mode="json")["max_change_percent"] == "10"
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate({})


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "BEAN"},
        {"currency": "USD"},
        {"daily_budget_minor": True},
        {"daily_budget_minor": 1.0},
        {"daily_budget_minor": 0},
        {"daily_budget_minor": 9007199254740992},
        {"target_roi": "NaN"},
        {"target_roi": "Infinity"},
        {"max_change_percent": "100.001"},
        {"cooldown_minutes": -1},
        {"max_changes_per_day": 0},
        {"mode": "AUTO_APPROVED"},
        {"token": "must-not-be-policy-config"},
    ],
)
def test_unknown_unit_invalid_value_or_extra_field_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate(config() | changes)
