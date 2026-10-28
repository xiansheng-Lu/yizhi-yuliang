"""内容出站DTO与敏感模式拒绝，不序列化平台原始payload。"""

import hashlib
import json
import re
from typing import Any

from yuliang.modules.content.ai_content_models import ContentContext
from yuliang.modules.content.models import Document
from yuliang.platform.errors import ApiError, ErrorCode

PATTERNS = (
    r"(?<!\d)1[3-9]\d{9}(?!\d)",
    r"(?<!\d)\d{17}[\dXx](?!\w)",
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    r"(?i)(authorization|access[_-]?token|refresh[_-]?token|cookie|api[_-]?key|secret)\s*[:=]",
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+",
    r"\bsk-[A-Za-z0-9_-]{12,}\b",
    r"[A-Za-z0-9_-]{32,}",
    r"(收货人|收货地址|详细地址|身份证号|银行卡号|结算账户)\s*[:：=]",
)


def safe_text(value: Any, field: str = "context") -> None:
    if isinstance(value, str):
        if any(re.search(pattern, value) for pattern in PATTERNS):
            raise ApiError(
                ErrorCode.INVALID_INPUT,
                "内容含敏感信息，未发送模型",
                details=[dict(field=field, reason="请移除个人联系方式或令牌")],
            )
    elif isinstance(value, dict):
        for key, item in value.items():
            safe_text(item, field + "." + str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            safe_text(item, field + f".{index}")


def context_from(document: Document, assets: list[dict[str, Any]]) -> ContentContext:
    value = ContentContext.model_validate(
        dict(brief=document.brief.model_dump(), body=document.body, assets=assets)
    )
    safe_text(value.model_dump())
    if len(value.model_dump_json().encode()) > 7000:
        raise ApiError(ErrorCode.INVALID_INPUT, "创作上下文超过7000字节，未发送模型")
    return value


def content_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
