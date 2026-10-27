"""U层仅合成端口与HTTP故障输入，不调用平台，不证明账户/动作真实通过。"""

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from yuliang.modules.connectors.base import (
    AssertionResult,
    AuthorizationRef,
    ChangePreview,
    GatewayContext,
    ObjectRef,
    PlatformChange,
    PlatformCommand,
    PlatformReceipt,
    PlatformResourcePage,
    ReadbackReference,
    ReadbackValue,
    ResourceQuery,
    TokenSet,
)
from yuliang.modules.connectors.http import (
    ConnectorTransportError,
    OperationSpec,
    PlatformHttpClient,
)
from yuliang.modules.connectors.registry import GatewayRegistry
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.errors import ApiError


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    object_id: str | None = None
    value: int = Field(strict=True, ge=0)


def context() -> GatewayContext:
    return GatewayContext(
        VerifiedIdentity(1, uuid4(), uuid4()),
        uuid4(),
        uuid4(),
        "123",
        "adq",
        "tencent",
        "adq",
        uuid4(),
        uuid4(),
        uuid4(),
        1,
        "a" * 20,
        "b" * 64,
    )


def operation(ctx: GatewayContext, **changes: Any) -> OperationSpec:
    values: dict[str, Any] = dict(
        key="adgroups.get",
        catalog_id=ctx.catalog_id,
        catalog_sha256=ctx.catalog_sha256,
        connector_id=ctx.connector_id,
        provider=ctx.provider,
        product_line=ctx.product_line,
        host="api.e.qq.com",
        method="GET",
        path="/v3.0/adgroups/get",
        effect="READ",
        parameters_model=Parameters,
        account_parameter="account_id",
    )
    return OperationSpec(**(values | changes))


def target(ctx: GatewayContext) -> ObjectRef:
    return ObjectRef(
        tenant_id=ctx.identity.tenant_id,
        account_id=ctx.account_id,
        connector_id=ctx.connector_id,
        provider=ctx.provider,
        product_line=ctx.product_line,
        native_id="456",
    )


def signer(request: httpx.Request, credential: SecretStr) -> httpx.Request:
    return httpx.Request(
        request.method,
        request.url.copy_merge_params({"access_token": credential.get_secret_value()}),
        headers=request.headers,
        content=request.content,
    )


async def no_wait(seconds: float) -> None:
    assert 0 <= seconds <= 5


class ExamplePort:
    async def read(self, ctx: GatewayContext, query: ResourceQuery) -> PlatformResourcePage:
        if query.target:
            ctx.assert_ref(query.target)
        return PlatformResourcePage(
            records=(), next_cursor=None, source_at=None, fetched_at=datetime.now(UTC)
        )

    async def preview(self, ctx: GatewayContext, change: PlatformChange) -> ChangePreview:
        return ChangePreview(
            normalized_changes=(),
            warnings=(),
            requires_approval=True,
            context_digest=change.frozen_digest,
        )

    async def submit(self, ctx: GatewayContext, command: PlatformCommand) -> PlatformReceipt:
        return PlatformReceipt(
            disposition="UNKNOWN",
            execution_id=command.execution_id,
            attempt_id=command.attempt_id,
            external_id=None,
            trace_id=None,
        )

    async def readback(self, ctx: GatewayContext, ref: ReadbackReference) -> ReadbackValue:
        ctx.assert_ref(ref.target)
        return ReadbackValue(
            verification="UNKNOWN", observed=None, source_at=None, fetched_at=datetime.now(UTC)
        )

    async def refresh_token(self, auth: AuthorizationRef) -> TokenSet:
        return TokenSet(
            access_token=SecretStr("synthetic-access"),
            refresh_token=SecretStr("synthetic-refresh"),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=(),
        )

    async def capability_probe(self, ctx: GatewayContext) -> AssertionResult:
        return AssertionResult(
            binding_id=ctx.binding_id,
            capability_version=ctx.capability_version,
            assertions=(),
            unavailable_reason="SYNTHETIC_NOT_REAL_PLATFORM",
        )


