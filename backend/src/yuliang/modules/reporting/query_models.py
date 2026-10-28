"""实际PlatformService.query冻结请求/响应，不接受自授范围。"""

import json
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from yuliang.modules.configuration.integration_models import PlatformResource, ProductLine, Provider
from yuliang.modules.configuration.models import V9Model


class QueryScope(V9Model):
    tenant_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    business_entity_id: UUID
    shop_id: UUID
    provider: Provider
    product_line: ProductLine
    account_id: str = Field(max_length=36)
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def valid(self) -> "QueryScope":
        first, last = date.fromisoformat(self.start), date.fromisoformat(self.end)
        if not 0 <= (last - first).days <= 365:
            raise ValueError("只读窗口须为有效日期，最多366天")
        if self.account_id and str(UUID(self.account_id)) != self.account_id:
            raise ValueError("账户UUID须规范格式")
        return self

    def key(self) -> str:
        return json.dumps(
            [
                self.tenant_id,
                str(self.business_entity_id),
                str(self.shop_id),
                self.provider,
                self.product_line,
                self.account_id,
                self.start,
                self.end,
            ],
            separators=(",", ":"),
            ensure_ascii=False,
        )


class QueryFilters(V9Model):
    search: str | None = Field(default=None, max_length=120)
    cursor: str | None = Field(default=None, max_length=2048)
    native_parent_id: str | None = Field(default=None, max_length=128)


class QueryRequest(V9Model):
    scope: QueryScope
    resource: PlatformResource
    filters: QueryFilters = Field(default_factory=QueryFilters)


class ResourceRow(V9Model):
    scope_key: str
    id: str
    native_id: str
    name: str
    status: str
    version: int | None = None
    updated_at: str
    metrics: dict[str, int | float | None]
    attribution_window: str | None = None
    money_type: Literal["AD_BUDGET", "AD_CASH_BALANCE", "MERCHANT_SETTLEMENT", "BEAN"] | None = None
    related: list[dict[str, str]] = Field(default_factory=list)


class ResourcePage(V9Model):
    contract_version: Literal["platform-workspace-v9.0"] = "platform-workspace-v9.0"
    source: Literal["API"] = "API"
    scope_key: str
    resource: PlatformResource
    rows: list[ResourceRow]
    next_cursor: str | None = None
    synced_at: str
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    granularity: str
    attribution_window: str | None = None
    refund_handling: str
    deduplication: str
    warnings: list[str]
