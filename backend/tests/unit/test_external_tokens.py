import httpx
import pytest
from pydantic import SecretStr, ValidationError

from yuliang.modules.connectors.external_tokens import (
    ExternalTokenConfig,
    ExternalTokenProvider,
    ExternalTokenUnavailable,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080",
        "https://example.com:443",
        "http://127.0.0.1:80/x",
        "http://user@127.0.0.1:80",
        "http://127.0.0.1:80?x=1",
    ],
)
def test_nonfixed_destinations_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        ExternalTokenConfig(base_url=url, api_key=SecretStr("synthetic"), product="wxshop")


@pytest.mark.asyncio
@pytest.mark.parametrize("product", ["adq", "wxshop"])
async def test_get_only_no_refresh_and_secret_repr(product: str) -> None:
    config = ExternalTokenConfig.model_validate(
        dict(
            base_url="http://127.0.0.1:8080",
            api_key="synthetic-key",
            product=product,
            account_id="123" if product == "adq" else None,
        )
    )

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path == "/api/token"
        assert request.url.params["raw"] == "1"
        assert "force_refresh" not in request.url.params
        assert request.headers["X-API-Key"] == "synthetic-key"
        return httpx.Response(
            200,
            json=dict(
                ok=True,
                masked=False,
                access_token="synthetic-token",
                remaining=120,
                account_id="123",
            ),
        )

    result = await ExternalTokenProvider(config, transport=httpx.MockTransport(handle)).get_token()
    assert result.remaining_seconds == 120
    assert "synthetic-token" not in repr(result)
    assert "synthetic-key" not in repr(config)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body",
    [
        (302, {}),
        (503, {"message": "secret"}),
        (200, {"ok": True, "masked": True}),
        (200, {"ok": True, "masked": False, "access_token": "secret", "remaining": True}),
    ],
)
async def test_fail_closed_without_response_leak(status: int, body: dict[str, object]) -> None:
    config = ExternalTokenConfig(
        base_url="http://127.0.0.1:8080", api_key=SecretStr("key"), product="wxshop"
    )
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json=body, headers={"Location": "http://example.com"})

    with pytest.raises(ExternalTokenUnavailable) as error:
        await ExternalTokenProvider(config, transport=httpx.MockTransport(handle)).get_token()
    assert len(calls) == 1
    assert "secret" not in str(error.value)


def test_invalid_configuration_does_not_expose_key() -> None:
    with pytest.raises(ValidationError) as error:
        ExternalTokenConfig.model_validate(
            dict(base_url="http://example.com:8080", api_key="private-input", product="wxshop")
        )
    assert "private-input" not in str(error.value)
    assert "private-input" not in repr(error.value)
