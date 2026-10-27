import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from yuliang.main import create_app
from yuliang.settings import Settings


def test_identity_routes_default_disabled_until_f01(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/v1/auth/session").status_code == 404


def test_auth_rollout_requires_independent_secret_and_origins(settings: Settings) -> None:
    values = settings.model_dump()
    values.update(identity_api_enabled=True, allowed_origins=["http://localhost:3000"])
    with pytest.raises(ValidationError):
        Settings(**values)
    values["auth_audit_secret"] = settings.session_secret
    with pytest.raises(ValidationError):
        Settings(**values)
    values["auth_audit_secret"] = SecretStr("independent-auth-audit-key-for-unit-test-only")
    assert Settings(**values).identity_api_enabled
