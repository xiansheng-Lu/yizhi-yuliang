"""已核验商品标识页转换；列表可见性不证明删除、SKU或库存。"""

import re
from typing import Any

from pydantic import SecretStr

from yuliang.modules.connectors.base import PlatformResourcePage
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.connectors.sync_store import MirrorRecord, SyncPage

PRODUCT_ID_POLICY = ArchivePolicy(fields=frozenset({"product_id", "visibility"}))


def product_id_page(page: PlatformResourcePage, *, requested_size: int = 100) -> SyncPage:
    """只转换已由原生适配确认成功的页，供现有原子页仓储提交。"""
    if type(requested_size) is not int or not 1 <= requested_size <= 100:
        raise ValueError("商品页大小非法")
    if len(page.records) > requested_size:
        raise ValueError("商品页超过请求边界")
    records = []
    for row in page.records:
        value: Any = row.get("product_id")
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[1-9][0-9]{0,19}", value) is None
            or int(value) > 18446744073709551615
        ):
            raise ValueError("商品标识必须为精确正整数文本")
        records.append(
            MirrorRecord(native_id=value, values={"product_id": value, "visibility": "OBSERVED"})
        )
    if page.next_cursor is not None and (
        not page.next_cursor or len(page.next_cursor) > 2048 or not records
    ):
        raise ValueError("非终页须包含记录与有界游标")
    return SyncPage(
        records=tuple(records),
        next_cursor=SecretStr(page.next_cursor) if page.next_cursor else None,
        terminal=page.next_cursor is None,
        fetched_at=page.fetched_at,
        source_at=page.source_at,
    )
