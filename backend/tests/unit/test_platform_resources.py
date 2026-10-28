import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from yuliang.modules.connectors.base import GatewayContext, ResourceQuery
from yuliang.modules.connectors.http import HttpExchange
from yuliang.modules.connectors.native_auth import NativeAuthorization
from yuliang.modules.connectors.native_reads import (
    ReadOnlyNativeAdapter,
    decode_verdict,
    native_signer,
    parse_page,
    read_spec,
)
from yuliang.modules.connectors.oauth import (
    CodeExchangeFailure,
    OAuthApplication,
    OAuthExchangeContext,
)
from yuliang.modules.connectors.resources import (
    ApplicationResource,
    ResourceBundle,
    checklist_projection,
    collect_resources,
    private_write,
    resource_summary,
)
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.errors import ApiError
from yuliang.platform.security.oauth_callback import OAuthCallbackPrivacy


def resource(tmp_path: Path, connector: str = "adq", **changes: Any) -> ApplicationResource:
    source = tmp_path / ".env"
    source.write_text("APP_SECRET=synthetic-secret\n", encoding="utf-8")
    application_id = uuid4()
    data = {
        "id": application_id,
        "connector_id": connector,
        "app_id": "synthetic-app",
        "mode": "ADQ_OAUTH" if connector == "adq" else "WXSHOP_SELF",
        "secret": {"file": source, "key": "APP_SECRET"},
        "source_root": tmp_path,
        "tenant_id": 1,
        "shop_id": uuid4(),
        "native_id": "12345",
        "enabled": True,
        "granted_scopes": ("read",),
        "permissions_evidence": "test-evidence",
        "token_owner_transferred": True,
        "redirect_uri": f"https://callback.example.invalid/v1/connections/oauth/{application_id}/callback"
        if connector == "adq"
        else None,
    }
    return ApplicationResource.model_validate(data | changes)


def context(port: NativeAuthorization) -> OAuthExchangeContext:
    return OAuthExchangeContext(
        VerifiedIdentity(1, uuid4(), uuid4()),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        port.application(),
    )


def test_collect_is_offline_and_does_not_copy_tokens_or_infer_permission(tmp_path: Path) -> None:
    adq, wx = tmp_path / "adq", tmp_path / "wx"
    (adq / "data").mkdir(parents=True)
    wx.mkdir()
    (adq / ".env").write_text("ADQ_CLIENT_ID=app-adq\nADQ_CLIENT_SECRET=private-app-secret\n")
    (wx / ".env").write_text("WX_APPID=wxapp\nWX_SECRET=private-wx-secret\n")
    (adq / "data/adq_credentials.json").write_text(
        json.dumps({"accounts": {"12345": {"refresh_token": "private-refresh", "scope_list": []}}})
    )
    bundle = collect_resources(adq, wx)
    rendered = bundle.model_dump_json()
    assert "private-refresh" not in rendered
    assert "private-app-secret" not in rendered
    assert bundle.applications[0].observed_native_ids == ("12345",)
    assert bundle.applications[0].granted_scopes is None
    assert not any(app.enabled for app in bundle.applications)
    assert resource_summary(bundle)["containsSecrets"] is False
    template = Path(__file__).resolve().parents[3] / (
        "docs/backend/evidence/resource-handoff/platform-inputs.template.json"
    )
    checklist = checklist_projection(bundle, json.loads(template.read_text(encoding="utf-8")))
    assert checklist["adq"]["permissions"]["actuallyGrantedScopeCodes"] is None
    assert checklist["adq"]["readonlyValidationApproval"]["confirmed"] is False
    assert checklist["adq"]["application"]["actualAuthorizationMode"] is None
    assert "private-app-secret" not in json.dumps(checklist)


