"""API分析结果最小冻结模型；正式叙述/引用增强归M4-03。"""

from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from yuliang.modules.analysis.models import AnalysisModel, AnalysisQuery


class AnalysisResult(AnalysisModel):
    schema_version: Literal["business-analysis-v1"] = "business-analysis-v1"
    id: UUID
    task_id: UUID
    source: Literal["API"] = "API"
    query: AnalysisQuery
    shop_name: str
    platform_name: str
    created_at: str
    updated_at: str | None = None
    completeness: Literal["complete", "partial", "empty"]
    coverage: str
    attribution: str
    comparison: dict[str, Any]
    metrics: list[dict[str, Any]]
    points: list[dict[str, Any]]
    previous_points: list[dict[str, Any]] = Field(default_factory=list)
    year_ago_points: list[dict[str, Any]] = Field(default_factory=list)
    sections: list[dict[str, Any]]
    insights: list[dict[str, Any]] = Field(default_factory=list)
    insight_source: Literal["rules"] = "rules"
    rules: dict[str, Any]

    @model_validator(mode="after")
    def selected_only(self) -> "AnalysisResult":
        if any(m["dimension"] not in self.query.dimensions for m in self.metrics):
            raise ValueError("指标不能超出所选维度")
        if len({m["id"] for m in self.metrics}) != len(self.metrics):
            raise ValueError("指标ID不能重复")
        return self
