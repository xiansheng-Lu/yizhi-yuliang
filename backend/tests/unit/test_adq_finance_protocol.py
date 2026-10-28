"""固定SDK只读协议与精确原生数值；不证明真实币种/财务口径。"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from yuliang.modules.connectors.adq_finance_protocol import (
    OperationQuery,
    int64_text,
    parse_funds,
    parse_operations,
)
from yuliang.modules.connectors.http import HttpExchange


def response(data: Any) -> HttpExchange:
    return HttpExchange(
        uuid4(), "synthetic.finance", 200, 1, datetime.now(UTC), {}, json.dumps(data).encode()
    )


@pytest.mark.parametrize(
    "value", [True, 1.0, "1.0", "01", "-0", 9223372036854775808, -9223372036854775809]
)
def test_finance_native_int64_rejects_lossy_or_ambiguous_values(value: Any) -> None:
    with pytest.raises(ValueError):
        int64_text(value)


def test_single_funds_precise_negative_or_missing_values_and_unknown_units() -> None:
    result = parse_funds(
        response(
            dict(
                code=0,
                data=dict(
                    list=[
                        dict(
                            fund_type="FUND_TYPE_CASH",
                            balance=9007199254740993,
                            realtime_cost=0,
                            fund_status="FUND_STATUS_NORMAL",
                            contact_person="PRIVATE",
                        ),
                        dict(fund_type="FUND_TYPE_CREDIT_ROLL", balance=-50),
                        dict(fund_type="FUND_TYPE_GIFT"),
                    ]
                ),
            )
        )
    )
    assert result.sync.terminal and result.sync.source_at is None
    assert result.rows[0]["native_balance"] == "9007199254740993"
    assert result.rows[1]["native_balance"] == "-50" and result.rows[2]["native_balance"] is None
    assert all(
        row["unit_state"] == "UNCONFIRMED" and "contact_person" not in row for row in result.rows
    )


@pytest.mark.parametrize(
    "body",
    [
        {"code": 0},
        {"code": 0, "data": {"list": None}},
        {"code": 0, "data": {"list": [{"fund_type": "FUND_TYPE_CASH", "account_id": 67890}]}},
        {"code": 0, "data": {"list": [{"fund_type": "FUND_TYPE_CASH", "balance": True}]}},
        {"code": 0, "data": {"list": [{"fund_type": "FUND_TYPE_CASH"}] * 2}},
    ],
)
def test_missing_or_invalid_funds_never_become_complete_zero_balances(body: Any) -> None:
    with pytest.raises(ValueError):
        parse_funds(response(body), "12345")


def test_operation_rows_have_no_native_log_id_and_no_timestamp_unit_guess() -> None:
    query = OperationQuery(
        operation_object_type="OPERATION_OBJECT_TYPE_ADGROUP",
        start_date="2026-10-01",
        end_date="2026-10-02",
    )
    row = dict(
        operation_object_id=9007199254740993,
        operation_action="OPERATION_ACTION_TYPE_UPDATE",
        operator_platform="OPERATOR_PLATFORM_MARKETING_API",
        created_time=123,
        fronted_operator="PRIVATE",
        operation_object_name="PRIVATE",
        operation_log=["PRIVATE"],
    )
    page = parse_operations(
        response(
            dict(
                code=0,
                data=dict(
                    list=[row, row],
                    page_info=dict(page=1, page_size=2, total_number=3, total_page=2),
                ),
            )
        ),
        query,
        1,
        2,
    )
    assert not page.sync.terminal and page.sync.scanned_items == 2 and page.sync.records == ()
    assert page.rows[0] == page.rows[1] and page.rows[0]["native_log_id"] is None
    assert (
        page.rows[0]["native_created_time"] == "123" and page.rows[0]["time_unit"] == "UNCONFIRMED"
    )
    assert "PRIVATE" not in json.dumps(page.rows)


def test_operation_query_keeps_sdk_object_hierarchy_and_bounded_real_dates() -> None:
    for values in (
        dict(operation_object_type="CAMPAIGN", start_date="2026-10-01", end_date="2026-10-02"),
        dict(
            operation_object_type="OPERATION_OBJECT_TYPE_ADGROUP",
            start_date="2026-02-30",
            end_date="2026-03-01",
        ),
        dict(
            operation_object_type="OPERATION_OBJECT_TYPE_ADGROUP",
            start_date="2026-10-02",
            end_date="2026-10-01",
        ),
    ):
        with pytest.raises(ValueError):
            OperationQuery.model_validate(values)
