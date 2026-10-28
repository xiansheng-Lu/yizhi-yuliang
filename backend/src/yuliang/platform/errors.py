"""只输出受控业务错误；框架验证不回显input/ctx或原始异常。"""

from enum import StrEnum

import structlog
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NO_DATA = "NO_DATA"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    ILLEGAL_TRANSITION = "ILLEGAL_TRANSITION"
    IDEMPOTENCY_MISMATCH = "IDEMPOTENCY_MISMATCH"
    POLICY_VERSION_CHANGED = "POLICY_VERSION_CHANGED"
    TARGET_QUARANTINED = "TARGET_QUARANTINED"
    RESOURCE_TERMINAL = "RESOURCE_TERMINAL"
    RISK_BLOCKED = "RISK_BLOCKED"
    MANUAL_ONLY = "MANUAL_ONLY"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    VALIDATION_TOKEN_INVALID = "VALIDATION_TOKEN_INVALID"  # noqa: S105 -- 契约错误码，非密钥。
    DATA_COVERAGE_INSUFFICIENT = "DATA_COVERAGE_INSUFFICIENT"
    LOCKED = "LOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    PLATFORM_REJECTED = "PLATFORM_REJECTED"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    UPSTREAM_RATE_LIMITED = "UPSTREAM_RATE_LIMITED"
    RESULT_UNKNOWN = "RESULT_UNKNOWN"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"
    PROVIDER_PENDING = "PROVIDER_PENDING"
    INTERNAL_ERROR = "INTERNAL_ERROR"


STATUS = {
    ErrorCode.INVALID_INPUT: 422,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NO_DATA: 404,
    ErrorCode.VERSION_CONFLICT: 409,
    ErrorCode.ILLEGAL_TRANSITION: 409,
    ErrorCode.IDEMPOTENCY_MISMATCH: 409,
    ErrorCode.POLICY_VERSION_CHANGED: 409,
    ErrorCode.TARGET_QUARANTINED: 409,
    ErrorCode.RESOURCE_TERMINAL: 409,
    ErrorCode.RISK_BLOCKED: 422,
    ErrorCode.MANUAL_ONLY: 422,
    ErrorCode.CAPABILITY_UNAVAILABLE: 422,
    ErrorCode.VALIDATION_TOKEN_INVALID: 422,
    ErrorCode.DATA_COVERAGE_INSUFFICIENT: 422,
    ErrorCode.LOCKED: 423,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.PLATFORM_REJECTED: 502,
    ErrorCode.UPSTREAM_ERROR: 502,
    ErrorCode.UPSTREAM_RATE_LIMITED: 502,
    ErrorCode.RESULT_UNKNOWN: 504,
    ErrorCode.AI_UNAVAILABLE: 503,
    ErrorCode.PROVIDER_PENDING: 503,
    ErrorCode.INTERNAL_ERROR: 500,
}
TRANSIENT = {ErrorCode.RATE_LIMITED, ErrorCode.UPSTREAM_RATE_LIMITED, ErrorCode.AI_UNAVAILABLE}


class ApiError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: list[dict[str, str]] | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []
        self.retry_after = retry_after


def failure(request: Request, error: ApiError) -> JSONResponse:
    request_id: str = request.state.request_id
    # 受控输出不包含异常对象、请求体、Cookie、URL查询或平台原始载荷。
    structlog.get_logger().warning(
        "api_error",
        request_id=request_id,
        code=error.code.value,
        status=STATUS[error.code],
    )
    is_write = request.method not in ("GET", "HEAD", "OPTIONS")
    headers = {"X-Request-Id": request_id, "Cache-Control": "no-store"}
    if error.code in (ErrorCode.RATE_LIMITED, ErrorCode.UPSTREAM_RATE_LIMITED):
        headers["Retry-After"] = str(max(1, min(error.retry_after or 30, 3600)))
    return JSONResponse(
        {
            "requestId": request_id,
            "error": {
                "code": error.code.value,
                "message": error.message,
                "details": error.details,
                "retryable": error.code in TRANSIENT and not is_write,
            },
        },
        status_code=STATUS[error.code],
        headers=headers,
    )


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return failure(request, exc)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path.startswith("/v1/content/documents"):
        from yuliang.modules.content.validation_errors import schema_details

        return failure(
            request,
            ApiError(
                ErrorCode.INVALID_INPUT,
                "内容校验未通过",
                details=schema_details(exc.errors(), exc.body),
            ),
        )
    details = [
        {"field": ".".join(str(part) for part in item["loc"][:5]), "reason": "字段格式不正确"}
        for item in exc.errors()[:20]
    ]
    return failure(request, ApiError(ErrorCode.INVALID_INPUT, "提交的信息不正确", details=details))


async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code = {
        400: ErrorCode.INVALID_INPUT,
        401: ErrorCode.UNAUTHENTICATED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NO_DATA,
        409: ErrorCode.VERSION_CONFLICT,
        422: ErrorCode.INVALID_INPUT,
        423: ErrorCode.LOCKED,
        429: ErrorCode.RATE_LIMITED,
    }.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    return failure(request, ApiError(code, "请求未获允许或资源不可用"))


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    structlog.get_logger().error(
        "request_failed",
        request_id=request.state.request_id,
        exception_type=type(exc).__name__,
    )
    return failure(request, ApiError(ErrorCode.INTERNAL_ERROR, "服务暂时不可用，请凭请求编号排查"))