async def test_six_port_contracts_and_registry_product_separation() -> None:
    ctx = context()
    registry = GatewayRegistry()
    with pytest.raises(ApiError):
        registry.resolve(ctx)
    gateway = ExamplePort()
    registry.register("tencent", "adq", gateway)
    assert registry.resolve(ctx) is gateway
    with pytest.raises(ValueError):
        registry.register("tencent", "adq", gateway)
    for provider, product in [("ocean", "qianchuan"), ("xhs", "juguang"), ("beans", "beans")]:
        with pytest.raises(ValueError):
            registry.register(provider, product, gateway)
    promotion = replace(ctx, connector_id="wxpromo", product_line="shop")
    assert promotion.adapter_product == "wxpromo"
    with pytest.raises(ApiError):
        registry.resolve(promotion)
    query = ResourceQuery(
        operation="adgroups.get", catalog_id=ctx.catalog_id, parameters={}, target=target(ctx)
    )
    assert (await gateway.read(ctx, query)).records == ()
    change = PlatformChange(**query.model_dump(), frozen_digest="c" * 64)
    assert (await gateway.preview(ctx, change)).requires_approval
    command = PlatformCommand(
        **change.model_dump(), execution_id=uuid4(), attempt_id=uuid4(), idempotency_key=uuid4()
    )
    assert (await gateway.submit(ctx, command)).disposition == "UNKNOWN"
    ref = ReadbackReference(
        execution_id=command.execution_id,
        attempt_id=command.attempt_id,
        target=target(ctx),
        expected_digest=change.frozen_digest,
    )
    assert (await gateway.readback(ctx, ref)).verification == "UNKNOWN"
    auth = AuthorizationRef(
        **target(ctx).model_dump(), authorization_id=ctx.authorization_id, authorization_version=1
    )
    token = await gateway.refresh_token(auth)
    assert "synthetic-access" not in repr(token)
    assert (await gateway.capability_probe(ctx)).assertions == ()


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "localhost",
        "api.e.qq.com.evil.invalid",
        "api.e.qq.com:444",
        "api.e.qq.com@evil.invalid",
        "api.e.qq.com#hidden",
        "api.weixin.qq.com",
    ],
)
def test_unapproved_host_and_provider_mismatch_rejected(host: str) -> None:
    with pytest.raises(ValueError):
        operation(context(), host=host)


@pytest.mark.parametrize(
    "path",
    [
        "https://api.e.qq.com/v3.0/x",
        "//evil.invalid/x",
        "/v3.0/../x",
        "/v3.0/%2e%2e/x",
        "/v3.0/x?token=x",
        "/v3.0/x#anchor",
        "/v3.0/{client_id}",
        "/v3.0/x\\y",
    ],
)
def test_path_is_fixed_and_cannot_be_tunneled(path: str) -> None:
    with pytest.raises(ValueError):
        operation(context(), path=path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("url", "https://evil.invalid"),
        ("access_token", "forged"),
        ("headers", {"Authorization": "forged"}),
        ("unexpected", 1),
        ("value", True),
        ("value", "10"),
        ("account_id", "other"),
    ],
)
async def test_untrusted_fields_types_and_account_never_reach_transport(
    field: str, value: Any
) -> None:
    ctx = context()
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    client = PlatformHttpClient(signer, transport=httpx.MockTransport(handle))
    with pytest.raises(ValueError):
        await client.send(
            ctx,
            operation(ctx),
            {"account_id": "123", "value": 10} | {field: value},
            SecretStr("test"),
        )
    assert not calls


async def test_target_catalog_and_product_must_match_before_http() -> None:
    ctx = context()

    async def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not send")

    client = PlatformHttpClient(signer, transport=httpx.MockTransport(never))
    spec = operation(
        ctx,
        method="POST",
        path="/v3.0/adgroups/update",
        effect="WRITE",
        target_parameter="object_id",
    )
    values = {"account_id": "123", "object_id": "456", "value": 10}
    for foreign in [
        target(ctx).model_copy(update={"tenant_id": 2}),
        target(ctx).model_copy(update={"account_id": uuid4()}),
        target(ctx).model_copy(update={"connector_id": "wxpromo", "product_line": "shop"}),
    ]:
        with pytest.raises(ValueError):
            await client.send(ctx, spec, values, SecretStr("test"), target=foreign)
    with pytest.raises(ValueError):
        await client.send(
            ctx, spec, values | {"object_id": "other"}, SecretStr("test"), target=target(ctx)
        )
    with pytest.raises(ValueError):
        await client.send(
            ctx,
            replace(spec, catalog_sha256="c" * 64),
            values,
            SecretStr("test"),
            target=target(ctx),
        )
    with pytest.raises(ValueError):
        await client.send(ctx, spec, values, SecretStr("test"))


