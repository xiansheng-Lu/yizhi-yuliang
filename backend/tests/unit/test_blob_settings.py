from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from yuliang.settings import Settings


def enabled(settings: Settings, **changes: Any) -> Settings:
    values = settings.model_dump() | dict(
        identity_api_enabled=True,
        auth_audit_secret=SecretStr("blob-audit-" * 8),
        allowed_origins=["http://localhost:3000"],
        blob_enabled=True,
        blob_endpoint_url="http://127.0.0.1:19000",
        blob_bucket="synthetic-private-assets",
        blob_access_key=SecretStr("synthetic-access"),
        blob_secret_key=SecretStr("synthetic-secret"),
    )
    return Settings(_env_file=None, **(values | changes))


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com",
        "http://127.0.0.1:19000/bucket",
        "https://host/?override=1",
        "https://user:private@host",
        "file:///tmp/store",
    ],
)
def test_blob_rejects_unsafe_endpoints(settings: Settings, endpoint: str) -> None:
    with pytest.raises(ValidationError):
        enabled(settings, blob_endpoint_url=endpoint)


def test_blob_production_tls_and_missing_configuration(settings: Settings) -> None:
    with pytest.raises(ValidationError):
        enabled(settings, environment="prod")
    assert enabled(
        settings, environment="prod", blob_endpoint_url="https://storage.example.invalid"
    )
    for field in ("blob_bucket", "blob_secret_key", "blob_access_key"):
        with pytest.raises(ValidationError):
            enabled(settings, **{field: None})
    assert "synthetic-secret" not in repr(enabled(settings))
    assert settings.blob_enabled is False


def test_blob_bounds_and_identity_gate(settings: Settings) -> None:
    with pytest.raises(ValidationError):
        enabled(settings, identity_api_enabled=False)
    with pytest.raises(ValidationError):
        enabled(settings, blob_upload_ttl_seconds=901)
    with pytest.raises(ValidationError):
        enabled(settings, blob_download_ttl_seconds=0)
