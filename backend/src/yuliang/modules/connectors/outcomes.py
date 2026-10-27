"""HTTP响应与业务确认分开；不能凭200或5xx推断资金写结果。"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal

from yuliang.modules.connectors.http import ConnectorTransportError, HttpExchange


@dataclass(frozen=True)
class BusinessVerdict:
    decision: Literal["CONFIRMED_SUCCESS", "CONFIRMED_REJECTION", "UNCONFIRMED"] = "UNCONFIRMED"
    rejection: Literal["PARAMETER", "AUTH", "FORBIDDEN", "QUOTA", "OTHER"] = "OTHER"
    trace_id: str | None = None


@dataclass(frozen=True)
class Outcome:
    category: str
    external: Literal[
        "ACCEPTED", "REJECTED", "UNKNOWN", "NOT_SENT", "READ_SUCCEEDED", "READ_FAILED"
    ]
    retryable_read: bool = False
    retry_after_seconds: int | None = None
    retry_after_unconfirmed: bool = False


def classify(
    effect: str,
    response: HttpExchange | None = None,
    error: ConnectorTransportError | None = None,
    verdict: BusinessVerdict | None = None,
) -> Outcome:
    write = effect in {"WRITE", "TOKEN_REFRESH"}
    if error is not None:
        if error.dispatch == "NOT_SENT":
            return Outcome("PREFLIGHT_ERROR", "NOT_SENT")
        code = (
            error.code
            if error.code
            in {
                "TIMEOUT",
                "UPSTREAM_ERROR",
                "RESPONSE_TOO_LARGE",
                "SIGNING_ERROR",
                "INVALID_RESPONSE",
            }
            else "UPSTREAM_ERROR"
        )
        return Outcome(code, "UNKNOWN" if write else "READ_FAILED", not write)
    if response is None:
        return Outcome("UNCONFIRMED", "UNKNOWN" if write else "READ_FAILED")
    verdict = verdict or BusinessVerdict()
    status = response.http_status
    if status >= 500:
        return Outcome("UPSTREAM_ERROR", "UNKNOWN" if write else "READ_FAILED", not write)
    if 300 <= status < 400:
        return Outcome("REDIRECT_REFUSED", "UNKNOWN" if write else "READ_FAILED")
    retry = None
    unconfirmed = False
    header = response.headers.get("retry-after")
    try:
        try:
            value = float(header or "")
        except ValueError:
            value = max(
                0.0, (parsedate_to_datetime(header or "") - datetime.now(UTC)).total_seconds()
            )
        if math.isfinite(value) and 0 <= value <= 86400:
            retry = math.ceil(value)
        elif header is not None:
            unconfirmed = True
    except (ValueError, TypeError, OverflowError):
        unconfirmed = header is not None
    if status == 429:
        external: Literal["REJECTED", "UNKNOWN"] = (
            "REJECTED" if verdict.decision == "CONFIRMED_REJECTION" else "UNKNOWN"
        )
        return Outcome(
            "RATE_LIMITED", external if write else "READ_FAILED", not write, retry, unconfirmed
        )
    if verdict.decision == "CONFIRMED_REJECTION":
        category = {
            "PARAMETER": "PARAMETER_REJECTED",
            "AUTH": "AUTH_REQUIRED",
            "FORBIDDEN": "FORBIDDEN",
            "QUOTA": "RATE_LIMITED",
            "OTHER": "PLATFORM_REJECTED",
        }.get(verdict.rejection, "PLATFORM_REJECTED")
        return Outcome(category, "REJECTED" if write else "READ_FAILED", False, retry)
    if 200 <= status < 300 and verdict.decision == "CONFIRMED_SUCCESS":
        return Outcome(
            "ACCEPTED" if write else "READ_SUCCEEDED", "ACCEPTED" if write else "READ_SUCCEEDED"
        )
    category = (
        "AUTH_REQUIRED" if status == 401 else "FORBIDDEN" if status == 403 else "INVALID_RESPONSE"
    )
    return Outcome(category, "UNKNOWN" if write else "READ_FAILED")