@pytest.mark.parametrize("effect", ["WRITE", "TOKEN_REFRESH"])
def test_writes_and_rotation_cannot_be_configured_to_retry(effect: str) -> None:
    with pytest.raises(ValueError):
        operation(
            context(),
            method="POST",
            effect=effect,
            max_read_attempts=2,
            idempotent_read=True,
            target_parameter="object_id",
        )


async def test_idempotent_read_retries_bounded_and_write_500_does_not() -> None:
    ctx = context()
    calls = []
    sleeps = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500 if len(calls) < 3 else 200, json={"result": "synthetic"})

    async def wait(seconds: float) -> None:
        sleeps.append(seconds)

    client = PlatformHttpClient(signer, transport=httpx.MockTransport(handle), sleep=wait)
    result = await client.send(
        ctx,
        operation(ctx, max_read_attempts=3, idempotent_read=True),
        {"account_id": "123", "value": 10},
        SecretStr("test"),
    )
    assert result.http_status == 200 and result.attempts == 3 and len(sleeps) == 2
    calls.clear()
    write = operation(
        ctx,
        method="POST",
        effect="WRITE",
        path="/v3.0/adgroups/update",
        target_parameter="object_id",
    )
    result = await client.send(
        ctx,
        write,
        {"account_id": "123", "object_id": "456", "value": 10},
        SecretStr("test"),
        target=target(ctx),
    )
    assert result.http_status == 500 and result.attempts == 1 and len(calls) == 1


