"""商家结算不混为广告资金，未知口径不能制造事实。"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from yuliang.modules.connectors.http import HttpExchange
from yuliang.modules.connectors.shop_orders import order_observation


def exchange(data: dict[str, Any]) -> HttpExchange:
    return HttpExchange(uuid4(), "test", 200, 1, datetime.now(UTC), {}, json.dumps(data).encode())


def test_settlement_unknown_and_missing_are_distinct() -> None:
    page = order_observation("SETTLEMENT", exchange(dict(errcode=0, available_amount=0)), None)
    values = page.records[0].values
    assert values["wallet_kind"] == "MERCHANT_SETTLEMENT"
    assert values["available_amount"] == "0" and values["pending_amount"] is None
    assert values["unit_status"] == "UNCONFIRMED" and page.source_at is None


@pytest.mark.parametrize("value", [True, 1.5, "1.1", {}, []])
def test_invalid_amount_never_rounds(value: Any) -> None:
    with pytest.raises(ValueError):
        order_observation("SETTLEMENT", exchange(dict(errcode=0, available_amount=value)), None)


def test_order_id_mismatch_rejected() -> None:
    with pytest.raises(ValueError):
        order_observation("ORDER", exchange(dict(errcode=0, order=dict(order_id="2"))), "1")


@pytest.mark.parametrize("kind", ["ORDER_LIST", "AFTERSALE_LIST"])
def test_list_requires_explicit_complete_cursor(kind: Any) -> None:
    with pytest.raises(ValueError):
        order_observation(kind, exchange(dict(errcode=0, has_more=True, next_key="")), None)


def test_refund_is_observation_not_completed_money_fact() -> None:
    page = order_observation(
        "AFTERSALE",
        exchange(
            dict(
                errcode=0,
                after_sale_order=dict(
                    after_sale_order_id="1",
                    order_id="2",
                    status="PROCESSING",
                    refund_info=dict(amount=0),
                    openid="PRIVATE",
                    details=dict(tel_number="PRIVATE"),
                ),
            )
        ),
        "1",
    )
    values = page.records[0].values
    assert values["refund_amount"] == "0" and values["refund_status"] == "PROCESSING"
    assert values["unit_status"] == "UNCONFIRMED" and page.source_at is None
    assert "PRIVATE" not in json.dumps(values)
