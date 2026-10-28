"""真实PG state/密文/跨进程锁与持久降级，平台侧使用显式MockTransport。"""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.modules.connectors.base import TokenSet
from yuliang.modules.connectors.native_auth import NativeAuthorization
from yuliang.modules.connectors.oauth import OAuthRejected
from yuliang.modules.connectors.renewal import renew_authorization
from yuliang.modules.connectors.resources import ApplicationResource, ResourceBundle
from yuliang.modules.connectors.runtime import PlatformRuntime
from yuliang.platform.db import business_transaction

from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import challenge
from .test_password_sessions import login_api as login_api

pytestmark = pytest.mark.integration


def port_for(data: dict[str, Any], tmp_path: Path, connector: str = "adq") -> NativeAuthorization:
    path = tmp_path / ".env"
    path.write_text("APP_SECRET=synthetic-secret\n", encoding="utf-8")
    application_id = uuid4()
    resource = ApplicationResource.model_validate(
        {
            "id": application_id,
            "connector_id": connector,
            "app_id": "synthetic-app",
            "mode": "ADQ_OAUTH" if connector == "adq" else "WXSHOP_SELF",
            "source_root": tmp_path,
            "secret": {"file": path, "key": "APP_SECRET"},
            "tenant_id": data["identity"].tenant_id,
            "shop_id": uuid4(),
            "native_id": "12345",
            "granted_scopes": (),
            "permissions_evidence": "synthetic-fixture",
            "token_owner_transferred": True,
            "enabled": True,
            "redirect_uri": f"https://callback.example.invalid/v1/connections/oauth/{application_id}/callback"
            if connector == "adq"
            else None,
        }
    )

    def respond(request: httpx.Request) -> httpx.Response:
        if connector == "wxshop":
            return httpx.Response(200, json={"access_token": "synthetic-wx", "expires_in": 30})
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "access_token": "synthetic-adq",
                    "refresh_token": "synthetic-refresh",
                    "access_token_expires_in": 30,
                    "authorizer_info": {"account_id": "12345", "scope_list": ["read"]},
                },
            },
        )

    return NativeAuthorization(resource, transport=httpx.MockTransport(respond))


async def connect(data: dict[str, Any], port: NativeAuthorization) -> Any:
    service = data["service"]
    started = service.start(data["identity"], data["session"], port.application(), uuid4())
    response = await service.complete(
        started.state, SecretStr("synthetic-code"), port.application(), port
    )
    assert "synthetic-adq" not in str(response) and response["status"] == "ACTIVE"
    return started


@pytest.mark.asyncio
@pytest.mark.parametrize("connector", ["adq", "wxshop"])
async def test_native_exchange_uses_real_vault_and_rejects_state_replay(
    oauth_ready: dict[str, Any], tmp_path: Path, connector: str
) -> None:
    data = oauth_ready
    port = port_for(data, tmp_path, connector)
    started = await connect(data, port)
    tokens = data["service"].tokens(data["identity"], data["session"], started.authorization_id)
    assert tokens.access_token.get_secret_value().startswith("synthetic-")
    with business_transaction(data["service"].engine, data["identity"]) as session:
        cipher = session.execute(
            text("SELECT access_token_enc FROM provider_authorizations WHERE id=:id"),
            {"id": started.authorization_id},
        ).scalar_one()
        assert b"synthetic" not in bytes(cipher)
    with pytest.raises(OAuthRejected):
        await data["service"].complete(
            started.state, SecretStr("synthetic-code"), port.application(), port
        )


