"""罗盘保留真实0、缺失与空数据，精确字段及非PII投影。"""

import json
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.connectors.http import HttpExchange
from yuliang.modules.connectors.shop_compass_protocol import CompassParameters, compass_page


def response(data: object) -> HttpExchange:
    return HttpExchange(
        uuid4(),
        "wxshop.compass.overall",
        200,
        1,
        datetime.now(UTC),
        {},
        json.dumps(dict(errcode=0, data=data)).encode(),
    )


def test_zero_missing_empty_and_no_private_fields() -> None:
    row = (
        compass_page(
            response(dict(pay_gmv="0", phone="PRIVATE", pay_order_cnt=2)), date(2026, 10, 20)
        )
        .records[0]
        .values
    )
    assert row["values"]["pay_gmv"] == "0"
    assert row["values"]["pay_refund_gmv"] is None
    assert "PRIVATE" not in json.dumps(row)
    assert row["state"] == "OBSERVED"
    assert compass_page(response({}), date(2026, 10, 20)).records[0].values["state"] == "EMPTY_DATA"


@pytest.mark.parametrize("value", [True, 1.5, -1, "01", "1e3", "18446744073709551616"])
def test_bad_metrics_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        compass_page(response(dict(pay_gmv=value)), date(2026, 10, 20))


@pytest.mark.parametrize("value", ["20260230", "2026-10-20", "20261301"])
def test_invalid_daily_query_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        CompassParameters(ds=value)


def test_missing_data_not_empty_success() -> None:
    with pytest.raises(ValueError):
        compass_page(response(None), date(2026, 10, 20))
