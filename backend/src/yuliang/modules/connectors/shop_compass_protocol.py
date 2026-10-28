"""固定来源罗盘日观察；未知单位/时区/退款口径不伪造规范事实。"""

import json
import re
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.http import HttpExchange, OperationSpec
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.connectors.sync_store import MirrorRecord, SyncPage

COMPASS_PATH = "/channels/ec/compass/shop/overall/get"
COMPASS_FIELDS = (
    "pay_gmv",
    "pay_uv",
    "pay_order_cnt",
    "pay_refund_gmv",
    "live_pay_gmv",
    "feed_pay_gmv",
    "product_click_uv",
)
COMPASS_POLICY = ArchivePolicy(fields=frozenset({"ds", "state", "values", "gaps", *COMPASS_FIELDS}))
COMPASS_ARCHIVE_POLICY = ArchivePolicy(fields=frozenset({"errcode", "data", *COMPASS_FIELDS}))


class CompassParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    ds: str = Field(pattern=r"^[0-9]{8}$")

    @field_validator("ds")
    @classmethod
    def valid_day(cls, value: str) -> str:
        datetime.strptime(value, "%Y%m%d")
        return value


def compass_spec(ctx: GatewayContext) -> OperationSpec:
    if (ctx.connector_id, ctx.provider, ctx.product_line) != ("wxshop", "wxshop", "wxshop"):
        raise ValueError("罗盘仅适用于独立小店产品线")
    return OperationSpec(
        key="wxshop.compass.overall",
        catalog_id=ctx.catalog_id,
        catalog_sha256=ctx.catalog_sha256,
        connector_id="wxshop",
        provider="wxshop",
        product_line="wxshop",
        host="api.weixin.qq.com",
        method="POST",
        path=COMPASS_PATH,
        effect="READ",
        parameters_model=CompassParameters,
        idempotent_read=True,
    )


def compass_page(exchange: HttpExchange, day: date) -> SyncPage:
    payload = json.loads(exchange.body)
    if (
        exchange.http_status != 200
        or not isinstance(payload, dict)
        or type(payload.get("errcode")) is not int
        or payload["errcode"] != 0
    ):
        raise ValueError("罗盘业务响应未确认成功")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("罗盘响应缺少数据对象")
    values: dict[str, Any] = {}
    gaps = [
        "UNIT_UNCONFIRMED",
        "TIMEZONE_UNCONFIRMED",
        "REFUND_SCOPE_UNCONFIRMED",
        "DEDUP_SCOPE_UNCONFIRMED",
        "SOURCE_TIME_UNAVAILABLE",
    ]
    for field in COMPASS_FIELDS:
        value = data.get(field)
        if value is None:
            values[field] = None
            gaps.append("MISSING:" + field)
        elif type(value) is int and 0 <= value <= 18446744073709551615:
            values[field] = str(value)
        elif (
            isinstance(value, str)
            and re.fullmatch(r"0|[1-9][0-9]{0,19}", value)
            and int(value) <= 18446744073709551615
        ):
            values[field] = value
        else:
            raise ValueError("罗盘指标须为精确非负整数或缺失")
    state = (
        "EMPTY_DATA"
        if not data
        else "OBSERVED"
        if any(v is not None for v in values.values())
        else "MISSING_METRICS"
    )
    record = MirrorRecord(
        native_id=day.strftime("%Y%m%d"),
        values=dict(ds=day.strftime("%Y%m%d"), state=state, values=values, gaps=gaps),
    )
    return SyncPage(
        records=(record,),
        next_cursor=None,
        terminal=True,
        fetched_at=exchange.received_at,
        source_at=None,
    )
