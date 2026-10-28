"""真实PG撤权与托管取令牌等待竞争；托管HTTP使用合成替身。"""

from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.external_tokens import ExternalToken
from yuliang.modules.connectors.native_auth import NativeAuthorization
from yuliang.modules.connectors.oauth import OAuthRejected
from yuliang.modules.connectors.resources import ApplicationResource
from yuliang.modules.connectors.runtime import PlatformRuntime

from .test_identity_database import database as database
from .test_native_platform_handoff import connect, port_for
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["NONE", "REVOKE", "VERSION", "WRONG_SHOP", "OLD_VERSION"])
async def test_external_token_rechecks_actual_grant_after_wait(
    oauth_ready: dict[str, Any], tmp_path: Path, change: str
) -> None:
    d = oauth_ready
    port = port_for(d, tmp_path)
    started = await connect(d, port)
    key = tmp_path / "service.env"
    key.write_text("SERVICE_KEY=synthetic-only\n", encoding="utf-8")
    item = ApplicationResource.model_validate(
        port.resource.model_dump()
        | dict(
            account_id=uuid4(),
            token_owner_transferred=False,
            external_token_service=dict(
                base_url="http://127.0.0.1:19091",
                owner_confirmed=True,
                api_key=dict(file=key, key="SERVICE_KEY"),
            ),
        )
    )
    target = object.__new__(PlatformRuntime)
    target.engine = d["service"].engine
    target.oauth = d["service"]
    target.ports = {item.id: NativeAuthorization(item)}
    ctx = GatewayContext(
        d["identity"],
        item.shop_id,
        item.account_id,
        "12345",
        "adq",
        "tencent",
        "adq",
        started.authorization_id,
        uuid4(),
        uuid4(),
        1,
        "a" * 20,
        "a" * 64,
    )
    if change == "WRONG_SHOP":
        ctx = replace(ctx, shop_id=uuid4())
    if change == "OLD_VERSION":
        ctx = replace(ctx, authorization_version=999)
    seen = []

    async def external(_: Any) -> ExternalToken:
        seen.append(True)
        if change in {"REVOKE", "VERSION"}:
            with d["maintenance"].begin() as owner:
                owner.execute(text("SET LOCAL ROLE app_owner"))
                owner.execute(
                    text("UPDATE provider_authorizations SET status='REVOKED' WHERE id=:id")
                    if change == "REVOKE"
                    else text("UPDATE provider_authorizations SET version=version+1 WHERE id=:id"),
                    dict(id=started.authorization_id),
                )
        return ExternalToken(access_token=SecretStr("synthetic-external"), remaining_seconds=60)

    with patch("yuliang.modules.connectors.runtime.ExternalTokenProvider.get_token", external):
        if change == "NONE":
            token = await target.token_for_read(ctx)
            assert token.get_secret_value() == "synthetic-external"
        else:
            with pytest.raises(OAuthRejected):
                await target.token_for_read(ctx)
    assert len(seen) == (0 if change in {"WRONG_SHOP", "OLD_VERSION"} else 1)
