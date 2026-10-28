"""固定官方SDK v3自定义人群只读协议；关键词不生成平台资源。"""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.http import HttpExchange, OperationSpec
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.connectors.sync_store import MirrorRecord, SyncPage

LOOKUP_FIELDS = ("audience_id", "account_id", "name", "status", "online_status")
LOOKUP_POLICY = ArchivePolicy(fields=frozenset(LOOKUP_FIELDS))
LOOKUP_ARCHIVE = ArchivePolicy(
    fields=frozenset(
        {
            "code",
            "data",
            "list",
            "page_info",
            "page",
            "page_size",
            "total_number",
            "total_page",
            *LOOKUP_FIELDS,
        }
    )
)
LOOKUP_PATH = "/v3.0/custom_audiences/get"


class AudienceParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    account_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    page: int = Field(default=1, strict=True, ge=1, le=10000)
    page_size: int = Field(default=100, strict=True, ge=1, le=100)
    fields: list[str]


def audience_spec(ctx: GatewayContext) -> OperationSpec:
    if (ctx.connector_id, ctx.provider, ctx.product_line) != ("adq", "tencent", "adq"):
        raise ValueError("自定义人群来源只适用于ADQ")
    return OperationSpec(
        key="adq.lookup.audiences",
        catalog_id=ctx.catalog_id,
        catalog_sha256=ctx.catalog_sha256,
        connector_id="adq",
        provider="tencent",
        product_line="adq",
        host="api.e.qq.com",
        method="GET",
        path=LOOKUP_PATH,
        effect="READ",
        parameters_model=AudienceParameters,
        account_parameter="account_id",
        cursor_parameter="page",
        initial_cursor="1",
        idempotent_read=True,
    )


