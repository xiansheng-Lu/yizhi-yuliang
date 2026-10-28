"""冻结business-report-v1草稿输入，正文按纯文本保存。"""

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from yuliang.modules.analysis.coverage import period_days
from yuliang.modules.analysis.models import AnalysisModel, AnalysisQuery


class ReportModule(AnalysisModel):
    id: str = Field(min_length=1, max_length=120)
    title: str = Field(max_length=120)
    text: str = Field(max_length=20000)
    visible: bool = Field(strict=True)
    show_chart: bool = Field(strict=True)
    show_metrics: bool | None = None
    metric_ids: list[str] = Field(max_length=40)
    section_id: str | None = None


class ReportDraft(AnalysisModel):
    schema_version: Literal["business-report-v1"]
    id: UUID
    analysis_id: UUID
    title: str = Field(min_length=1, max_length=120)
    period: Literal["daily", "weekly", "monthly", "custom"]
    query: AnalysisQuery
    shop_name: str
    platform_name: str
    source: Literal["API"]
    updated_at: AwareDatetime
    version: int = Field(strict=True, ge=0)
    metrics: list[dict[str, Any]]
    points: list[dict[str, Any]]
    sections: list[dict[str, Any]]
    insights: list[dict[str, Any]]
    coverage: str
    attribution: str
    modules: list[ReportModule] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def valid(self) -> "ReportDraft":
        days = period_days(self.query)
        if (
            self.period == "daily"
            and len(days) != 1
            or self.period == "weekly"
            and len(days) != 7
            or self.period == "monthly"
            and (days[0].day != 1 or len(days) < 28)
        ):
            raise ValueError("报告周期与分析范围不符")
        if not self.title.strip() or not any(m.visible for m in self.modules):
            raise ValueError("标题不可空且至少一个可见模块")
        if len({m.id for m in self.modules}) != len(self.modules):
            raise ValueError("模块ID重复")
        metrics = {m["id"] for m in self.metrics}
        sections = {s["id"] for s in self.sections}
        if any(
            not set(m.metric_ids) <= metrics
            or (m.section_id is not None and m.section_id not in sections)
            for m in self.modules
        ):
            raise ValueError("模块引用不属于冻结分析")
        return self
