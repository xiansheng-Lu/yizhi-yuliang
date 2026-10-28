"""平台出站边界；只有显式幂等读取可重试，写/令牌轮换从不自动重试。"""

import asyncio
import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal, Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from yuliang.modules.capability.models import CONNECTOR_CONTEXTS
from yuliang.modules.connectors.base import AuthorizationGatewayContext, GatewayContext, ObjectRef
from yuliang.platform.security.egress import private_http_logs, validate_destination

_FORBIDDEN_FIELDS = frozenset(
    {
        "authorization",
        "cookie",
        "headers",
        "url",
        "endpoint",
        "host",
        "method",
        "path",
        "http_method",
        "access_token",
        "refresh_token",
        "client_secret",
        "app_secret",
        "secret",
        "password",
        "token",
        "signature",
        "tenant_id",
        "tenantid",
        "user_id",
        "userid",
        "membership_id",
        "provider",
        "product_line",
        "productline",
        "connector_id",
        "connectorid",
        "authorization_id",
        "capability_version",
    }
)
_AUTH_QUERY_FIELDS = frozenset(
    {
        "access_token",
        "refresh_token",
        "client_id",
        "client_secret",
        "appid",
        "app_secret",
        "secret",
        "signature",
        "sign",
        "timestamp",
        "nonce",
        "grant_type",
        "code",
    }
)


@dataclass(frozen=True)
class OperationSpec:
    key: str
    catalog_id: str
    catalog_sha256: str
    connector_id: str
    provider: str
    product_line: str
    host: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    path: str
    effect: Literal["READ", "WRITE", "TOKEN_REFRESH"]
    parameters_model: type[BaseModel]
    account_parameter: str | None = None
    target_parameter: str | None = None
    creates_object: bool = False
    idempotent_read: bool = False
    max_read_attempts: int = 1
    max_response_bytes: int = 1024 * 1024
    cursor_parameter: str | None = None
    initial_cursor: str | None = None
    preflight_resources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.initial_cursor is not None and (
            self.effect != "READ"
            or not self.idempotent_read
            or self.cursor_parameter is None
            or self.initial_cursor != ""
            and not re.fullmatch(r"[1-9][0-9]{0,5}", self.initial_cursor)
        ):
            raise ValueError("分页首值须由固定只读Schema明确声明（数字或空串）")
        if (
            type(self.preflight_resources) is not tuple
            or len(self.preflight_resources) > 16
            or len(set(self.preflight_resources)) != len(self.preflight_resources)
            or any(
                not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_.]{0,59}", key)
                for key in self.preflight_resources
            )
            or self.preflight_resources
            and self.effect != "WRITE"
        ):
            raise ValueError("写前置资源必须由服务器明确声明")
        if (
            not re.fullmatch(r"[a-z][a-z0-9_.]{0,79}", self.key)
            or not re.fullmatch(r"[0-9a-f]{20}", self.catalog_id)
            or not re.fullmatch(r"[0-9a-f]{64}", self.catalog_sha256)
            or CONNECTOR_CONTEXTS.get(self.connector_id) != (self.provider, self.product_line)
        ):
            raise ValueError("操作描述或目录上下文不合法")
        if (
            not re.fullmatch(r"/[A-Za-z0-9_./-]+", self.path)
            or "//" in self.path
            or any(part in {".", ".."} for part in self.path.split("/"))
        ):
            raise ValueError("操作路径必须是代码固定的规范化路径")
        validate_destination(httpx.URL(f"https://{self.host}{self.path}"), self.provider, self.path)
        if self.method not in {
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "HEAD",
            "OPTIONS",
        } or self.effect not in {"READ", "WRITE", "TOKEN_REFRESH"}:
            raise ValueError("操作方法或副作用类型不支持")
        if self.effect == "READ" and self.method not in {"GET", "HEAD", "OPTIONS", "POST"}:
            raise ValueError("读操作不能使用修改方法")
        if self.effect == "WRITE" and self.method in {"GET", "HEAD", "OPTIONS"}:
            raise ValueError("写操作不能使用只读方法")
        if type(self.max_read_attempts) is not int or not 1 <= self.max_read_attempts <= 3:
            raise ValueError("最多三次受控读取")
        if self.max_read_attempts > 1 and not (
            self.effect == "READ" and self.idempotent_read is True
        ):
            raise ValueError("写、令牌轮换或未声明幂等的读取不得自动重试")
        if (
            type(self.max_response_bytes) is not int
            or not 1 <= self.max_response_bytes <= 4 * 1024 * 1024
        ):
            raise ValueError("响应大小界限不合法")
        if self.parameters_model.model_config.get("extra") != "forbid":
            raise ValueError("操作参数必须使用拒绝额外字段的Schema")
        properties = self.parameters_model.model_json_schema().get("properties", {})
        for field_name in (self.account_parameter, self.target_parameter, self.cursor_parameter):
            if field_name is not None and field_name not in properties:
                raise ValueError("上下文字段必须由操作Schema明确声明")
        if self.effect == "WRITE" and (
            self.account_parameter is None
            or self.target_parameter is None
            and self.creates_object is not True
        ):
            raise ValueError("写操作必须绑定账户以及目标，或明确为新建对象")


