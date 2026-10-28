"""固定官方SDK 1.3计划/广告组原始协议；不由类型推断单位。"""

import json
import re
from typing import Literal

from pydantic import SecretStr

from yuliang.modules.connectors.adq_assets import AdqAssetParameters, AssetPage, native_integer
from yuliang.modules.connectors.adq_paging import verify_adq_page
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.http import HttpExchange, OperationSpec
from yuliang.modules.connectors.native_reads import decode_verdict
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.connectors.sync_store import MirrorRecord, SyncPage

PlanLevel = Literal["CAMPAIGN", "ADGROUP"]
COMMON = (
    "campaign_id",
    "configured_status",
    "daily_budget",
    "total_budget",
    "created_time",
    "last_modified_time",
    "is_deleted",
)
FIELDS: dict[PlanLevel, tuple[str, ...]] = {
    "CAMPAIGN": (*COMMON, "campaign_name"),
    "ADGROUP": (
        *COMMON,
        "adgroup_id",
        "adgroup_name",
        "bid_amount",
        "deep_conversion_worth_rate",
        "optimization_goal",
        "billing_event",
    ),
}
POLICY = ArchivePolicy(fields=frozenset({*COMMON, *FIELDS["ADGROUP"], "campaign_name", "level"}))
ARCHIVE = ArchivePolicy(
    fields=POLICY.fields
    | frozenset(
        {"code", "data", "list", "page_info", "page", "page_size", "total_number", "total_page"}
    )
)


def plan_spec(ctx: GatewayContext, level: PlanLevel) -> OperationSpec:
    if (ctx.connector_id, ctx.provider, ctx.product_line) != ("adq", "tencent", "adq"):
        raise ValueError("计划读取只适用ADQ")
    if level not in FIELDS:
        raise ValueError("未知原生计划层级")
    return OperationSpec(
        key=f"adq.plans.{level.lower()}",
        catalog_id=ctx.catalog_id,
        catalog_sha256=ctx.catalog_sha256,
        connector_id="adq",
        provider="tencent",
        product_line="adq",
        host="api.e.qq.com",
        method="GET",
        path="/v1.3/campaigns/get" if level == "CAMPAIGN" else "/v1.3/adgroups/get",
        effect="READ",
        parameters_model=AdqAssetParameters,
        account_parameter="account_id",
        cursor_parameter="page",
        initial_cursor="1",
        idempotent_read=True,
    )


def plan_page(
    ctx: GatewayContext, level: PlanLevel, exchange: HttpExchange, number: int, size: int
) -> AssetPage:
    if decode_verdict(exchange).decision != "CONFIRMED_SUCCESS":
        raise ValueError("平台未确认计划读取成功")
    data = json.loads(exchange.body).get("data")
    if not isinstance(data, dict):
        raise ValueError("缺少计划分页")
    total, pages = verify_adq_page(data, number, size)
    if total > 10000:
        raise ValueError("完整计划范围超出10000上限")
    records: list[MirrorRecord] = []
    for row in data["list"]:
        if not isinstance(row, dict):
            raise ValueError("计划记录不是对象")
        if (
            "account_id" in row
            and native_integer(row["account_id"], positive=True) != ctx.native_account_id
        ):
            raise ValueError("计划属于其他账户")
        values: dict[str, object] = {"level": level}
        for key in FIELDS[level]:
            value = row.get(key)
            if value is None:
                values[key] = None
            elif key in {"campaign_id", "adgroup_id"}:
                values[key] = native_integer(value, positive=True)
            elif key in {
                "daily_budget",
                "total_budget",
                "bid_amount",
                "created_time",
                "last_modified_time",
            }:
                if type(value) is int:
                    number_value = value
                elif isinstance(value, str) and re.fullmatch(r"-?(?:0|[1-9][0-9]{0,18})", value):
                    number_value = int(value)
                else:
                    raise ValueError("原生字段不是精确int64")
                if not -9223372036854775808 <= number_value <= 9223372036854775807:
                    raise ValueError("原生字段超出int64")
                values[key] = str(number_value)
            elif key == "is_deleted":
                if type(value) is not bool:
                    raise ValueError("删除状态需明确布尔值")
                values[key] = value
            elif key == "deep_conversion_worth_rate":
                # JSON直接Decimal，避免float截断；字符串仅接受精确非负十进制。
                from decimal import Decimal

                raw = json.loads(exchange.body, parse_float=Decimal)["data"]["list"][
                    len(records)
                ].get(key)
                if isinstance(raw, bool) or not isinstance(raw, (int, Decimal, str)):
                    raise ValueError("ROI原始值不是精确数字")
                token = str(raw)
                if not re.fullmatch(r"[0-9]{1,19}(?:\.[0-9]{1,12})?", token):
                    raise ValueError("ROI超出精确观察范围")
                values[key] = token
            else:
                if (
                    not isinstance(value, str)
                    or not 1 <= len(value) <= 180
                    or any(ord(c) < 32 for c in value)
                ):
                    raise ValueError("原生名称或状态非法")
                values[key] = value
        native = values["campaign_id" if level == "CAMPAIGN" else "adgroup_id"]
        if not isinstance(native, str) or values["campaign_id"] is None:
            raise ValueError("缺少原生计划层级ID")
        records.append(MirrorRecord(native_id=native, values=values))
    terminal = number >= pages
    return AssetPage(
        SyncPage(
            tuple(records),
            None if terminal else SecretStr(str(number + 1)),
            terminal,
            exchange.received_at,
        ),
        number,
        size,
        total,
        pages,
    )
