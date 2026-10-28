"""前端五种文档完整字段；version是单文档锁，不接受容器revision。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

Kind = Literal["planning", "scripts", "graphics", "videos", "editing"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Brief(Model):
    audience: str = ""
    objective: str = ""
    sellingPoints: str = ""
    channel: str = ""
    requirements: str = ""


class Settings(Model):
    ratio: Literal["9:16", "16:9", "1:1", "4:5"]
    duration: float
    resolution: Literal["720P", "1080P", "4K"]
    skill: str


class Scene(Model):
    id: str
    title: str
    narration: str
    visual: str
    duration: float


class Layer(Model):
    id: str
    type: Literal["text", "image"]
    text: str
    assetId: str
    x: float
    y: float
    width: float
    height: float
    fontSize: float
    color: str
    visible: bool
    locked: bool


class Board(Model):
    id: str
    name: str
    width: float
    height: float
    background: str
    layers: list[Layer]
    x: float | None = None
    y: float | None = None


class Node(Model):
    id: str
    type: Literal["reference", "direction", "generate", "result"]
    x: float
    y: float
    text: str
    assetId: str


class Edge(Model):
    id: str
    sourceNodeId: str
    targetNodeId: str


class Clip(Model):
    id: str
    assetId: str
    track: Literal["video", "audio", "caption"]
    start: float
    sourceStart: float
    duration: float
    text: str
    volume: float


class Message(Model):
    id: str
    text: str
    createdAt: str
    context: str
    assetIds: list[str] | None = None


class Document(Model):
    id: str
    kind: Kind
    title: str = Field(min_length=1, max_length=120)
    productId: str
    parentId: str | None
    version: int = Field(ge=1, le=2147483646)
    updatedAt: str
    status: Literal["draft", "review"]
    brief: Brief
    body: str
    scenes: list[Scene]
    boards: list[Board]
    nodes: list[Node]
    edges: list[Edge]
    clips: list[Clip]
    settings: Settings
    prompt: str
    messages: list[Message] | None = None

    @model_validator(mode="after")
    def bounded(self) -> "Document":
        from yuliang.modules.content.validation import structure_issues

        issues = structure_issues(self.model_dump())
        if issues:
            raise PydanticCustomError("content_rules", "内容字段未通过校验", {"issues": issues})
        return self


class DocumentDelete(Model):
    version: int = Field(ge=1, le=2147483646)


class Flow(Model):
    kind: Literal["scripts", "videos", "graphics", "editing"]
