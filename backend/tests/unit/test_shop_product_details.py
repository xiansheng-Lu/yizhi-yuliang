"""固定详情字段白名单与精确数值，不从示例默认值制造事实。"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from yuliang.modules.connectors.http import HttpExchange
from yuliang.modules.connectors.shop_detail_protocol import parse_detail


def response(product: dict[str, object]) -> HttpExchange:
    return HttpExchange(
        uuid4(),
        "wxshop.product.detail",
        200,
        1,
        datetime.now(UTC),
        {},
        json.dumps(dict(errcode=0, product=product)).encode(),
    )


def test_detail_precision_missing_and_privacy() -> None:
    p = parse_detail(
        response(
            dict(
                product_id=18446744073709551615,
                status=5,
                title="PRIVATE",
                skus=[
                    dict(
                        sku_id=18446744073709551614,
                        sale_price=9223372036854775807,
                        stock_num=0,
                        phone="PRIVATE",
                    )
                ],
            )
        ),
        "18446744073709551615",
    )
    assert p.product["skus"][0]["native_price"] == "9223372036854775807"
    assert p.product["skus"][0]["native_stock"] == "0"
    assert "PRIVATE" not in str(p.product)
    assert p.sync.source_at is None
    assert parse_detail(response(dict(product_id="1")), "1").product["sku_presence"] == "MISSING"
    assert (
        parse_detail(response(dict(product_id="1", skus=[])), "1").product["sku_presence"]
        == "PRESENT"
    )


@pytest.mark.parametrize(
    "product",
    [
        dict(product_id="2"),
        dict(product_id="1", status=True),
        dict(product_id="1", skus={}),
        dict(product_id="1", skus=[dict(sku_id="1", stock_num=-1)]),
        dict(product_id="1", skus=[dict(sku_id="1", sale_price=1.5)]),
        dict(product_id="1", skus=[dict(sku_id="1"), dict(sku_id="1")]),
    ],
)
def test_invalid_detail_never_advances(product: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        parse_detail(response(product), "1")
