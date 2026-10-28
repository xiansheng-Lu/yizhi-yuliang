"""按已知订单ID读取与独立商家结算观察；不采集订单PII。"""

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from yuliang.modules.connectors.http import HttpExchange, OperationSpec
from yuliang.modules.connectors.native_reads import decode_verdict
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.connectors.sync_store import MirrorRecord, SyncPage

Kind = Literal["ORDER", "SETTLEMENT", "ORDER_LIST", "AFTERSALE", "AFTERSALE_LIST"]
FIELDS = frozenset(
    {
        "order_id",
        "status",
        "create_time",
        "update_time",
        "order_price",
        "available_amount",
        "pending_amount",
        "wallet_kind",
        "unit_status",
        "source_time_status",
        "coverage",
        "refund_status",
        "after_sale_order_id",
        "refund_amount",
    }
)
ORDER_POLICY = ArchivePolicy(fields=FIELDS)
ORDER_ARCHIVE_POLICY = ArchivePolicy(
    fields=FIELDS
    | {
        "errcode",
        "order",
        "order_detail",
        "price_info",
        "after_sale_order",
        "refund_info",
        "amount",
        "order_id_list",
        "after_sale_order_id_list",
        "has_more",
    }
)


class OrderRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    order_id: str = Field(pattern=r"^[1-9][0-9]{0,39}$")


class BalanceRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start_time: int = Field(ge=0, strict=True)
    end_time: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def valid(self) -> "TimeRange":
        if self.end_time < self.start_time or self.end_time - self.start_time > 604800:
            raise ValueError("本地单次窗口须为最多七天")
        return self


class OrderListRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    update_time_range: TimeRange
    page_size: int = Field(default=100, ge=1, le=100, strict=True)
    next_key: str = Field(default="", max_length=2048)


class AftersaleRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    after_sale_order_id: str = Field(pattern=r"^[1-9][0-9]{0,39}$")


class AftersaleListRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    begin_create_time: int = Field(ge=0, strict=True)
    end_create_time: int = Field(ge=0, strict=True)
    next_key: str = Field(default="", max_length=2048)

    @model_validator(mode="after")
    def valid(self) -> "AftersaleListRead":
        TimeRange(start_time=self.begin_create_time, end_time=self.end_create_time)
        return self


SPECS: dict[Kind, tuple[str, type[BaseModel]]] = {
    "ORDER": ("/channels/ec/order/get", OrderRead),
    "SETTLEMENT": ("/channels/ec/funds/getbalance", BalanceRead),
    "ORDER_LIST": ("/channels/ec/order/list/get", OrderListRead),
    "AFTERSALE": ("/channels/ec/aftersale/getaftersaleorder", AftersaleRead),
    "AFTERSALE_LIST": ("/channels/ec/aftersale/getaftersalelist", AftersaleListRead),
}


def shop_order_spec(kind: Kind, catalog_id: str, digest: str) -> OperationSpec:
    path, model = SPECS[kind]
    paged = kind in {"ORDER_LIST", "AFTERSALE_LIST"}
    return OperationSpec(
        key="wxshop." + kind.lower(),
        catalog_id=catalog_id,
        catalog_sha256=digest,
        connector_id="wxshop",
        provider="wxshop",
        product_line="wxshop",
        host="api.weixin.qq.com",
        method="POST",
        effect="READ",
        path=path,
        parameters_model=model,
        idempotent_read=True,
        cursor_parameter="next_key" if paged else None,
        initial_cursor="" if paged else None,
    )


def integer(value: Any) -> str | None:
    if value is None:
        return None
    if type(value) is int:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]{1,40}", value):
        return str(int(value))
    raise ValueError("原生金额/时间必须是精确整数")


def order_observation(kind: Kind, response: HttpExchange, order_id: str | None) -> SyncPage:
    if decode_verdict(response).decision != "CONFIRMED_SUCCESS":
        raise ValueError("平台未确认读取成功")
    raw = json.loads(response.body)
    values: dict[str, Any] = dict(
        unit_status="UNCONFIRMED",
        source_time_status="UNCONFIRMED",
        coverage="SINGLE_ENTITY_OBSERVATION",
        refund_status="NOT_PROVIDED",
    )
    if kind in {"ORDER_LIST", "AFTERSALE_LIST"}:
        key = "order_id_list" if kind == "ORDER_LIST" else "after_sale_order_id_list"
        ids = raw.get(key)
        more = raw.get("has_more")
        cursor = raw.get("next_key")
        if (
            not isinstance(ids, list)
            or type(more) is not bool
            or (more and (not isinstance(cursor, str) or not cursor or not ids))
        ):
            raise ValueError("列表缺少明确完整分页")
        records = []
        for native in ids:
            if not isinstance(native, str) or re.fullmatch(r"[1-9][0-9]{0,39}", native) is None:
                raise ValueError("列表ID非法")
            records.append(
                MirrorRecord(
                    native_id=native,
                    values={
                        "order_id" if kind == "ORDER_LIST" else "after_sale_order_id": native,
                        "coverage": "LIST_ID_ONLY",
                    },
                )
            )
        return SyncPage(
            records=tuple(records),
            next_cursor=SecretStr(cursor) if more else None,
            terminal=not more,
            fetched_at=response.received_at,
        )
    if kind == "AFTERSALE":
        order = raw.get("after_sale_order")
        if not isinstance(order, dict) or order.get("after_sale_order_id") != order_id:
            raise ValueError("售后单ID不符")
        values.update(
            after_sale_order_id=order_id,
            order_id=order.get("order_id"),
            refund_status=order.get("status"),
        )
        if not isinstance(values["refund_status"], str) or len(values["refund_status"]) > 80:
            raise ValueError("售后状态非法")
        for key in ("create_time", "update_time"):
            values[key] = integer(order.get(key))
        refund = order.get("refund_info", {})
        if not isinstance(refund, dict):
            raise ValueError("退款结构非法")
        values["refund_amount"] = integer(refund.get("amount"))
        native = str(order_id)
    elif kind == "ORDER":
        order = raw.get("order")
        if not isinstance(order, dict) or str(order.get("order_id")) != order_id:
            raise ValueError("订单ID与请求不符")
        values.update(order_id=order_id)
        for key in ("status", "create_time", "update_time"):
            values[key] = integer(order.get(key))
        detail = order.get("order_detail", {})
        if not isinstance(detail, dict) or not isinstance(detail.get("price_info", {}), dict):
            raise ValueError("订单金额结构非法")
        values["order_price"] = integer(detail.get("price_info", {}).get("order_price"))
        native = str(order_id)
    else:
        if not any(k in raw for k in ("available_amount", "pending_amount")):
            raise ValueError("商家结算响应缺少金额")
        values.update(
            wallet_kind="MERCHANT_SETTLEMENT",
            available_amount=integer(raw.get("available_amount")),
            pending_amount=integer(raw.get("pending_amount")),
        )
        native = "MERCHANT_SETTLEMENT"
    return SyncPage(
        records=(MirrorRecord(native_id=native, values=values),),
        next_cursor=None,
        terminal=True,
        fetched_at=response.received_at,
        source_at=None,
    )
