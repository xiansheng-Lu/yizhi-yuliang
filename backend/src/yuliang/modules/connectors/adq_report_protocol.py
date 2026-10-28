"""固定官方1.3 ADVERTISER报表协议；原生金额及小时编码保持精确且不猜含义。"""

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Self

from pydantic import Field, SecretStr, model_validator

from yuliang.modules.connectors.adq_finance_protocol import int64_text
from yuliang.modules.connectors.adq_paging import verify_adq_page
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.http import HttpExchange, OperationSpec
from yuliang.modules.connectors.native_reads import decode_verdict
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.connectors.sync_store import SyncPage
from yuliang.modules.reporting.adq_contracts import ReportKind, TimeLine
from yuliang.modules.reporting.models import Frozen
from yuliang.platform.errors import ApiError, ErrorCode

VALUE_FIELDS = ("view_count", "valid_click_count", "cost", "order_amount")
REPORT_MIRROR_POLICY = ArchivePolicy(fields=frozenset())
REPORT_ARCHIVE_POLICY = ArchivePolicy(
    fields=ArchivePolicy().fields
    | frozenset(
        {
            "account_id",
            "date",
            "hour",
            *VALUE_FIELDS,
            "page_info",
            "page",
            "page_size",
            "total_number",
            "total_page",
        }
    )
)


class ReportQuery(Frozen):
    kind: ReportKind = "DAILY"
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    time_line: TimeLine

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if (
            not 0
            <= (date.fromisoformat(self.end_date) - date.fromisoformat(self.start_date)).days
            < 31
        ):
            raise ValueError("报表使用内部有界1至31天窗口，长区间分批")
        return self


class ReportDateRange(Frozen):
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class ReportParameters(Frozen):
    account_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    level: Literal["REPORT_LEVEL_ADVERTISER"] = "REPORT_LEVEL_ADVERTISER"
    date_range: ReportDateRange
    time_line: TimeLine
    page: int = Field(default=1, strict=True, ge=1, le=100000)
    page_size: int = Field(default=100, strict=True, ge=1, le=100)
    fields: list[str]


def report_spec(ctx: GatewayContext, kind: ReportKind) -> OperationSpec:
    if (ctx.connector_id, ctx.provider, ctx.product_line) != ("adq", "tencent", "adq"):
        raise ValueError("广告整账户报表不能混入小店/微信豆")
    return OperationSpec(
        key=f"adq.report.{kind.lower()}",
        catalog_id=ctx.catalog_id,
        catalog_sha256=ctx.catalog_sha256,
        connector_id="adq",
        provider="tencent",
        product_line="adq",
        host="api.e.qq.com",
        method="GET",
        effect="READ",
        path="/v1.3/daily_reports/get" if kind == "DAILY" else "/v1.3/hourly_reports/get",
        parameters_model=ReportParameters,
        account_parameter="account_id",
        cursor_parameter="page",
        initial_cursor="1",
        idempotent_read=True,
    )


@dataclass(frozen=True)
class ReportPage:
    sync: SyncPage
    rows: tuple[dict[str, Any], ...]
    total_number: int
    total_page: int


def parse_report(
    response: HttpExchange,
    kind: ReportKind,
    day: date,
    native_account: str,
    page: int,
    size: int,
) -> ReportPage:
    if decode_verdict(response).decision != "CONFIRMED_SUCCESS":
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "平台未确认报表只读成功")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("报表包含重复JSON字段")
            result[key] = value
        return result

    payload = json.loads(response.body, object_pairs_hook=unique).get("data")
    if not isinstance(payload, dict):
        raise ValueError("报表缺少明确data")
    total, pages = verify_adq_page(payload, page, size)
    rows = []
    keys = set()
    for row in payload["list"]:
        if (
            not isinstance(row, dict)
            or int64_text(row.get("account_id"), positive=True) != native_account
        ):
            raise ValueError("报表原生账户缺失或跨账户")
        if kind == "DAILY" and row.get("date") != day.isoformat():
            raise ValueError("日报日期与实际逐日查询不一致")
        hour = int64_text(row.get("hour")) if kind == "HOURLY" else None
        key = hour if hour is not None else day.isoformat()
        if key in keys:
            raise ValueError("同一整账户日期/小时重复，不能累加报表总量")
        keys.add(key)
        rows.append(
            dict(
                native_key=key,
                account_id=native_account,
                query_date=day.isoformat(),
                native_hour=hour,
                **{
                    name: int64_text(row[name]) if row.get(name) is not None else None
                    for name in VALUE_FIELDS
                },
            )
        )
    terminal = page >= pages
    return ReportPage(
        SyncPage(
            (),
            None if terminal else SecretStr(str(page + 1)),
            terminal,
            response.received_at,
            scanned_items=len(rows),
        ),
        tuple(rows),
        total,
        pages,
    )
