"""ApiAnalysisAdapter的条件与小写任务状态，不接受客户端阶段/身份。"""

from datetime import date
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

type AnalysisPlatform = Literal[
    "all", "adq", "wxpromo", "beans", "wxshop", "qianchuan", "douyinshop", "juguang", "redshop"
]
type Dimension = Literal["shop", "live", "ads", "video"]


class AnalysisModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        alias_generator=to_camel,
        hide_input_in_errors=True,
    )


class AnalysisQuery(AnalysisModel):
    shop_id: UUID
    platform_id: AnalysisPlatform
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    dimensions: tuple[Dimension, ...] = Field(min_length=1, max_length=4)
    timezone: Literal["Asia/Shanghai"]

    @model_validator(mode="after")
    def actual_range(self) -> Self:
        start, end = date.fromisoformat(self.start_date), date.fromisoformat(self.end_date)
        if not 0 <= (end - start).days <= 365:
            raise ValueError("分析需为有效自然日范围，最长366天")
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("分析维度不能重复")
        return self


class AnalysisCreate(AnalysisModel):
    query: AnalysisQuery
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class AnalysisTask(AnalysisModel):
    id: UUID
    query: AnalysisQuery
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: int = Field(strict=True, ge=0, le=100)
    result_id: UUID | None = None
    error: str | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def actual_progress(self) -> Self:
        if self.status == "succeeded" and (self.result_id is None or self.progress != 100):
            raise ValueError("成功须有真实冻结结果和完整进度")
        if self.status != "succeeded" and (self.result_id is not None or self.progress == 100):
            raise ValueError("未成功不得提前展示结果或100%")
        if self.status == "queued" and self.progress != 0:
            raise ValueError("排队不得伪造计算进度")
        if (self.status == "failed") != bool(self.error):
            raise ValueError("失败须有受控原因，其他阶段不能附失败结论")
        return self
