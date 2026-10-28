"""Serializable OverviewLoader boundary; no speculative HTTP route."""

from datetime import date, timedelta
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from yuliang.modules.configuration.models import V9Model
from yuliang.modules.reporting.query_models import QueryScope


class ReportingScope(V9Model):
    # Frontend ReportingScope deliberately uses snake_case.
    shop_id: str = Field(alias="shop_id")
    live_session_id: str = Field(alias="live_session_id")
    connector_id: str = Field(alias="connector_id")
    date: str
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"


class OverviewFilter(V9Model):
    scope: ReportingScope
    start_date: str
    end_date: str


class OverviewRequest(V9Model):
    filter: OverviewFilter
    accounts: tuple[QueryScope, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def exact_scope(self) -> Self:
        date.fromisoformat(self.filter.scope.date)
        first, last = (
            date.fromisoformat(self.filter.start_date),
            date.fromisoformat(self.filter.end_date),
        )
        try:
            first - timedelta(days=(last - first).days + 1)
        except OverflowError as exc:
            raise ValueError("前期日期超出可表示范围") from exc
        seen = set()
        for a in self.accounts:
            if (a.product_line, a.provider) != ("adq", "tencent") or not a.account_id:
                raise ValueError("Overview投放指标仅支持已规范ADQ账户，不混经营GMV")
            if (a.start, a.end) != (self.filter.start_date, self.filter.end_date):
                raise ValueError("账户日期必须精确匹配筛选日期")
            if self.filter.scope.shop_id not in {"all", str(a.shop_id)}:
                raise ValueError("店铺范围不匹配")
            if self.filter.scope.connector_id not in {"all", "adq"}:
                raise ValueError("渠道范围不匹配")
            key = a.account_id
            if key in seen:
                raise ValueError("冻结账户范围不可重复")
            seen.add(key)
        if self.filter.scope.live_session_id != "all":
            raise ValueError("缺直播场次规范维度，不把账户金额摊派至场次")
        return self


class OverviewTotals(V9Model):
    spend: int | None = None
    gmv: int | None = None
    roi: float | None = None
    orders: int | None = None
    conversion: float | None = None
    cpm: float | None = None


class OverviewReport(V9Model):
    source: Literal["API"] = "API"
    filter: OverviewFilter
    updated_at: str | None = None
    coverage_note: str
    totals: OverviewTotals
    previous: OverviewTotals | None = None
    trend: dict[str, Any]
    channels: list[dict[str, Any]]
    lives: list[dict[str, Any]] = Field(default_factory=list)
    distributions: dict[str, list[dict[str, Any]]]
    funnel: dict[str, Any]
    traffic: dict[str, Any]
    ai: dict[str, int | float | None]
    insights: list[dict[str, str]] = Field(default_factory=list)
