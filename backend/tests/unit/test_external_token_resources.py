from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from test_platform_resources import resource

from yuliang.modules.connectors.native_auth import NativeAuthorization
from yuliang.modules.connectors.oauth import CodeExchangeFailure


def reference(tmp_path: Path, **changes: object) -> dict[str, object]:
    return (
        dict(
            base_url="http://127.0.0.1:19091",
            owner_confirmed=True,
            api_key=dict(file=tmp_path / "not-read.env", key="SERVICE_KEY"),
        )
        | changes
    )


def test_external_config_has_one_explicit_owner_and_no_secret_read(tmp_path: Path) -> None:
    item = resource(
        tmp_path, token_owner_transferred=False, external_token_service=reference(tmp_path)
    )
    assert item.external_token_service is not None
    with pytest.raises(ValidationError):
        resource(tmp_path, external_token_service=reference(tmp_path))
    with pytest.raises(ValidationError):
        resource(
            tmp_path,
            token_owner_transferred=False,
            external_token_service=reference(tmp_path, owner_confirmed=False),
        )
    with pytest.raises(ValidationError):
        resource(
            tmp_path,
            token_owner_transferred=False,
            external_token_service=reference(tmp_path, base_url="http://example.com:90"),
        )


@pytest.mark.asyncio
async def test_external_config_cannot_start_native_refresh_or_authorization(tmp_path: Path) -> None:
    item = resource(
        tmp_path, token_owner_transferred=False, external_token_service=reference(tmp_path)
    )
    port = NativeAuthorization(item)
    with pytest.raises(ValueError):
        port.authorize_url(SecretStr("synthetic-state"))
    with pytest.raises(CodeExchangeFailure):
        await port.renew(SecretStr("synthetic-refresh"))
