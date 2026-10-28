"""前端五种文档完整字段；version是单文档锁，不接受容器revision。"""

import json
import math
import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
        if not self.title.strip():
            raise ValueError("标题不能为空")
        datetime.fromisoformat(self.updatedAt.replace("Z", "+00:00"))
        if not 1 <= self.settings.duration <= 600:
            raise ValueError("制作时长需1至600秒")
        node_ids = {v.id for v in self.nodes}
        if len(node_ids) != len(self.nodes) or any(
            v.sourceNodeId not in node_ids or v.targetNodeId not in node_ids for v in self.edges
        ):
            raise ValueError("节点重复或边引用不存在")
        if any(not 0.1 <= v.duration <= 600 for v in self.scenes):
            raise ValueError("镜头时长非法")
        for board in self.boards:
            if re.fullmatch(r"#[0-9a-fA-F]{6}", board.background) is None:
                raise ValueError("画板颜色非法")
            if any(not n.is_integer() or not 100 <= n <= 8192 for n in [board.width, board.height]):
                raise ValueError("画板尺寸非法")
            for layer in board.layers:
                if re.fullmatch(r"#[0-9a-fA-F]{6}", layer.color) is None:
                    raise ValueError("图层颜色非法")
                if layer.type == "image" and not layer.assetId:
                    raise ValueError("图片图层需要已确认素材")
                if min(layer.width, layer.height, layer.fontSize) <= 0:
                    raise ValueError("图层尺寸非法")
        for clip in self.clips:
            if clip.track != "caption" and not clip.assetId:
                raise ValueError("音视频剪辑需要已确认素材")
            if (
                clip.start < 0
                or clip.sourceStart < 0
                or clip.duration < 0.05
                or not 0 <= clip.volume <= 1
            ):
                raise ValueError("剪辑参数非法")
        asset_ids = (
            [v.assetId for v in self.nodes]
            + [v.assetId for v in self.clips]
            + [v.assetId for b in self.boards for v in b.layers]
            + [a for m in self.messages or [] for a in m.assetIds or []]
        )
        if len(set(v for v in asset_ids if v)) > 32:
            raise ValueError("单文档最多引用32个已确认素材")
        for value in asset_ids:
            if value:
                UUID(value)
        for message in self.messages or []:
            datetime.fromisoformat(message.createdAt.replace("Z", "+00:00"))
        UUID(self.id)
        if self.productId:
            UUID(self.productId)
        if self.parentId:
            UUID(self.parentId)

        def check(value: Any, depth: int = 0) -> None:
            if depth > 15:
                raise ValueError("文档层级过深")
            if isinstance(value, list):
                if len(value) > 1000:
                    raise ValueError("文档集合超限")
                for child in value:
                    check(child, depth + 1)
            elif isinstance(value, dict):
                for child in value.values():
                    check(child, depth + 1)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("数值非法")

        check(self.model_dump())
        if len(json.dumps(self.model_dump(), ensure_ascii=False).encode()) > 1048576:
            raise ValueError("文档超过1MiB")
        return self


class DocumentDelete(Model):
    version: int = Field(ge=1, le=2147483646)


class Flow(Model):
    kind: Literal["scripts", "videos", "graphics", "editing"]
