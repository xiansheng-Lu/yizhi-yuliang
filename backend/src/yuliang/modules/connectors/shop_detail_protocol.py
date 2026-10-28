"""固定SDK商品详情协议，缺字段保留未知，不采集标题/图像/联系人。"""

import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.http import HttpExchange, OperationSpec
from yuliang.modules.connectors.native_reads import decode_verdict
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.connectors.sync_store import MirrorRecord, SyncPage
from yuliang.platform.errors import ApiError, ErrorCode

DETAIL_FIELDS = frozenset(
    {"errcode", "product", "product_id", "status", "skus", "sku_id", "sale_price", "stock_num"}
)
DETAIL_ARCHIVE_POLICY = ArchivePolicy(fields=DETAIL_FIELDS)
DETAIL_MIRROR_POLICY = ArchivePolicy(
    fields=DETAIL_FIELDS | {"native_status", "native_price", "native_stock", "sku_presence"}
)


def identifier(value: Any) -> str:
    if type(value) is int:
        value = str(value)
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[1-9][0-9]{0,19}", value) is None
        or int(value) > 18446744073709551615
    ):
        raise ValueError("原生标识不是精确uint64")
    return value


def unsigned(value: Any) -> str | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 9223372036854775807:
        raise ValueError("原生数值必须为有界非负整数，不能把空值当0")
    return str(value)


class DetailQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    product_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")


@dataclass(frozen=True)
class DetailPage:
    sync: SyncPage
    product: dict[str, Any]


def detail_spec(ctx: GatewayContext) -> OperationSpec:
    return OperationSpec(
        key="wxshop.product.detail",
        catalog_id=ctx.catalog_id,
        catalog_sha256=ctx.catalog_sha256,
        connector_id="wxshop",
        provider="wxshop",
        product_line="wxshop",
        host="api.weixin.qq.com",
        method="POST",
        path="/channels/ec/product/get",
        effect="READ",
        parameters_model=DetailQuery,
        idempotent_read=True,
    )


def parse_detail(exchange: HttpExchange, requested: str) -> DetailPage:
    requested = identifier(requested)
    if decode_verdict(exchange).decision != "CONFIRMED_SUCCESS":
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "平台未确认商品详情读取成功")
    raw = json.loads(exchange.body).get("product")
    if not isinstance(raw, dict) or identifier(raw.get("product_id")) != requested:
        raise ValueError("商品详情缺失或串商品")
    status = unsigned(raw.get("status"))
    skus = raw.get("skus")
    if skus is not None and (not isinstance(skus, list) or len(skus) > 1000):
        raise ValueError("SKU集合类型或边界非法")
    rows = []
    seen = set()
    for sku in skus or []:
        if not isinstance(sku, dict):
            raise ValueError("SKU必须为对象")
        native = identifier(sku.get("sku_id"))
        if native in seen:
            raise ValueError("SKU标识重复")
        seen.add(native)
        rows.append(
            dict(
                sku_id=native,
                native_price=unsigned(sku.get("sale_price")),
                native_stock=unsigned(sku.get("stock_num")),
                native_status=unsigned(sku.get("status")),
            )
        )
    product = dict(
        product_id=requested,
        native_status=status,
        skus=rows,
        sku_presence="MISSING" if skus is None else "PRESENT",
    )
    return DetailPage(
        SyncPage(
            (MirrorRecord(native_id=requested, values=product),),
            None,
            True,
            exchange.received_at,
            source_at=None,
        ),
        product,
    )
