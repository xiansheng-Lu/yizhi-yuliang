"""官方SDK固定资金/操作日志协议；只提取明确字段，不制造金额单位或平台日志ID。"""

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from yuliang.modules.connectors.adq_paging import verify_adq_page
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.http import HttpExchange, OperationSpec
from yuliang.modules.connectors.native_reads import AdqFundsRead, decode_verdict
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.connectors.sync_store import MirrorRecord, SyncPage
from yuliang.platform.errors import ApiError, ErrorCode

OperationObjectType = Literal[
    "OPERATION_OBJECT_TYPE_ADGROUP",
    "OPERATION_OBJECT_TYPE_DYNAMIC_CREATIVE",
    "OPERATION_OBJECT_TYPE_MARKETING_TARGET_ASSETS",
    "OPERATION_OBJECT_TYPE_JOINT_BUDGET",
]
FUNDS_FIELDS = ("fund_type", "balance", "fund_status", "realtime_cost")
OPERATION_FIELDS = ("operation_action", "operation_object_id", "operator_platform", "created_time")
FINANCE_MIRROR_POLICY = ArchivePolicy(
    fields=frozenset(
        {
            "fund_type",
            "native_balance",
            "native_realtime_cost",
            "fund_status",
            "unit_state",
        }
    )
)
FINANCE_ARCHIVE_POLICY = ArchivePolicy(
    fields=ArchivePolicy().fields
    | frozenset(
        {
            *FUNDS_FIELDS,
            *OPERATION_FIELDS,
            "page_info",
            "page",
            "page_size",
            "total_number",
            "total_page",
        }
    )
)


class OperationQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_object_type: OperationObjectType
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def window(self) -> Self:
        start, end = date.fromisoformat(self.start_date), date.fromisoformat(self.end_date)
        if not 0 <= (end - start).days < 31:
            raise ValueError("操作日志使用有界的1至31天窗口，长区间须分批取证")
        return self


class OperationParameters(OperationQuery):
    account_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    page: int = Field(default=1, strict=True, ge=1, le=100000)
    page_size: int = Field(default=100, strict=True, ge=1, le=100)
    fields: list[str]


def finance_spec(ctx: GatewayContext, kind: Literal["FUNDS", "OPERATIONS"]) -> OperationSpec:
    if (ctx.connector_id, ctx.provider, ctx.product_line) != ("adq", "tencent", "adq"):
        raise ValueError("广告钱包不能替换成小店结算或微信豆")
    return OperationSpec(
        key=f"adq.finance.{kind.lower()}",
        catalog_id=ctx.catalog_id,
        catalog_sha256=ctx.catalog_sha256,
        connector_id="adq",
        provider="tencent",
        product_line="adq",
        host="api.e.qq.com",
        method="GET",
        path="/v1.3/funds/get" if kind == "FUNDS" else "/v3.0/operation_log_list/get",
        effect="READ",
        parameters_model=AdqFundsRead if kind == "FUNDS" else OperationParameters,
        account_parameter="account_id",
        cursor_parameter="page" if kind == "OPERATIONS" else None,
        initial_cursor="1" if kind == "OPERATIONS" else None,
        idempotent_read=True,
    )


def int64_text(value: Any, *, positive: bool = False) -> str:
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r"0|-?[1-9][0-9]{0,18}", value):
        number = int(value)
    else:
        raise ValueError("金额或标识不是可精确核对的int64")
    if not (1 if positive else -9223372036854775808) <= number <= 9223372036854775807:
        raise ValueError("金额或标识超出SDK int64边界")
    return str(number)


def _payload(response: HttpExchange) -> dict[str, Any]:
    if decode_verdict(response).decision != "CONFIRMED_SUCCESS":
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "平台未确认只读资金/日志请求成功")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("响应有重复JSON字段，不能解释为唯一业务结果")
            result[key] = value
        return result

    body = json.loads(response.body, object_pairs_hook=unique)
    payload = body.get("data")
    if not isinstance(payload, dict) or not isinstance(payload.get("list"), list):
        raise ValueError("响应缺少明确list，不当作空数据")
    if len(payload["list"]) > 1000 or any(not isinstance(row, dict) for row in payload["list"]):
        raise ValueError("响应条目超界或不是对象")
    return payload


def _code(value: Any, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", value):
        raise ValueError("原生类型/状态/动作缺少可核对代码")
    return value


@dataclass(frozen=True)
class FinancePage:
    sync: SyncPage
    rows: tuple[dict[str, Any], ...]
    total_number: int
    total_page: int


def _check_account(row: dict[str, Any], native_account_id: str | None) -> None:
    if "account_id" in row and (
        native_account_id is None
        or int64_text(row["account_id"], positive=True) != native_account_id
    ):
        raise ValueError("响应包含其他账户数据，不能拼装到当前广告钱包")


def parse_funds(response: HttpExchange, native_account_id: str | None = None) -> FinancePage:
    payload = _payload(response)
    _check_account(payload, native_account_id)
    records = []
    for row in payload["list"]:
        _check_account(row, native_account_id)
        values = dict(
            fund_type=_code(row.get("fund_type")),
            fund_status=_code(row.get("fund_status"), required=False),
            native_balance=int64_text(row["balance"]) if row.get("balance") is not None else None,
            native_realtime_cost=int64_text(row["realtime_cost"])
            if row.get("realtime_cost") is not None
            else None,
            unit_state="UNCONFIRMED",
        )
        records.append(MirrorRecord(native_id=str(values["fund_type"]), values=values))
    page = SyncPage(tuple(records), None, True, response.received_at)
    return FinancePage(page, tuple(r.values for r in records), len(records), 1)


def parse_operations(
    response: HttpExchange,
    query: OperationQuery,
    page: int,
    size: int,
    native_account_id: str | None = None,
) -> FinancePage:
    payload = _payload(response)
    _check_account(payload, native_account_id)
    total, pages = verify_adq_page(payload, page, size)
    rows = []
    for row in payload["list"]:
        _check_account(row, native_account_id)
        values = dict(
            operation_object_type=query.operation_object_type,
            operation_object_id=int64_text(row.get("operation_object_id"), positive=True),
            operation_action=_code(row.get("operation_action")),
            operator_platform=_code(row.get("operator_platform")),
            native_created_time=int64_text(row["created_time"])
            if row.get("created_time") is not None
            else None,
            native_log_id=None,
            time_unit="UNCONFIRMED",
        )
        rows.append(values)
    terminal = page >= pages
    # SDK无日志ID：以独立观察表保存每行，不向native_id列制造平台事件ID。
    sync = SyncPage(
        (),
        None if terminal else SecretStr(str(page + 1)),
        terminal,
        response.received_at,
        scanned_items=len(rows),
    )
    return FinancePage(sync, tuple(rows), total, pages)
