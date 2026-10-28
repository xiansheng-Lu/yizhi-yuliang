"""共用内容规则：结构/迭代无环/可信素材边界；purpose不授予业务执行能力。"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import ValidationError

from yuliang.modules.content.validation_errors import detail, schema_details
from yuliang.platform.errors import ApiError, ErrorCode

if TYPE_CHECKING:
    from yuliang.modules.content.models import Document

Issue = dict[str, str]


def structure_issues(doc: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []

    def fail(path: str, code: str) -> None:
        if len(issues) < 20:
            issues.append(dict(field=path, code=code))

    def date(value: str, path: str) -> None:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            fail(path, "TIME")

    def uuid(value: str, path: str) -> None:
        try:
            UUID(value)
        except ValueError:
            fail(path, "UUID")

    def bounded(value: Any, path: str = "", depth: int = 0) -> None:
        if depth > 15:
            fail(path, "DEPTH")
            return
        if isinstance(value, list):
            if len(value) > 1000:
                fail(path, "COUNT")
                return
            for index, child in enumerate(value):
                bounded(child, f"{path}.{index}" if path else str(index), depth + 1)
        elif isinstance(value, dict):
            for key, child in value.items():
                bounded(child, f"{path}.{key}" if path else key, depth + 1)
        elif isinstance(value, float) and not math.isfinite(value):
            fail(path, "FINITE")

    bounded(doc)
    if len(json.dumps(doc, ensure_ascii=False).encode()) > 1048576:
        fail("", "SIZE")
    if any(v["code"] in {"SIZE", "COUNT", "DEPTH"} for v in issues):
        return issues
    if not doc["title"].strip():
        fail("title", "TITLE")
    date(doc["updatedAt"], "updatedAt")
    uuid(doc["id"], "id")
    if doc["productId"]:
        uuid(doc["productId"], "productId")
    if doc["parentId"]:
        uuid(doc["parentId"], "parentId")
    if not 1 <= doc["settings"]["duration"] <= 600:
        fail("settings.duration", "DURATION")
    for index, scene in enumerate(doc["scenes"]):
        if not 0.1 <= scene["duration"] <= 600:
            fail(f"scenes.{index}.duration", "SCENE_DURATION")
    nodes = doc["nodes"]
    edges = doc["edges"]
    seen: set[str] = set()
    graph: dict[str, list[tuple[str, int]]] = {}
    for index, node in enumerate(nodes):
        if node["id"] in seen:
            fail(f"nodes.{index}.id", "DUPLICATE_NODE")
        seen.add(node["id"])
        graph.setdefault(node["id"], [])
    for index, edge in enumerate(edges):
        missing = False
        for field in ["sourceNodeId", "targetNodeId"]:
            if edge[field] not in seen:
                fail(f"edges.{index}.{field}", "MISSING_NODE")
                missing = True
        if not missing:
            graph[edge["sourceNodeId"]].append((edge["targetNodeId"], index))
    # 显式栈DFS覆盖每个断开子图，不受Python递归深度限制。
    colors: dict[str, int] = {}
    for root in graph:
        if colors.get(root):
            continue
        colors[root] = 1
        stack = [(root, iter(graph[root]))]
        while stack:
            source, children = stack[-1]
            child = next(children, None)
            if child is None:
                colors[source] = 2
                stack.pop()
                continue
            target, index = child
            if colors.get(target) == 1:
                fail(f"edges.{index}.targetNodeId", "CYCLE")
            elif not colors.get(target):
                colors[target] = 1
                stack.append((target, iter(graph[target])))
    asset_fields: list[tuple[str, str]] = []
    for index, board in enumerate(doc["boards"]):
        path = f"boards.{index}"
        if re.fullmatch(r"#[0-9a-fA-F]{6}", board["background"]) is None:
            fail(path + ".background", "COLOR")
        for field in ["width", "height"]:
            value = board[field]
            if (
                not math.isfinite(value)
                or not float(value).is_integer()
                or not 100 <= value <= 8192
            ):
                fail(path + "." + field, "BOARD_SIZE")
        for sub, layer in enumerate(board["layers"]):
            lp = f"{path}.layers.{sub}"
            if re.fullmatch(r"#[0-9a-fA-F]{6}", layer["color"]) is None:
                fail(lp + ".color", "COLOR")
            for field in ["width", "height", "fontSize"]:
                if layer[field] <= 0:
                    fail(lp + "." + field, "LAYER_SIZE")
            if layer["type"] == "image" and not layer["assetId"]:
                fail(lp + ".assetId", "IMAGE_REQUIRED")
            asset_fields.append((layer["assetId"], lp + ".assetId"))
    for index, clip in enumerate(doc["clips"]):
        path = f"clips.{index}"
        if clip["track"] == "caption" and not clip["text"].strip():
            fail(path + ".text", "CAPTION_TEXT")
        if clip["track"] != "caption" and not clip["assetId"]:
            fail(path + ".assetId", "ASSET_REQUIRED")
        for field, valid in [
            ("start", clip["start"] >= 0),
            ("sourceStart", clip["sourceStart"] >= 0),
            ("duration", clip["duration"] >= 0.05),
            ("volume", 0 <= clip["volume"] <= 1),
        ]:
            if not valid:
                fail(path + "." + field, "CLIP")
        asset_fields.append((clip["assetId"], path + ".assetId"))
    asset_fields.extend(
        (node["assetId"], f"nodes.{index}.assetId") for index, node in enumerate(nodes)
    )
    for index, message in enumerate(doc.get("messages") or []):
        date(message["createdAt"], f"messages.{index}.createdAt")
        asset_fields.extend(
            (value, f"messages.{index}.assetIds.{sub}")
            for sub, value in enumerate(message.get("assetIds") or [])
        )
    if len({value for value, _ in asset_fields if value}) > 32:
        fail("", "ASSET_COUNT")
    for value, path in asset_fields:
        if value:
            uuid(value, path)
    return issues


@dataclass(frozen=True)
class MediaFact:
    kind: Literal["image", "video", "audio", "text"]
    duration: Decimal | None
    duration_verified: bool


def media_issues(document: Document, facts: Mapping[UUID, MediaFact] | None) -> list[Issue]:
    issues = []
    for index, clip in enumerate(document.clips):
        if clip.track == "caption":
            continue
        fact = (facts or {}).get(UUID(clip.assetId)) if clip.assetId else None
        path = f"clips.{index}.assetId"
        if fact is None:
            code = "MEDIA_MISSING"
        elif fact.kind != clip.track:
            code = "MEDIA_KIND"
        elif (
            not fact.duration_verified
            or not isinstance(fact.duration, Decimal)
            or not fact.duration.is_finite()
            or fact.duration < 0
        ):
            code = "MEDIA_DURATION"
        elif Decimal(str(clip.sourceStart)) + Decimal(str(clip.duration)) > fact.duration + Decimal(
            "0.01"
        ):
            code = "MEDIA_BOUND"
            path = f"clips.{index}.duration"
        else:
            continue
        issues.append(dict(field=path, code=code))
    return issues[:20]


def validate_content(
    value: Any,
    *,
    purpose: Literal["SAVE", "GENERATION", "SOP"] = "SAVE",
    media: Mapping[UUID, MediaFact] | None = None,
) -> Document:
    """三个调用方使用同一判断；没有业务执行/供应商调用或权限自授。"""
    from yuliang.modules.content.models import Document

    if purpose not in {"SAVE", "GENERATION", "SOP"}:
        raise ValueError("未知内容校验调用方")
    raw = value.model_dump(warnings=False) if isinstance(value, Document) else value
    try:
        document = Document.model_validate(raw)
    except ValidationError as exc:
        raise ApiError(
            ErrorCode.INVALID_INPUT, "内容校验未通过", details=schema_details(exc.errors(), raw)
        ) from None
    issues = media_issues(document, media)
    if issues:
        raise ApiError(
            ErrorCode.INVALID_INPUT,
            "内容校验未通过",
            details=[detail(v["field"], v["code"], raw) for v in issues],
        )
    return document
