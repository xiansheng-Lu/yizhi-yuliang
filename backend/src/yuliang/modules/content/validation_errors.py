"""内容校验错误的受控字段定位，不回显原始input/ctx/异常文本。"""

import re
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

REASONS = {
    "CAPTION_TEXT": "字幕片段需要非空文本",
    "ID_MISMATCH": "路径与文档标识不一致",
    "TITLE": "标题不能为空且最多120字",
    "TIME": "时间格式不正确",
    "DURATION": "制作时长需要在1至600秒之间",
    "SCENE_DURATION": "镜头时长需要在0.1至600秒之间",
    "DUPLICATE_NODE": "节点标识重复",
    "MISSING_NODE": "连线引用了不存在的节点",
    "CYCLE": "节点图不能包含环",
    "BOARD_SIZE": "画板宽高需要为100至8192的整数",
    "COLOR": "颜色需要为六位十六进制格式",
    "LAYER_SIZE": "图层宽高和字号必须大于0",
    "IMAGE_REQUIRED": "图片图层需要已确认图片素材",
    "CLIP": "剪辑起点、时长或音量超出允许范围",
    "ASSET_REQUIRED": "音视频片段需要已确认素材",
    "UUID": "对象标识格式不正确",
    "ASSET_COUNT": "单文档最多引用32个唯一素材",
    "FINITE": "数值必须有限",
    "DEPTH": "文档层级超过限制",
    "COUNT": "集合超过1000项限制",
    "SIZE": "文档超过1MiB限制",
    "MEDIA_MISSING": "素材不存在或未取得可信元数据",
    "MEDIA_KIND": "片段轨道与素材类型不一致",
    "MEDIA_DURATION": "素材时长尚未验证，不能作为剪辑边界",
    "MEDIA_BOUND": "剪辑超出素材边界或引用了不可用素材。",
}
FIELDS = frozenset(
    (
        "id kind title productId parentId version updatedAt status brief audience objective "
        "sellingPoints channel requirements body scenes narration visual duration boards name "
        "width height background layers type text assetId x y fontSize color visible locked "
        "nodes edges sourceNodeId targetNodeId clips track start sourceStart volume settings "
        "ratio resolution skill prompt messages createdAt context assetIds"
    ).split()
)
COLLECTION_IDS = {
    "scenes": "sceneId",
    "boards": "boardId",
    "layers": "layerId",
    "nodes": "nodeId",
    "edges": "edgeId",
    "clips": "clipId",
    "messages": "messageId",
}


def _identifier(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return value if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,79}", value) else None


def _path(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    parts = value.split(".")
    if len(parts) > 16 or any(
        part not in FIELDS and not (part.isascii() and part.isdecimal() and len(part) < 7)
        for part in parts
    ):
        return []
    return parts


def detail(field: str, code: str, document: Any) -> dict[str, str]:
    parts = _path(field)
    result = {
        "field": "document" + ("." + ".".join(parts) if parts else ""),
        "reason": REASONS.get(code, "字段格式不正确"),
    }
    if not isinstance(document, dict):
        return result
    identifier = _identifier(document.get("id"))
    if identifier:
        result["documentId"] = identifier
    current: Any = document
    for index, part in enumerate(parts):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdecimal() and int(part) < len(current):
            current = current[int(part)]
            collection = parts[index - 1] if index else ""
            if collection in COLLECTION_IDS and isinstance(current, dict):
                identifier = _identifier(current.get("id"))
                if identifier:
                    result[COLLECTION_IDS[collection]] = identifier
        else:
            break
    return result


def schema_details(errors: Sequence[Mapping[str, Any]], document: Any) -> list[dict[str, str]]:
    result = []
    for error in errors[:20]:
        if error.get("type") == "content_rules":
            context = error.get("ctx", {})
            custom = context.get("issues") if isinstance(context, dict) else None
            for issue in custom[:20] if isinstance(custom, list) else []:
                if isinstance(issue, dict) and issue.get("code") in REASONS:
                    result.append(detail(issue.get("field", ""), issue["code"], document))
            continue
        loc = list(error.get("loc", ()))
        if loc and loc[0] in {"body", "path", "query"}:
            prefix = loc[0]
            loc = loc[1:]
            if prefix == "path" and loc == ["identifier"]:
                loc = ["id"]
        item = detail(".".join(str(p) for p in loc), "", document)
        item["reason"] = {
            "missing": "缺少必填字段",
            "extra_forbidden": "包含未支持字段",
            "literal_error": "字段选项无效",
            "float_type": "需要有限数值",
            "int_type": "需要整数",
            "string_type": "需要字符串",
            "bool_type": "需要布尔值",
            "list_type": "需要数组",
            "uuid_parsing": "对象标识格式不正确",
            "uuid_type": "对象标识格式不正确",
            "greater_than_equal": "数值超出允许范围",
            "less_than_equal": "数值超出允许范围",
        }.get(str(error.get("type")), "字段格式不正确")
        result.append(item)
    return result[:20] or [{"field": "document", "reason": "内容字段格式不正确"}]