def test_private_output_does_not_overwrite_manual_mapping(tmp_path: Path) -> None:
    path = tmp_path / "resources.json"
    private_write(path, {"mapping": "manually-verified"})
    with pytest.raises(FileExistsError):
        private_write(path, {"mapping": None})
    assert json.loads(path.read_text())["mapping"] == "manually-verified"


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": None},
        {"shop_id": None},
        {"permissions_evidence": None},
        {"granted_scopes": None},
        {"token_owner_transferred": False},
    ],
)
def test_enable_requires_completed_handoff(tmp_path: Path, changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        resource(tmp_path, **changes)


def test_bundle_refuses_duplicate_applications(tmp_path: Path) -> None:
    value = resource(tmp_path)
    with pytest.raises(ValidationError):
        ResourceBundle(applications=(value, value))


def test_self_credential_has_no_fake_callback(tmp_path: Path) -> None:
    app = NativeAuthorization(resource(tmp_path, "wxshop")).application()
    assert app.redirect_uri == ""
    assert app.authorization_mode == "SELF_CREDENTIAL"
    with pytest.raises(ValueError):
        replace(app, connector_id="adq", provider="tencent", product_line="adq")
    with pytest.raises(ValueError):
        OAuthApplication(uuid4(), "app", "adq", "tencent", "adq", "http://callback.invalid")


@pytest.mark.asyncio
async def test_adq_exchange_uses_fixed_endpoint_and_verifies_native_account(tmp_path: Path) -> None:
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.host == "api.e.qq.com" and request.url.path == "/oauth/token"
        assert request.url.params["grant_type"] == "authorization_code"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "access_token": "synthetic-access",
                    "refresh_token": "synthetic-refresh",
                    "access_token_expires_in": 60,
                    "authorizer_info": {"account_id": "12345", "scope_list": ["read"]},
                },
            },
        )

    port = NativeAuthorization(resource(tmp_path), transport=httpx.MockTransport(respond))
    tokens = await port.exchange(context(port), SecretStr("synthetic-code"))
    assert tokens.scopes == ("read",)
    assert "synthetic-access" not in repr(tokens)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_adq_wrong_account_never_becomes_authorized(tmp_path: Path) -> None:
    port = NativeAuthorization(
        resource(tmp_path),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "access_token": "synthetic",
                        "access_token_expires_in": 60,
                        "authorizer_info": {"account_id": "other"},
                    },
                },
            )
        ),
    )
    with pytest.raises(CodeExchangeFailure):
        await port.exchange(context(port), SecretStr("synthetic-code"))