@dataclass(frozen=True)
class HttpExchange:
    request_id: UUID
    operation_key: str
    http_status: int
    attempts: int
    received_at: datetime
    headers: dict[str, str]
    body: bytes = field(repr=False)
    dispatch: Literal["RESPONDED"] = "RESPONDED"


class ConnectorTransportError(Exception):
    def __init__(
        self,
        code: str,
        request_id: UUID,
        attempts: int,
        *,
        dispatch: Literal["NOT_SENT", "UNKNOWN", "RESPONDED"] = "UNKNOWN",
        http_status: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code, self.request_id, self.attempts = code, request_id, attempts
        self.dispatch, self.http_status = dispatch, http_status


class RequestSigner(Protocol):
    def __call__(self, request: httpx.Request, credential: SecretStr) -> httpx.Request: ...


class ExchangeObserver(Protocol):
    def started(self, attempt: int) -> None: ...
    def responded(self, response: HttpExchange) -> None: ...
    def failed(self, error: ConnectorTransportError) -> None: ...


def _check_fields(value: Any, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("平台参数嵌套过深")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key.lower().replace("-", "_") in _FORBIDDEN_FIELDS:
                raise ValueError("平台参数不能透传认证、路由或请求头字段")
            _check_fields(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_fields(item, depth + 1)


def _query(value: Any) -> str:
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return str(value)


def _delay(header: str | None, attempt: int) -> float | None:
    fallback = min(math.ldexp(0.25, attempt - 1), 2.0)
    if header is None:
        return fallback
    try:
        seconds = float(header)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return fallback
    if not math.isfinite(seconds) or seconds < 0:
        return fallback
    # 较长等待留给M1-04调度，不提前重试或在请求内忙循环。
    return seconds if seconds <= 5 else None


class PlatformHttpClient:
    def __init__(
        self,
        signer: RequestSigner,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        total_timeout: float = 30,
    ) -> None:
        if not 0 < total_timeout <= 90:
            raise ValueError("需要有界平台网络预算")
        self.signer, self.transport, self.sleep, self.total_timeout = (
            signer,
            transport,
            sleep,
            total_timeout,
        )

    async def send(
        self,
        ctx: GatewayContext | AuthorizationGatewayContext,
        spec: OperationSpec,
        parameters: dict[str, Any],
        credential: SecretStr,
        *,
        target: ObjectRef | None = None,
        observer: ExchangeObserver | None = None,
    ) -> HttpExchange:
        if isinstance(ctx, AuthorizationGatewayContext) and (
            spec.effect != "TOKEN_REFRESH"
            or spec.account_parameter is not None
            or spec.target_parameter is not None
            or target is not None
        ):
            raise ValueError("账户发现前的授权上下文只允许无账户令牌操作")
        if (
            spec.connector_id,
            spec.provider,
            spec.product_line,
            spec.catalog_id,
            spec.catalog_sha256,
        ) != (ctx.connector_id, ctx.provider, ctx.product_line, ctx.catalog_id, ctx.catalog_sha256):
            raise ValueError("操作与已核对账户、产品线或目录版本不一致")
        if target is not None:
            ctx.assert_ref(target)
        _check_fields(parameters)
        try:
            payload = spec.parameters_model.model_validate(parameters, strict=True).model_dump(
                mode="json", by_alias=True
            )
            _check_fields(payload)
            encoded = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode()
        except (ValidationError, ValueError, TypeError):
            raise ValueError("平台参数不符合该操作Schema") from None
        if len(encoded) > 256 * 1024:
            raise ValueError("平台请求超出大小界限")
        if (
            spec.account_parameter is not None
            and isinstance(ctx, GatewayContext)
            and str(payload.get(spec.account_parameter)) != ctx.native_account_id
        ):
            raise ValueError("参数账户与服务端账户不一致")
        if spec.target_parameter is not None and (
            target is None or str(payload.get(spec.target_parameter)) != target.native_id
        ):
            raise ValueError("参数目标与服务端已核对目标不一致")
        kwargs: dict[str, Any] = {
            "headers": {"Accept": "application/json", "X-Request-ID": str(ctx.request_id)}
        }
        if spec.method in {"GET", "HEAD", "OPTIONS"}:
            kwargs["params"] = {
                key: _query(value) for key, value in payload.items() if value is not None
            }
        else:
            kwargs["content"] = encoded
            kwargs["headers"]["Content-Type"] = "application/json"
        request = httpx.Request(spec.method, f"https://{spec.host}{spec.path}", **kwargs)
        original = request.content
        original_query = request.url.params
        try:
            request = self.signer(request, credential)
        except Exception:
            raise ConnectorTransportError(
                "SIGNING_ERROR", ctx.request_id, 0, dispatch="NOT_SENT"
            ) from None
        validate_destination(request.url, spec.provider, spec.path)
        if any(
            request.url.params.get_list(key) != original_query.get_list(key)
            for key in original_query
        ) or any(
            key not in original_query and key not in _AUTH_QUERY_FIELDS
            for key in request.url.params
        ):
            raise ValueError("签名器不能改写业务查询或添加未声明的路由字段")
        if (
            request.method != spec.method
            or request.content != original
            or "host" in request.headers
            and request.headers["host"] not in {spec.host, spec.host + ":443"}
        ):
            raise ValueError("签名器不能改写业务方法、载荷或出口Host")
        attempts = 0
        active = False
        try:
            with private_http_logs():
                async with asyncio.timeout(self.total_timeout):
                    async with httpx.AsyncClient(
                        trust_env=False,
                        follow_redirects=False,
                        transport=self.transport,
                        timeout=httpx.Timeout(30, connect=5, read=30, write=30, pool=5),
                    ) as client:
                        for attempts in range(1, spec.max_read_attempts + 1):
                            if observer is not None:
                                observer.started(attempts)
                            active = True
                            try:
                                async with client.stream(
                                    request.method,
                                    request.url,
                                    headers=request.headers,
                                    content=request.content,
                                ) as response:
                                    body = bytearray()
                                    async for chunk in response.aiter_bytes():
                                        if len(body) + len(chunk) > spec.max_response_bytes:
                                            raise ConnectorTransportError(
                                                "RESPONSE_TOO_LARGE",
                                                ctx.request_id,
                                                attempts,
                                                dispatch="RESPONDED",
                                                http_status=response.status_code,
                                            )
                                        body.extend(chunk)
                                    headers = {
                                        key: response.headers[key]
                                        for key in ("retry-after",)
                                        if key in response.headers
                                    }
                                    exchange = HttpExchange(
                                        ctx.request_id,
                                        spec.key,
                                        response.status_code,
                                        attempts,
                                        datetime.now(UTC),
                                        headers,
                                        bytes(body),
                                    )
                                if observer is not None:
                                    observer.responded(exchange)
                                active = False
                                if (
                                    response.status_code not in {429, 500, 502, 503, 504}
                                    or attempts >= spec.max_read_attempts
                                ):
                                    return exchange
                                delay = _delay(headers.get("retry-after"), attempts)
                                if delay is None:
                                    return exchange
                                await self.sleep(delay)
                            except (httpx.TimeoutException, httpx.HTTPError) as error:
                                code = (
                                    "TIMEOUT"
                                    if isinstance(error, httpx.TimeoutException)
                                    else "UPSTREAM_ERROR"
                                )
                                failure = ConnectorTransportError(code, ctx.request_id, attempts)
                                if observer is not None:
                                    observer.failed(failure)
                                active = False
                                if attempts >= spec.max_read_attempts:
                                    raise failure from None
                                await self.sleep(_delay(None, attempts) or 0.25)
        except TimeoutError:
            failure = ConnectorTransportError("TIMEOUT", ctx.request_id, attempts)
            if active and observer is not None:
                observer.failed(failure)
            raise failure from None
        raise ConnectorTransportError("UPSTREAM_ERROR", ctx.request_id, attempts)
