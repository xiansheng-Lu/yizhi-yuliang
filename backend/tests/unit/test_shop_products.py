"""商品标识不制造库存、SKU、价格或删除事实。"""

from datetime import UTC, datetime

import pytest

from yuliang.modules.connectors.base import PlatformResourcePage
from yuliang.modules.connectors.shop_products import product_id_page


def page(rows: tuple[dict[str, object], ...], cursor: str | None = None) -> PlatformResourcePage:
    return PlatformResourcePage(
        records=rows, next_cursor=cursor, source_at=None, fetched_at=datetime.now(UTC)
    )


def test_observation_preserves_large_id_and_discards_unproven_fields() -> None:
    result = product_id_page(page(({"product_id": "18446744073709551615", "stock": 0},)))
    assert result.records[0].values == {
        "product_id": "18446744073709551615",
        "visibility": "OBSERVED",
    }
    assert result.terminal and result.source_at is None


@pytest.mark.parametrize("value", [True, 1, "", "0", "-1", "01", "18446744073709551616"])
def test_invalid_native_ids_fail_closed(value: object) -> None:
    with pytest.raises(ValueError):
        product_id_page(page(({"product_id": value},)))


def test_duplicate_ids_and_empty_continuation_fail_closed() -> None:
    with pytest.raises(ValueError):
        product_id_page(page(({"product_id": "1"}, {"product_id": "1"})))
    with pytest.raises(ValueError):
        product_id_page(page((), "next"))
    assert product_id_page(page(())).records == ()
    result = product_id_page(page(({"product_id": "1"},), "next"))
    assert not result.terminal and result.next_cursor is not None


def test_explicit_empty_cursor_is_read_only_and_distinct_from_absence() -> None:
    from dataclasses import replace

    from yuliang.modules.connectors.native_reads import read_spec

    spec = read_spec("wxshop.products", "a" * 20, "a" * 64)
    assert spec.initial_cursor is None
    empty = replace(spec, initial_cursor="")
    assert empty.initial_cursor == ""
    assert replace(spec, initial_cursor="1").initial_cursor == "1"
    for changes in [
        dict(effect="WRITE"),
        dict(idempotent_read=False),
        dict(cursor_parameter=None),
        dict(initial_cursor="0"),
        dict(initial_cursor="opaque"),
    ]:
        with pytest.raises(ValueError):
            replace(empty, **changes)
