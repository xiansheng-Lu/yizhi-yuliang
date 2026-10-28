"""内容AI专用合同；不使用command_parse解释对象。"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yuliang.modules.content.models import Brief

Scenario = Literal["content_topic", "content_planning", "content_script", "content_storyboard"]


class ContentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ContentPrepare(ContentModel):
    requestKey: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
    expectedRunVersion: int = Field(ge=1, le=2147483646)
    expectedAttempt: int = Field(ge=1, le=2147483646)
    documentId: str
    documentVersion: int = Field(ge=1, le=2147483646)
    aiScenario: Scenario
    assetIds: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def ids(self) -> "ContentPrepare":
        UUID(self.documentId)
        for identifier in self.assetIds:
            UUID(identifier)
        if len(set(self.assetIds)) != len(self.assetIds):
            raise ValueError("素材不可重复")
        return self


class ContentConfirm(ContentModel):
    quoteHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    maximumCostMinor: int = Field(ge=0)
    currency: Literal["CNY", "USD"]
    confirmed: Literal[True]


class ContextAsset(ContentModel):
    kind: Literal["image", "text"]
    name: str = Field(max_length=120)


class ContentContext(ContentModel):
    brief: Brief
    body: str = Field(max_length=10000)
    assets: list[ContextAsset] = Field(default_factory=list, max_length=8)
    # 内部标识与token不出站，授权绑定留服务端事实表。
    productSource: Literal["OPERATOR_BRIEF"] = "OPERATOR_BRIEF"


class BodyOutput(ContentModel):
    body: str = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def nonblank(self) -> "BodyOutput":
        if not self.body.strip():
            raise ValueError("正文不可空白")
        return self


class SceneOutput(ContentModel):
    title: str = Field(max_length=120)
    narration: str = Field(max_length=2000)
    visual: str = Field(max_length=2000)
    duration: float = Field(ge=0.1, le=600, allow_inf_nan=False)


class StoryboardOutput(ContentModel):
    scenes: list[SceneOutput] = Field(min_length=1, max_length=20)


def output_patch(scenario: Scenario, value: dict[str, Any]) -> dict[str, Any]:
    from uuid import uuid4

    if scenario == "content_storyboard":
        parsed = StoryboardOutput.model_validate(value)
        return dict(scenes=[dict(id=str(uuid4())) | scene.model_dump() for scene in parsed.scenes])
    return BodyOutput.model_validate(value).model_dump()
