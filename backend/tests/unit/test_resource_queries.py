"""Frontend numeric safety and frozen date scope boundaries."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from yuliang.modules.reporting.query_models import QueryScope
from yuliang.modules.reporting.query_service import normalized


def scope(**changes: str) -> QueryScope:
    value = dict(
        tenantId="1",
        businessEntityId="00000000-0000-0000-0000-000000000001",
        shopId="00000000-0000-0000-0000-000000000002",
        provider="tencent",
        productLine="adq",
        accountId="00000000-0000-0000-0000-000000000003",
        start="2024-02-29",
        end="2024-03-01",
    )
    return QueryScope.model_validate(value | changes)


def test_scope_key_matches_ordered_frontend_identity_and_valid_leap_date() -> None:
    value = scope()
    assert json.loads(value.key()) == [
        "1",
        str(value.business_entity_id),
        str(value.shop_id),
        "tencent",
        "adq",
        value.account_id,
        value.start,
        value.end,
    ]
    assert " " not in value.key()


@pytest.mark.parametrize(
    "changes", [{"start": "2023-02-29"}, {"end": "2024-02-28"}, {"end": "2026-03-01"}]
)
def test_invalid_or_unbounded_history_is_rejected(changes: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        scope(**changes)


@pytest.mark.parametrize(
    "value", [Decimal("9007199254740992"), Decimal("0.5"), Decimal("NaN"), Decimal("Infinity"), -1]
)
def test_unsafe_money_not_coerced_to_zero(value: Decimal | int) -> None:
    warnings: list[str] = []
    assert normalized(value, "MINOR", warnings) is None
    assert warnings


def test_real_zero_distinct_from_unknown() -> None:
    assert normalized(Decimal(0), "MINOR", []) == 0
    assert normalized(None, "MINOR", []) is None