@pytest.mark.asyncio
async def test_refresh_competition_one_upstream_and_rotated_refresh_persisted(
    oauth_ready: dict[str, Any], tmp_path: Path
) -> None:
    data = oauth_ready
    port = port_for(data, tmp_path)
    started = await connect(data, port)
    entered, release = asyncio.Event(), asyncio.Event()

    async def upstream(_: SecretStr | None) -> TokenSet:
        entered.set()
        await release.wait()
        return TokenSet(
            access_token=SecretStr("synthetic-new-access"),
            refresh_token=SecretStr("synthetic-rotated-refresh"),
            scopes=(),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    async def renew() -> TokenSet:
        return await renew_authorization(
            data["service"],
            data["identity"],
            data["session"],
            started.authorization_id,
            uuid4(),
            port,
        )

    with patch.object(port, "renew", new=AsyncMock(side_effect=upstream)) as network:
        first = asyncio.create_task(renew())
        await asyncio.wait_for(entered.wait(), timeout=5)
        try:
            with pytest.raises(OAuthRejected):
                await renew()
        finally:
            release.set()
            await first
        tokens = await renew()  # 已刷新成功，第二个请求复用有效快照，不再次轮换。
        assert network.await_count == 1
        assert tokens.scopes == ("read",)
        assert tokens.refresh_token is not None
        assert tokens.refresh_token.get_secret_value() == "synthetic-rotated-refresh"


@pytest.mark.asyncio
async def test_refresh_failure_commits_degraded_status_and_revocation_never_refreshes(
    oauth_ready: dict[str, Any], tmp_path: Path
) -> None:
    data = oauth_ready
    port = port_for(data, tmp_path)
    started = await connect(data, port)
    with patch.object(
        port, "renew", new=AsyncMock(side_effect=RuntimeError("private upstream text"))
    ):
        with pytest.raises(OAuthRejected):
            await renew_authorization(
                data["service"],
                data["identity"],
                data["session"],
                started.authorization_id,
                uuid4(),
                port,
            )
    with business_transaction(data["service"].engine, data["identity"]) as session:
        status = session.execute(
            text("SELECT status FROM provider_authorizations WHERE id=:id"),
            {"id": started.authorization_id},
        ).scalar_one()
        assert status == "REFRESH_FAILED"
    with data["maintenance"].begin() as connection:
        connection.execute(text("SET LOCAL ROLE app_owner"))
        connection.execute(
            text("UPDATE provider_authorizations SET status='REVOKED' WHERE id=:id"),
            {"id": started.authorization_id},
        )
    with patch.object(port, "renew", new=AsyncMock()) as network:
        with pytest.raises(OAuthRejected):
            await renew_authorization(
                data["service"],
                data["identity"],
                data["session"],
                started.authorization_id,
                uuid4(),
                port,
            )
        network.assert_not_awaited()


def test_http_self_credential_activation_and_reauthorization_use_current_session(
    oauth_ready: dict[str, Any], tmp_path: Path
) -> None:
    data = oauth_ready
    port = port_for(data, tmp_path, "wxshop")
    keyring, state_file = tmp_path / "keys.json", tmp_path / "state.key"
    keyring.write_text(json.dumps({"1": base64.b64encode(b"T" * 32).decode()}))
    state_file.write_text("synthetic-independent-state-key-of-sufficient-length")
    target = PlatformRuntime(
        data["service"].engine,
        data["app"].state.settings,
        ResourceBundle(
            applications=(port.resource,), keyring_file=keyring, state_secret_file=state_file
        ),
    )
    target.ports[port.resource.id] = port
    assert not target.adq_assets  # 空scope仍可授权；镜像同步保持关闭。
    data["app"].state.platform_runtime = target
    client = data["client"]
    path = f"/v1/connections/applications/{port.resource.id}/authorize"
    try:
        assert client.post(path).status_code == 403
        assert len(client.get("/v1/connections/applications").json()["data"]["applications"]) == 1
        response = client.post(path, headers={"X-CSRF-Token": challenge(client)})
        assert response.status_code == 200, response.text
        value = response.json()["data"]
        assert value["authorizationMode"] == "SELF_CREDENTIAL"
        assert "synthetic-wx" not in response.text and "refresh_token" not in response.text
        grant = client.get("/v1/connections").json()["data"]["connections"][0]
        assert grant["reauthorizationAvailable"] is True
        response = client.post(
            f"/v1/connections/{value['authorizationId']}/refresh",
            headers={"X-CSRF-Token": challenge(client)},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["authorizationId"] != value["authorizationId"]
        client.cookies.clear()
        assert client.get("/v1/connections/applications").status_code == 401
    finally:
        target.close()
        data["app"].state.platform_runtime = None


def test_http_adq_callback_without_cookie_and_without_secret_query_in_server_scope(
    oauth_ready: dict[str, Any], tmp_path: Path
) -> None:
    from urllib.parse import parse_qs, urlsplit

    data = oauth_ready
    port = port_for(data, tmp_path)
    keyring, state_file = tmp_path / "keys.json", tmp_path / "state.key"
    keyring.write_text(json.dumps({"1": base64.b64encode(b"T" * 32).decode()}))
    state_file.write_text("synthetic-independent-state-key-of-sufficient-length")
    target = PlatformRuntime(
        data["service"].engine,
        data["app"].state.settings,
        ResourceBundle(
            applications=(port.resource,), keyring_file=keyring, state_secret_file=state_file
        ),
    )
    target.ports[port.resource.id] = port
    data["app"].state.platform_runtime = target
    client = data["client"]
    try:
        response = client.post(
            f"/v1/connections/applications/{port.resource.id}/authorize",
            headers={"X-CSRF-Token": challenge(client)},
        )
        assert response.status_code == 200, response.text
        assert not target.adq_assets  # 授权回调可用，空scope不能装配镜像。
        query = parse_qs(urlsplit(response.json()["data"]["authorizationUrl"]).query)
        client.cookies.clear()
        response = client.get(
            f"/v1/connections/oauth/{port.resource.id}/callback",
            params={"state": query["state"][0], "authorization_code": "synthetic-code"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "ACTIVE"
        assert "synthetic-adq" not in response.text and query["state"][0] not in response.text
        response = client.get(
            f"/v1/connections/oauth/{port.resource.id}/callback",
            params={"state": query["state"][0], "authorization_code": "synthetic-code"},
        )
        assert response.status_code == 422
    finally:
        target.close()
        data["app"].state.platform_runtime = None


@pytest.mark.asyncio
async def test_cancelled_refresh_is_persisted_as_failed(
    oauth_ready: dict[str, Any], tmp_path: Path
) -> None:
    data = oauth_ready
    port = port_for(data, tmp_path)
    started = await connect(data, port)
    entered = asyncio.Event()

    async def upstream(_: SecretStr | None) -> TokenSet:
        entered.set()
        await asyncio.Event().wait()
        raise RuntimeError("unreachable")

    with patch.object(port, "renew", new=AsyncMock(side_effect=upstream)):
        task = asyncio.create_task(
            renew_authorization(
                data["service"],
                data["identity"],
                data["session"],
                started.authorization_id,
                uuid4(),
                port,
            )
        )
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    with business_transaction(data["service"].engine, data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT status FROM provider_authorizations WHERE id=:id"),
                {"id": started.authorization_id},
            ).scalar_one()
            == "REFRESH_FAILED"
        )


@pytest.mark.asyncio
async def test_restricted_renewal_function_rejects_wrong_actor_and_stale_version(
    oauth_ready: dict[str, Any], tmp_path: Path
) -> None:
    from sqlalchemy.exc import DBAPIError

    data = oauth_ready
    port = port_for(data, tmp_path)
    started = await connect(data, port)
    statement = text("""SELECT access.renew_provider_token(:member,:user,:session,:id,:app,
        'synthetic-app','adq',:version,false,NULL,NULL,NULL,NULL,NULL)""")
    for actor, version in ((uuid4(), 1), (data["identity"].user_id, 0)):
        with business_transaction(data["service"].engine, data["identity"]) as session:
            assert (
                session.execute(
                    statement,
                    {
                        "member": data["identity"].membership_id,
                        "user": actor,
                        "session": data["session"],
                        "id": started.authorization_id,
                        "app": port.resource.id,
                        "version": version,
                    },
                ).scalar_one()
                is False
            )
    with (
        pytest.raises(DBAPIError),
        business_transaction(data["service"].engine, data["identity"]) as session,
    ):
        session.execute(
            text("UPDATE provider_authorizations SET status='ACTIVE' WHERE id=:id"),
            {"id": started.authorization_id},
        )