def audience_page(
    exchange: HttpExchange, account: str, number: int, size: int
) -> tuple[SyncPage, int, int]:
    body = json.loads(exchange.body)
    if (
        exchange.http_status != 200
        or not isinstance(body, dict)
        or type(body.get("code")) is not int
        or body["code"] != 0
    ):
        raise ValueError("人群响应未确认成功")
    data = body.get("data")
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("list"), list)
        or not isinstance(data.get("page_info"), dict)
    ):
        raise ValueError("人群响应缺少完整分页")
    meta = data["page_info"]
    if any(
        type(meta.get(k)) is not int for k in ("page", "page_size", "total_number", "total_page")
    ):
        raise ValueError("人群分页必须为精确整数")
    total, pages = meta["total_number"], meta["total_page"]
    rows = data["list"]
    if (
        meta["page"] != number
        or meta["page_size"] != size
        or total < 0
        or pages not in ({0, 1} if total == 0 else {(total + size - 1) // size})
        or number > max(1, pages)
        or len(rows)
        != (0 if total == 0 else size if number < pages else total - size * (pages - 1))
    ):
        raise ValueError("人群分页范围不一致")
    records = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("人群行格式非法")
        normalized: dict[str, Any] = {}
        for key in ("account_id", "audience_id"):
            value = row.get(key)
            if type(value) is int and 0 < value <= 9223372036854775807:
                normalized[key] = str(value)
            elif (
                isinstance(value, str)
                and value.isascii()
                and value.isdecimal()
                and value == str(int(value))
                and 0 < int(value) <= 9223372036854775807
            ):
                normalized[key] = value
            else:
                raise ValueError("人群标识不精确")
        if (
            normalized["account_id"] != account
            or not isinstance(row.get("name"), str)
            or not 1 <= len(row["name"]) <= 200
        ):
            raise ValueError("人群账户或名称不匹配")
        for key in ("name", "status", "online_status"):
            value = row.get(key)
            if not isinstance(value, str) or len(value) > 200:
                raise ValueError("人群状态缺失")
            normalized[key] = value
        records.append(MirrorRecord(native_id=normalized["audience_id"], values=normalized))
    terminal = number >= pages
    return (
        SyncPage(
            records=tuple(records),
            next_cursor=None if terminal else SecretStr(str(number + 1)),
            terminal=terminal,
            fetched_at=exchange.received_at,
        ),
        total,
        pages,
    )


TARGET_PATH = "/v3.0/marketing_target_assets/get"
TARGET_FIELDS = ("marketing_asset_id", "marketing_asset_name", "marketing_asset_type", "is_deleted")
TARGET_POLICY = ArchivePolicy(fields=frozenset(TARGET_FIELDS))
TARGET_ARCHIVE = ArchivePolicy(
    fields=frozenset(
        {
            "code",
            "data",
            "list",
            "page_info",
            "page",
            "page_size",
            "total_number",
            "total_page",
            *TARGET_FIELDS,
        }
    )
)
TARGET_TYPES = frozenset(
    {
        "MARKETING_TARGET_TYPE_TRAFFIC",
        "MARKETING_TARGET_TYPE_HOUSE_PROPERTY",
        "MARKETING_TARGET_TYPE_PRODUCT",
        "MARKETING_TARGET_TYPE_WECHAT_STORE_PRODUCT",
        "MARKETING_TARGET_TYPE_WECHAT_STORE_PRODUCT_SET",
        "MARKETING_TARGET_TYPE_PRODUCT_AGGREGATION_PAGE",
    }
)
ASSET_TYPES = frozenset(
    {
        "MARKETING_ASSET_TYPE_TRAFFIC_CAR_MODEL",
        "MARKETING_ASSET_TYPE_REAL_ESTATE_REAL_ESTATE",
        "MARKETING_ASSET_TYPE_PRODUCT_AGGREGATION_PAGE_WECHAT_STORE_PRODUCT_BUNDLES",
    }
)


class TargetParameters(AudienceParameters):
    marketing_target_type: str = Field(pattern=r"^MARKETING_TARGET_TYPE_[A-Z_]{1,80}$")

    @field_validator("marketing_target_type")
    @classmethod
    def fixed_type(cls, value: str) -> str:
        if value not in TARGET_TYPES:
            raise ValueError("目标类型未实现固定来源")
        return value


def target_spec(ctx: GatewayContext) -> OperationSpec:
    from dataclasses import replace

    return replace(
        audience_spec(ctx),
        key="adq.lookup.targets",
        path=TARGET_PATH,
        parameters_model=TargetParameters,
    )


def target_page(
    exchange: HttpExchange, account: str, number: int, size: int
) -> tuple[SyncPage, int, int]:
    from dataclasses import replace

    body = json.loads(exchange.body)
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("data"), dict)
        or not isinstance(body["data"].get("list"), list)
    ):
        raise ValueError("推广资产响应缺少实际资源页")
    originals = []
    normalized = []
    for raw in body["data"]["list"]:
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("marketing_asset_type"), str)
            or not 1 <= len(raw["marketing_asset_type"]) <= 160
        ):
            raise ValueError("推广资产类型缺失")
        deleted = raw.get("is_deleted")
        if deleted is not None and type(deleted) is not bool:
            raise ValueError("推广资产删除标识须为真实布尔值或未知")
        originals.append(raw)
        normalized.append(
            dict(
                audience_id=raw.get("marketing_asset_id"),
                account_id=account,
                name=raw.get("marketing_asset_name"),
                status="OBSERVED",
                online_status="UNKNOWN",
            )
        )
    # 复用精确ID、完整页数和响应业务码检查；注入的account仅表示请求账户，不伪造响应归属证据。
    amended = dict(body) | dict(data=dict(body["data"]) | dict(list=normalized))
    page, total, pages = audience_page(
        replace(exchange, body=json.dumps(amended).encode()), account, number, size
    )
    records = tuple(
        MirrorRecord(
            native_id=record.native_id,
            values=dict(
                marketing_asset_id=record.native_id,
                marketing_asset_name=record.values["name"],
                marketing_asset_type=raw["marketing_asset_type"],
                is_deleted=raw.get("is_deleted"),
            ),
        )
        for record, raw in zip(page.records, originals, strict=True)
    )
    return replace(page, records=records), total, pages
