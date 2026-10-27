"""口径隔离、精确金额与来源排序规则。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from yuliang.modules.reporting.models import FactDimensions, MetricSample, aggregate_ratio
from yuliang.modules.reporting.revisions import SourceStamp, compare


def dimensions(**changes: object) -> FactDimensions:
    return FactDimensions.model_validate(
        {
            "fact_date": date(2026, 10, 27),
            "granularity": "DAY",
            "provider": "tencent",
            "product_line": "adq",
            "entity_kind": "ACCOUNT",
            "native_entity_id": "123",
            "shop_id": uuid4(),
            "account_id": uuid4(),
            "metric_name": "spend_minor",
            "unit": "MINOR",
            "currency": "CNY",
            "attribution_window": "7d_click",
            "refund_policy": "GROSS",
        }
        | changes
    )


@pytest.mark.parametrize("bad", [0.1, True, Decimal("1.2"), 9007199254740992, Decimal("NaN")])
def test_money_never_uses_float_fraction_or_unsafe_integer(bad: object) -> None:
    with pytest.raises(ValueError):
        MetricSample(dimensions=dimensions(), value=bad)


def test_zero_missing_and_weighted_ratio() -> None:
    key = dimensions()
    assert MetricSample(dimensions=key, value=0).normalized_value == 0
    assert MetricSample(dimensions=key, value=None).normalized_value is None
    ratio = FactDimensions.model_validate(
        key.model_dump()
        | {"metric_name": "roi", "unit": "RATIO", "currency": None, "basis_currency": "CNY"}
    )
    first = MetricSample(dimensions=ratio, numerator=100, denominator=10)
    second = MetricSample(
        dimensions=FactDimensions.model_validate(
            ratio.model_dump() | {"fact_date": date(2026, 10, 26)}
        ),
        numerator=9,
        denominator=9,
    )
    assert aggregate_ratio((first, second)) == Decimal("5.736842")
    assert MetricSample(dimensions=ratio, numerator=0, denominator=1).normalized_value == 0
    assert MetricSample(dimensions=ratio, numerator=0, denominator=0).normalized_value is None
    with pytest.raises(ValueError):
        MetricSample(dimensions=ratio, value=5)
    with pytest.raises(ValueError):
        aggregate_ratio((first, first))
    mixed = FactDimensions.model_validate(
        second.dimensions.model_dump() | {"refund_policy": "NET_OF_REFUND"}
    )
    with pytest.raises(ValueError):
        aggregate_ratio((first, MetricSample(dimensions=mixed, numerator=9, denominator=9)))


def test_full_key_and_comparable_key_do_not_accept_free_text_override() -> None:
    key = dimensions()
    variants: list[dict[str, Any]] = [
        {"product_line": "shop"},
        {"account_id": uuid4()},
        {"live_id": uuid4()},
        {"product_id": uuid4(), "sku_id": uuid4()},
        {"unit": "BEAN", "currency": "BEAN"},
        {"attribution_window": "1d_click"},
        {"refund_policy": "NET_OF_REFUND"},
        {"granularity": "HOUR", "fact_hour": 0},
    ]
    for change in variants:
        other = FactDimensions.model_validate(key.model_dump() | change)
        assert key.business_key() != other.business_key()
    with pytest.raises(ValueError):
        FactDimensions.model_validate(key.model_dump() | {"comparable_key": "pretend-same"})


def test_source_order_never_uses_fetched_time() -> None:
    source = uuid4()
    now = datetime(2026, 10, 27, tzinfo=UTC)
    old = SourceStamp(source, 1, now, 5, "actual-source-revision")
    assert (
        compare(
            old,
            SourceStamp(source, 99, now + timedelta(days=1), 4, "actual-source-revision"),
            same_value=False,
        )
        == "STALE"
    )
    assert compare(old, SourceStamp(source, 2, None, None, None), same_value=False) == "CONFLICT"
    assert (
        compare(old, SourceStamp(source, 2, now, 6, "actual-source-revision"), same_value=False)
        == "APPLIED"
    )
    assert (
        compare(old, SourceStamp(source, 2, now, 5, "actual-source-revision"), same_value=False)
        == "CONFLICT"
    )
    unknown = SourceStamp(source, 1, None, None, None)
    assert compare(unknown, SourceStamp(source, 2, None, None, None), same_value=False) == "APPLIED"
    assert (
        compare(unknown, SourceStamp(uuid4(), 99, None, None, None), same_value=False) == "CONFLICT"
    )
