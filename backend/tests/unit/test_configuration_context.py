"""V9跨端上下文与字段单位约束。"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.configuration.fields import initial_fields
from yuliang.modules.configuration.models import ConfigurationContext, FieldCapability, FieldProof

pytestmark = pytest.mark.unit


def context_values() -> dict[str, Any]:
    return dict(
        product="adq",
        scenario="ADQ",
        business_entity_id=uuid4(),
        account_id=uuid4(),
        shop_id="",
        objective_id="",
        optimization_id="",
        strategy_id="",
        target_ids=(),
        placement_ids=(),
        bid_mode_id="",
        order_type_id="",
        fund_type_id="",
        delivery_mode_id="",
        creative_method_id="",
    )


def test_context_matches_js_utf16_order_and_all_fifteen_dimensions() -> None:
    values = context_values() | {"target_ids": ("\ue000", "😀", "a"), "placement_ids": ("2", "1")}
    context = ConfigurationContext.model_validate(values)
    key = json.loads(context.context_key)
    assert len(key) == 15 and key[8] == ["a", "😀", "\ue000"] and key[9] == ["1", "2"]
    assert (
        ConfigurationContext.model_validate(
            values | {"target_ids": tuple(reversed(values["target_ids"]))}
        ).context_key
        == context.context_key
    )
    for field in (
        "objective_id",
        "optimization_id",
        "strategy_id",
        "bid_mode_id",
        "order_type_id",
        "fund_type_id",
        "delivery_mode_id",
        "creative_method_id",
    ):
        assert (
            ConfigurationContext.model_validate(values | {field: "other"}).context_key
            != context.context_key
        )


@pytest.mark.parametrize(
    "update",
    [
        {"scenario": "VIDEO"},
        {"target_ids": ("a", "a")},
        {"placement_ids": ("",)},
        {"shop_id": "unmapped-shop"},
        {"target_ids": ("\ud800",)},
        {"objective_id": "bad\x00"},
        {"tenantId": "1"},
        {"target_ids": tuple(str(n) for n in range(101))},
    ],
)
def test_context_rejects_ambiguous_or_untrusted_dimensions(update: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ConfigurationContext.model_validate(context_values() | update)


@pytest.mark.parametrize(
    "update",
    [
        {"min": 0.5},
        {"step": 0.1},
        {"precision": 2},
        {"max": float("inf")},
        {"max": 9007199254740992},
        {"min": True},
        {"min": "100"},
        {"allowed_min": (0.5,)},
        {"allowed_max": (9007199254740992,)},
        {"min": 20, "max": 10},
        {"unit": "RATIO"},
    ],
)
def test_money_bounds_cannot_be_fractional_nonfinite_or_coerced(update: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FieldCapability.model_validate(
            dict(state="AVAILABLE", reason="synthetic", value_type="INTEGER", unit="CNY_MINOR")
            | update
        )


def test_initial_fields_do_not_manufacture_platform_options_or_bounds() -> None:
    fields = initial_fields()
    assert len(fields) == 42 and fields["name"].state == "PUBLIC"
    assert all(f.state == "UNCONFIRMED" for k, f in fields.items() if k != "name")
    assert all(f.options is None and f.min is None and f.max is None for f in fields.values())


@pytest.mark.parametrize("unit", ["CNY_MINOR", "BEAN"])
def test_money_and_bean_zero_bounds_remain_integers(unit: str) -> None:
    field = FieldCapability.model_validate(
        dict(
            state="AVAILABLE",
            reason="synthetic",
            value_type="INTEGER",
            unit=unit,
            min=0,
            max=100,
            step=1,
        )
    )
    assert field.model_dump(mode="json")["min"] == 0


@pytest.mark.parametrize(
    "update",
    [
        {"unit": "RATIO", "value_type": "DECIMAL"},
        {"state": "API_PENDING", "options": []},
        {"state": "UNCONFIRMED", "min": 100},
        {
            "options": [
                {"id": "a", "name": "A", "available": True},
                {"id": "a", "name": "B", "available": False},
            ]
        },
    ],
)
def test_unconfirmed_precision_and_options_fail_closed(update: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FieldCapability.model_validate(
            dict(state="AVAILABLE", reason="synthetic", value_type="SELECT") | update
        )


def test_available_evidence_requires_capability_and_actual_authorization_version() -> None:
    now = datetime.now(UTC)
    values = dict(
        context=context_values(),
        field_id="budget",
        capability=dict(state="AVAILABLE", reason="synthetic", value_type="INTEGER"),
        proof_ref="synthetic/M3-07",
        checked_at=now,
        expires_at=now + timedelta(minutes=1),
    )
    with pytest.raises(ValidationError):
        FieldProof.model_validate(values)
    proof = FieldProof.model_validate(
        values
        | dict(
            binding_id=uuid4(),
            binding_version=1,
            authorization_id=uuid4(),
            authorization_version=1,
            required_scopes=("read",),
        )
    )
    assert proof.capability.state == "AVAILABLE"
