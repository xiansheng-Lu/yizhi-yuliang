"""白名单业务字段投影；未知字段、凭据和订单PII默认不进入存档。"""

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

_PRIVATE = re.compile(
    r"token|secret|password|authorization|oauth|credential|session|csrf|cookie|phone|mobile|email|address|receiver|recipient|buyer|customer|openid|unionid|bank|card|identity|contact|name|signature|(^|_)auth(_|$)",
    re.I,
)


@dataclass(frozen=True)
class ArchivePolicy:
    fields: frozenset[str] = frozenset(
        {
            "account_id",
            "advertiser_id",
            "object_id",
            "id",
            "data",
            "list",
            "rows",
            "code",
            "errcode",
            "status",
            "amount_minor",
            "budget_minor",
            "currency",
            "impressions",
            "clicks",
            "count",
        }
    )
    max_items: int = 1000

    def __post_init__(self) -> None:
        if type(self.max_items) is not int or not 1 <= self.max_items <= 1000:
            raise ValueError("存档条目上限不合法")


def fingerprint(key: SecretStr, purpose: str, value: bytes) -> str:
    secret = key.get_secret_value()
    if len(secret) < 32:
        raise ValueError("需要独立服务端审计密钥")
    return hmac.new(secret.encode(), purpose.encode() + b"\0" + value, hashlib.sha256).hexdigest()


def canonical(value: Any) -> bytes:
    try:
        result = json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError("载荷必须是有界JSON") from None
    if len(result) > 4 * 1024 * 1024:
        raise ValueError("载荷超过归档上限")
    return result


def project(value: Any, policy: ArchivePolicy) -> dict[str, Any]:
    omitted = 0
    truncated = False

    def visit(item: Any, depth: int = 0) -> Any:
        nonlocal omitted, truncated
        if depth > 12:
            truncated = True
            return None
        if isinstance(item, dict):
            safe = {}
            for name, child in item.items():
                if (
                    not isinstance(name, str)
                    or name not in policy.fields
                    or _PRIVATE.search(name)
                    or name.lower() in {"name", "message", "msg", "description", "remark"}
                ):
                    omitted += 1
                    continue
                if name.lower() in {"code", "errcode"} and type(child) is not int:
                    omitted += 1
                    continue
                safe[name] = visit(child, depth + 1)
            return safe
        if isinstance(item, list):
            if len(item) > policy.max_items:
                truncated = True
            return [visit(child, depth + 1) for child in item[: policy.max_items]]
        if isinstance(item, str):
            if len(item) > 160 or not re.fullmatch(r"[A-Za-z0-9_.:/+ -]*", item):
                omitted += 1
                return None
            return item
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float) and math.isfinite(item):
            return item
        omitted += 1
        return None

    safe = visit(value)
    return {
        "data": safe if isinstance(safe, (dict, list)) else None,
        "omittedFields": omitted,
        "truncated": truncated,
    }