@pytest.mark.asyncio
async def test_cross_tenant_exchange_does_not_call_upstream(tmp_path: Path) -> None:
    def no_call(_: httpx.Request) -> httpx.Response:
        pytest.fail("跨租户不能读取凭据或出站")

    port = NativeAuthorization(resource(tmp_path), transport=httpx.MockTransport(no_call))
    ctx = replace(context(port), identity=VerifiedIdentity(2, uuid4(), uuid4()))
    with pytest.raises(CodeExchangeFailure):
        await port.exchange(ctx, SecretStr("synthetic"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body",
    [
        (302, {}),
        (500, {}),
        (200, {"access_token": "leaked"}),
        (200, {"errcode": 48001, "errmsg": "private upstream text"}),
        (200, {"access_token": "leaked", "expires_in": True}),
    ],
)
async def test_unconfirmed_responses_are_not_success_or_retried(
    tmp_path: Path, status: int, body: dict[str, Any]
) -> None:
    calls = []

    def respond(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, json=body, headers={"Location": "https://other.invalid"})

    port = NativeAuthorization(resource(tmp_path, "wxshop"), transport=httpx.MockTransport(respond))
    with pytest.raises(CodeExchangeFailure) as error:
        await port.renew(None)
    assert "private upstream text" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_wx_stable_renewal_is_self_credential_and_uses_actual_ttl(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/cgi-bin/stable_token"
        assert json.loads(request.content)["force_refresh"] is False
        return httpx.Response(200, json={"access_token": "synthetic-wx", "expires_in": 20})

    port = NativeAuthorization(resource(tmp_path, "wxshop"), transport=httpx.MockTransport(respond))
    tokens = await port.renew(None)
    assert 0 < (tokens.expires_at - datetime.now(UTC)).total_seconds() <= 20
    assert tokens.refresh_token is None


def exchange(data: Any) -> HttpExchange:
    return HttpExchange(
        uuid4(), "test.read", 200, 1, datetime.now(UTC), {}, json.dumps(data).encode()
    )


def test_read_protocol_preserves_version_and_signs_auth_outside_business_parameters() -> None:
    spec = read_spec("adq.campaigns", "a" * 20, "b" * 64)
    assert spec.path == "/v1.3/campaigns/get" and spec.max_read_attempts == 1
    request = native_signer(
        httpx.Request("GET", "https://api.e.qq.com/v1.3/campaigns/get?page=2"),
        SecretStr("synthetic"),
    )
    assert request.url.params["page"] == "2"
    assert request.url.params["access_token"] == "synthetic"
    assert len(request.url.params["nonce"]) <= 32


def test_adq_page_and_wx_cursor_do_not_fake_completeness() -> None:
    page = parse_page(
        "adq.campaigns",
        exchange(
            {
                "code": 0,
                "data": {
                    "list": [{"campaign_id": "c1", "access_token": "private"}],
                    "page_info": {"total_page": 2},
                },
            }
        ),
    )
    assert page.next_cursor == "2" and "access_token" not in page.records[0]
    with pytest.raises(ValueError):
        parse_page("adq.campaigns", exchange({"code": 0, "data": {"list": []}}))
    page = parse_page(
        "wxshop.products", exchange({"errcode": 0, "product_ids": [], "next_key": ""})
    )
    assert page.records == () and page.next_cursor is None


def test_missing_money_is_not_zero_and_shop_pii_is_omitted() -> None:
    with pytest.raises(ValueError):
        parse_page("wxshop.balance", exchange({"errcode": 0}))
    page = parse_page(
        "wxshop.balance", exchange({"errcode": 0, "available_amount": 0, "pending_amount": 3})
    )
    assert page.records[0]["available_amount"] == 0
    page = parse_page(
        "wxshop.info", exchange({"errcode": 0, "info": {"nickname": "shop", "phone": "private"}})
    )
    assert "phone" not in page.records[0]
    assert decode_verdict(exchange({"data": {}})).decision == "UNCONFIRMED"


def test_different_shop_constraint_is_preserved(tmp_path: Path) -> None:
    adq = resource(tmp_path)
    wx = resource(tmp_path, "wxshop", shop_id=adq.shop_id)
    with pytest.raises(ValidationError):
        ResourceBundle(applications=(adq, wx), shop_relationship="DISTINCT_SHOPS")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameters,path",
    [
        ({"account_id": "foreign"}, "/v3.0/advertiser/get"),
        ({"url": "https://other.invalid"}, "/v3.0/advertiser/get"),
        ({}, "/not-approved"),
    ],
)
async def test_read_adapter_refuses_identity_route_and_unapproved_interface(
    parameters: dict[str, Any], path: str
) -> None:
    ctx = GatewayContext(
        VerifiedIdentity(1, uuid4(), uuid4()),
        uuid4(),
        uuid4(),
        "12345",
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
    client, token = AsyncMock(), AsyncMock(return_value=SecretStr("synthetic"))
    adapter = ReadOnlyNativeAdapter(client, token, "adq", (path,))
    with pytest.raises((ValueError, ApiError)):
        await adapter.read(
            ctx,
            ResourceQuery(
                operation="adq.account", catalog_id=ctx.catalog_id, parameters=parameters
            ),
        )
    client.send.assert_not_awaited()
    token.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/v1/connections/oauth/invalid-id/callback", "/v1/connections/oauth/unknown/path"]
)
async def test_callback_query_is_private_even_when_route_is_invalid(path: str) -> None:
    captured = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        captured.append(scope)

    async def receive() -> Any:
        return {"type": "http.disconnect"}

    async def send(_: Any) -> None:
        pass

    scope: Any = {
        "type": "http",
        "path": path,
        "query_string": b"state=synthetic-state&authorization_code=synthetic-private-code",
    }
    await OAuthCallbackPrivacy(app)(scope, receive, send)
    assert captured[0]["query_string"] == b""
    assert "synthetic-private-code" not in repr(captured[0])
    assert captured[0]["state"]["platform_oauth_query"][
        "authorization_code"
    ].get_secret_value() == ("synthetic-private-code")
