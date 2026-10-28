"""固定SDK人群页拒绝错误数据/串账户，精确ID和不透明游标不生成资源。"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from yuliang.modules.connectors.http import HttpExchange
from yuliang.modules.connectors.lookup_protocol import audience_page


def exchange(rows: list[dict[str, object]], **metadata: int) -> HttpExchange:
    meta = dict(page=1, page_size=2, total_number=len(rows), total_page=1) | metadata
    return HttpExchange(
        uuid4(),
        "adq.lookup.audiences",
        200,
        1,
        datetime.now(UTC),
        {},
        json.dumps(dict(code=0, data=dict(list=rows, page_info=meta))).encode(),
    )


def test_precise_ids_and_complete_terminal() -> None:
    row = dict(
        audience_id=9223372036854775807,
        account_id=12345,
        name="真实人群",
        status="SUCCESS",
        online_status="ONLINE",
        phone="PRIVATE",
    )
    page, total, pages = audience_page(exchange([row]), "12345", 1, 2)
    assert page.records[0].native_id == "9223372036854775807"
    assert "phone" not in page.records[0].values
    assert page.terminal and total == pages == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("audience_id", True),
        ("audience_id", 1.5),
        ("account_id", 12346),
        ("name", ""),
        ("online_status", None),
    ],
)
def test_invalid_and_cross_account_resources_rejected(field: str, value: object) -> None:
    row: dict[str, object] = dict(
        audience_id=1, account_id=12345, name="real", status="SUCCESS", online_status="ONLINE"
    )
    row[field] = value
    with pytest.raises(ValueError):
        audience_page(exchange([row]), "12345", 1, 2)


def test_incomplete_pagination_rejected() -> None:
    with pytest.raises(ValueError):
        audience_page(exchange([], total_number=3, total_page=2), "12345", 1, 2)


@pytest.mark.parametrize("pages", [0, 1])
def test_authorized_empty_page_zero_or_one_terminal(pages: int) -> None:
    page, total, _ = audience_page(exchange([], total_page=pages), "12345", 1, 2)
    assert total == 0 and page.terminal and page.records == ()
