from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError
from test_query_plans import observation, project_adgroups, request, view

from yuliang.modules.reporting.query_adgroup_cursor import offsets, parse_selector, selector
from yuliang.modules.reporting.query_service import QueryService
from yuliang.platform.errors import ApiError, ErrorCode


def test_positions_are_server_bounded() -> None:
    assert offsets(0) == offsets(100) == []
    assert offsets(101) == [100]
    assert len(offsets(10000)) == 99
    for value in (True, -1, 10001):
        with pytest.raises(ApiError):
            offsets(value)


def test_numeric_native_id_order_not_decimal_string_order() -> None:
    source = view() | dict(observations=[observation(str(i)) for i in (10, 2, 1)])
    page = project_adgroups(source, request(), "2026-10-28T00:00:00Z")
    assert [row.native_id for row in page.rows] == ["1", "2", "10"]


def test_selector_is_canonical_locator() -> None:
    identifier = uuid4()
    value = selector(identifier)
    assert value is not None and parse_selector(value) == identifier
    assert selector(None) is None
    for invalid in ("", value.upper(), value + "x", "adgroup-c1." + "z" * 32):
        with pytest.raises(ApiError):
            parse_selector(invalid)


@pytest.mark.parametrize("state,count", [("40001", 3), ("23503", 1)])
def test_retry_is_bounded_and_does_not_swallow_other_db_errors(state: str, count: int) -> None:
    service = QueryService(Mock(), Mock())
    original = Exception("synthetic")
    original.sqlstate = state  # type: ignore[attr-defined]
    failure = DBAPIError(None, None, original)
    service._query_once = Mock(side_effect=failure)  # type: ignore[method-assign]
    if state == "40001":
        with pytest.raises(ApiError) as raised:
            service.query(request())
        assert raised.value.code == ErrorCode.VERSION_CONFLICT
    else:
        with pytest.raises(DBAPIError) as db:
            service.query(request())
        assert db.value is failure
    assert service._query_once.call_count == count