@pytest.mark.parametrize("effect", ["WRITE", "TOKEN_REFRESH"])
async def test_uncertain_delivery_never_retries_or_reports_write_failure(effect: str) -> None:
    ctx = context()
    calls = []

    def timeout(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout(str(request.url), request=request)

    client = PlatformHttpClient(signer, transport=httpx.MockTransport(timeout))
    spec = operation(ctx, method="POST", effect=effect, target_parameter="object_id")
    with pytest.raises(ConnectorTransportError) as failed:
        await client.send(
            ctx,
            spec,
            {"account_id": "123", "object_id": "456", "value": 10},
            SecretStr("private-test"),
            target=target(ctx),
        )
    assert failed.value.dispatch == "UNKNOWN" and failed.value.attempts == 1 and len(calls) == 1
    assert "private-test" not in str(failed.value)


async def test_retry_after_long_delay_is_delegated_not_retried_early() -> None:
    ctx = context()
    calls = []

    def limited(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"}, json={})

    result = await PlatformHttpClient(
        signer, transport=httpx.MockTransport(limited), sleep=no_wait
    ).send(
        ctx,
        operation(ctx, max_read_attempts=3, idempotent_read=True),
        {"account_id": "123", "value": 1},
        SecretStr("test"),
    )
    assert (
        result.http_status == 429
        and result.attempts == 1
        and result.headers == {"retry-after": "60"}
    )
    assert len(calls) == 1


async def test_redirect_and_oversized_response_are_not_followed_or_retried() -> None:
    ctx = context()
    calls = []

    def redirect(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            302, headers={"location": "https://evil.invalid/capture"}, text="redirect"
        )

    client = PlatformHttpClient(signer, transport=httpx.MockTransport(redirect))
    result = await client.send(
        ctx, operation(ctx), {"account_id": "123", "value": 1}, SecretStr("test")
    )
    assert result.http_status == 302 and len(calls) == 1

    def large(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100)

    client = PlatformHttpClient(signer, transport=httpx.MockTransport(large))
    with pytest.raises(ConnectorTransportError) as failed:
        await client.send(
            ctx,
            operation(ctx, max_response_bytes=50),
            {"account_id": "123", "value": 1},
            SecretStr("test"),
        )
    assert failed.value.code == "RESPONSE_TOO_LARGE" and failed.value.dispatch == "RESPONDED"


async def test_proxy_environment_ignored_and_library_logs_hide_signed_url(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ctx = context()
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HTTPS_PROXY", "http://synthetic-llm-proxy.invalid:9999")
    monkeypatch.setenv("ALL_PROXY", "http://synthetic-llm-proxy.invalid:9999")
    original = httpx.AsyncClient
    options = []

    def client_spy(**kwargs: Any) -> httpx.AsyncClient:
        options.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_spy)

    def handle(request: httpx.Request) -> httpx.Response:
        logging.getLogger("httpcore.http11").debug("unsafe detail %s", str(request.url))
        return httpx.Response(200, json={"synthetic_secret": "private-test-token"})

    result = await PlatformHttpClient(signer, transport=httpx.MockTransport(handle)).send(
        ctx, operation(ctx), {"account_id": "123", "value": 1}, SecretStr("private-test-token")
    )
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    assert "private-test-token" not in caplog.text and "private-test-token" not in repr(result)
    logging.getLogger("httpx").info("outside-platform-context")
    assert "outside-platform-context" in caplog.text


async def test_signer_cannot_redirect_change_payload_or_expose_error() -> None:
    ctx = context()

    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError("签名拒绝场景不得发送")

    for mutation in ("host", "body", "exception", "query", "extra_query"):

        def bad(
            request: httpx.Request, credential: SecretStr, mode: str = mutation
        ) -> httpx.Request:
            if mode == "exception":
                raise ValueError(credential.get_secret_value())
            if mode in {"query", "extra_query"}:
                return httpx.Request(
                    request.method,
                    request.url.copy_merge_params(
                        {"account_id": "other"} if mode == "query" else {"destination": "unknown"}
                    ),
                    headers=request.headers,
                    content=request.content,
                )
            return httpx.Request(
                request.method,
                "https://evil.invalid/v3.0/adgroups/get" if mode == "host" else request.url,
                content=b"changed" if mode == "body" else request.content,
            )

        with pytest.raises((ValueError, ConnectorTransportError)) as rejected:
            await PlatformHttpClient(bad, transport=httpx.MockTransport(never)).send(
                ctx,
                operation(ctx),
                {"account_id": "123", "value": 1},
                SecretStr("private-test-token"),
            )
        assert "private-test-token" not in str(rejected.value)


async def test_overall_timeout_and_cancellation_have_no_automatic_write_retry() -> None:
    ctx = context()
    calls = []
    started = asyncio.Event()

    async def delayed(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        started.set()
        await asyncio.Event().wait()
        return httpx.Response(200)

    client = PlatformHttpClient(signer, transport=httpx.MockTransport(delayed), total_timeout=0.01)
    with pytest.raises(ConnectorTransportError) as failed:
        await client.send(ctx, operation(ctx), {"account_id": "123", "value": 1}, SecretStr("test"))
    assert failed.value.code == "TIMEOUT" and len(calls) == 1
    calls.clear()
    started.clear()
    write = operation(
        ctx,
        method="POST",
        effect="WRITE",
        path="/v3.0/adgroups/update",
        target_parameter="object_id",
    )
    task = asyncio.create_task(
        PlatformHttpClient(signer, transport=httpx.MockTransport(delayed)).send(
            ctx,
            write,
            {"account_id": "123", "object_id": "456", "value": 1},
            SecretStr("test"),
            target=target(ctx),
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 1


def test_extra_field_schema_or_naive_token_expiry_rejected() -> None:
    class Loose(BaseModel):
        value: int

    with pytest.raises(ValueError):
        operation(context(), parameters_model=Loose)
    with pytest.raises(ValidationError):
        TokenSet(
            access_token=SecretStr("test"),
            refresh_token=None,
            expires_at=datetime(2026, 10, 27),
            scopes=(),
        )
