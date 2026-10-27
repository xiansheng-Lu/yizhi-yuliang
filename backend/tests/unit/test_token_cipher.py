"""真实AEAD反篡改与上下文隔离；密钥全部临时生成。"""

import secrets
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretBytes, SecretStr

from yuliang.modules.connectors.base import AuthorizationGatewayContext, AuthorizationRef
from yuliang.modules.connectors.http import OperationSpec, PlatformHttpClient
from yuliang.modules.connectors.token_cipher import (
    Ciphertext,
    TokenCipher,
    TokenCipherError,
    TokenContext,
)
from yuliang.platform.db import VerifiedIdentity


def test_aead_random_nonce_tamper_and_context_binding() -> None:
    cipher = TokenCipher({1: SecretBytes(secrets.token_bytes(32))}, 1)
    context = TokenContext(1, uuid4(), uuid4(), "synthetic-app", "adq", "tencent", "adq")
    token = SecretStr("synthetic-token-only")
    first = cipher.seal(context, "access", token)
    second = cipher.seal(context, "access", token)
    assert first.value != second.value and token.get_secret_value().encode() not in first.value
    assert cipher.open(context, "access", first) == token
    for changed in [
        replace(context, tenant_id=2),
        replace(context, authorization_id=uuid4()),
        replace(context, application_id=uuid4()),
        replace(context, app_id="other"),
        replace(context, connector_id="wxpromo"),
        replace(context, provider="wxshop"),
        replace(context, product_line="shop"),
    ]:
        with pytest.raises(TokenCipherError):
            cipher.open(changed, "access", first)
    with pytest.raises(TokenCipherError):
        cipher.open(context, "refresh", first)
    for broken in [first.value[:-1] + bytes([first.value[-1] ^ 1]), b"", first.value[:10]]:
        with pytest.raises(TokenCipherError):
            cipher.open(context, "access", Ciphertext(1, broken))
    with pytest.raises(TokenCipherError):
        cipher.open(context, "access", Ciphertext(2, first.value))
    assert token.get_secret_value() not in repr(first) + repr(cipher)


def test_rotation_retains_old_decrypt_only_until_reencrypted() -> None:
    old, new = SecretBytes(secrets.token_bytes(32)), SecretBytes(secrets.token_bytes(32))
    context = TokenContext(1, uuid4(), uuid4(), "synthetic-app", "adq", "tencent", "adq")
    first = TokenCipher({1: old}, 1).seal(context, "refresh", SecretStr("synthetic-refresh"))
    rotating = TokenCipher({1: old, 2: new}, 2)
    second = rotating.seal(context, "refresh", rotating.open(context, "refresh", first))
    assert second.key_version == 2
    current = TokenCipher({2: new}, 2)
    assert current.open(context, "refresh", second).get_secret_value() == "synthetic-refresh"
    with pytest.raises(TokenCipherError):
        current.open(context, "refresh", first)


def test_application_authorization_does_not_fabricate_account() -> None:
    auth = AuthorizationRef(
        tenant_id=1,
        application_id=uuid4(),
        connector_id="adq",
        provider="tencent",
        product_line="adq",
        authorization_id=uuid4(),
        authorization_version=1,
    )
    assert auth.account_id is None and auth.native_id is None
    with pytest.raises(ValueError):
        AuthorizationRef.model_validate(auth.model_dump() | {"account_id": uuid4()})


async def test_accountless_token_http_uses_existing_fixed_egress_without_budget_context() -> None:
    class Parameters(BaseModel):
        model_config = ConfigDict(extra="forbid")
        grant_type: str

    ctx = AuthorizationGatewayContext(
        VerifiedIdentity(1, uuid4(), uuid4()),
        uuid4(),
        "adq",
        "tencent",
        "adq",
        uuid4(),
        uuid4(),
        "a" * 20,
        "b" * 64,
    )
    spec = OperationSpec(
        "synthetic.token",
        ctx.catalog_id,
        ctx.catalog_sha256,
        "adq",
        "tencent",
        "adq",
        "api.e.qq.com",
        "GET",
        "/synthetic/token",
        "TOKEN_REFRESH",
        Parameters,
    )
    calls = []

    def signer(request: httpx.Request, credential: SecretStr) -> httpx.Request:
        request.url = request.url.copy_add_param("code", credential.get_secret_value())
        return request

    def platform(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    result = await PlatformHttpClient(signer, transport=httpx.MockTransport(platform)).send(
        ctx, spec, {"grant_type": "synthetic"}, SecretStr("synthetic-secret-code")
    )
    assert result.http_status == 500 and len(calls) == 1
    assert "account_id" not in str(calls[0].url)
    with pytest.raises(ValueError):
        await PlatformHttpClient(signer, transport=httpx.MockTransport(platform)).send(
            ctx, replace(spec, effect="READ"), {"grant_type": "synthetic"}, SecretStr("synthetic")
        )
    assert len(calls) == 1
