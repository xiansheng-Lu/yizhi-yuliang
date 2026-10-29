"""中转站生图适配器单元测试：全部经假传输，默认不发生真实付费调用。"""

import base64
import hashlib
import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from yuliang.modules.content.relay_image_generation_provider import (
    RelayImageGenerationProvider,
)
from yuliang.platform.errors import ApiError
from yuliang.settings import Settings

# 1x1 透明PNG，可被现有媒体探测解码。
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
ENDPOINT = "https://relay.test.invalid/v1/api/generate"
RESULT_URL = "https://file.test.invalid/output.png"
EXTERNAL_ID = "2-5486c9a3-0000-4000-8000-000000000001"


class RecordingWriter:
    def __init__(self, asset: UUID | None = None) -> None:
        self.asset = asset or UUID("00000000-0000-4000-8000-0000000000aa")
        self.calls: list[tuple[str, str, bytes, str]] = []

    def write(self, request_key: str, document_id: str, data: bytes, mime: str) -> UUID:
        self.calls.append((request_key, document_id, data, mime))
        return self.asset


def provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    price_minor: int = 37,
    writer: RecordingWriter | None = None,
) -> tuple[RelayImageGenerationProvider, RecordingWriter]:
    recorder = writer or RecordingWriter()
    value = RelayImageGenerationProvider(
        endpoint=ENDPOINT,
        model="gpt-image-2.5",
        api_key=SecretStr("synthetic-not-real-key"),
        price_minor=price_minor,
        writer=recorder,
        transport=httpx.MockTransport(handler),
    )
    return value, recorder


def parameters(kind: str = "IMAGE") -> dict[str, object]:
    return {
        "kind": kind,
        "prompt": "一只白猫坐在窗台上",
        "documentId": str(uuid4()),
    }


def succeeded(request: httpx.Request) -> httpx.Response:
    assert request.url == httpx.URL(ENDPOINT)
    assert request.headers["authorization"] == "Bearer synthetic-not-real-key"
    body = json.loads(request.content)
    assert body == {
        "model": "gpt-image-2.5",
        "prompt": "一只白猫坐在窗台上",
        "size": "1024x1024",
        "n": 1,
    }
    return httpx.Response(
        200,
        json={
            "id": EXTERNAL_ID,
            "status": "succeeded",
            "results": [{"url": RESULT_URL}],
            "progress": 100,
        },
    )


def downloaded(declared: str = "image/png", data: bytes = PNG) -> httpx.Response:
    return httpx.Response(200, content=data, headers={"content-type": declared})


def relay_success(request: httpx.Request) -> httpx.Response:
    if request.url == httpx.URL(ENDPOINT):
        return succeeded(request)
    if request.url == httpx.URL(RESULT_URL):
        return downloaded()
    raise AssertionError("未预期的下游地址")


@pytest.mark.parametrize("kind", ["IMAGE", "GRAPHIC"])
def test_success_only_returns_confirmed_internal_asset(kind: str) -> None:
    value, writer = provider(relay_success)
    request_key = str(uuid4())
    result = value.submit(request_key, "a" * 64, parameters(kind))
    assert result.state == "READY_FOR_REVIEW"
    assert result.external_id == EXTERNAL_ID
    assert result.asset_id == writer.asset
    assert len(writer.calls) == 1
    assert writer.calls[0][0] == request_key
    assert writer.calls[0][2] == PNG
    assert writer.calls[0][3] == "image/png"
    assert result.billing.state == "KNOWN"
    assert result.billing.amount_minor == 37
    assert result.billing.currency == "CNY"
    proof = result.billing.proof_hash
    assert proof is not None
    assert len(proof) == 64
    int(proof, 16)
    assert proof == hashlib.sha256(
        json.dumps(
            {
                "provider": "relay-image",
                "endpoint": ENDPOINT,
                "model": "gpt-image-2.5",
                "requestKey": request_key,
                "externalId": EXTERNAL_ID,
                "sha256": hashlib.sha256(PNG).hexdigest(),
                "amountMinor": 37,
                "currency": "CNY",
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "UPSTREAM_ERROR"), (401, "UPSTREAM_ERROR"), (500, "RESULT_UNKNOWN")],
)
def test_http_failure_is_not_treated_as_success(status: int, code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"id": EXTERNAL_ID, "status": "failed"})

    value, writer = provider(handler)
    with pytest.raises(ApiError) as caught:
        value.submit(str(uuid4()), "a" * 64, parameters())
    assert caught.value.code.value == code
    assert writer.calls == []


def test_provider_failed_receipt_returns_failed_without_asset() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": EXTERNAL_ID, "status": "failed", "error": "prompt is empty"},
        )

    value, writer = provider(handler)
    result = value.submit(str(uuid4()), "a" * 64, parameters())
    assert result.state == "FAILED"
    assert result.external_id == EXTERNAL_ID
    assert result.asset_id is None
    assert result.billing.state == "UNKNOWN"
    assert writer.calls == []


def test_empty_results_and_missing_url_stay_unknown() -> None:
    results: list[dict[str, object]] = [
        {"id": EXTERNAL_ID, "status": "succeeded", "results": []},
        {
            "id": EXTERNAL_ID,
            "status": "succeeded",
            "results": [{"url": "http://file.test.invalid/x.png"}],
        },
        {"status": "succeeded", "results": [{"url": RESULT_URL}]},
        {"id": EXTERNAL_ID, "status": "processing"},
    ]
    for payload in results:
        def handler(
            request: httpx.Request, payload: dict[str, object] = payload
        ) -> httpx.Response:
            return httpx.Response(200, json=payload)

        value, writer = provider(handler)
        with pytest.raises(ApiError) as caught:
            value.submit(str(uuid4()), "a" * 64, parameters())
        assert caught.value.code.value == "RESULT_UNKNOWN"
        assert writer.calls == []


def test_download_mime_and_actual_bytes_are_both_checked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(ENDPOINT):
            return succeeded(request)
        return downloaded("text/plain")

    value, writer = provider(handler)
    with pytest.raises(ApiError) as caught:
        value.submit(str(uuid4()), "a" * 64, parameters())
    assert caught.value.code.value == "RESULT_UNKNOWN"
    assert writer.calls == []

    def mismatched(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(ENDPOINT):
            return succeeded(request)
        return downloaded("image/png", b"not a real PNG")

    value, writer = provider(mismatched)
    with pytest.raises(ApiError) as caught:
        value.submit(str(uuid4()), "a" * 64, parameters())
    assert caught.value.code.value == "RESULT_UNKNOWN"
    assert writer.calls == []


def test_download_non_2xx_stays_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(ENDPOINT):
            return succeeded(request)
        return httpx.Response(503)

    value, writer = provider(handler)
    with pytest.raises(ApiError) as caught:
        value.submit(str(uuid4()), "a" * 64, parameters())
    assert caught.value.code.value == "RESULT_UNKNOWN"
    assert writer.calls == []


def test_timeout_keeps_unknown_and_never_writes_asset() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    value, writer = provider(handler)
    with pytest.raises(ApiError) as caught:
        value.submit(str(uuid4()), "a" * 64, parameters())
    assert caught.value.code.value == "RESULT_UNKNOWN"
    assert writer.calls == []


@pytest.mark.parametrize("kind", ["VIDEO", "VIDEO_EDIT", "SCRIPT"])
def test_non_image_kind_is_rejected_before_any_call(kind: str) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    value, writer = provider(handler)
    with pytest.raises(ApiError) as caught:
        value.submit(str(uuid4()), "a" * 64, parameters(kind))
    assert caught.value.code.value == "CAPABILITY_UNAVAILABLE"
    assert calls == []
    assert writer.calls == []


def test_query_never_fabricates_sync_relay_state() -> None:
    value, _ = provider(lambda request: httpx.Response(200, json={}))
    assert value.query(str(uuid4())).state == "UNKNOWN"
    assert value.query(str(uuid4())).billing.state == "UNKNOWN"


def test_price_missing_or_invalid_is_refused() -> None:
    for price in (0, -1, 37.5):
        with pytest.raises(ValueError):
            RelayImageGenerationProvider(
                endpoint=ENDPOINT,
                model="gpt-image-2.5",
                api_key=SecretStr("synthetic-not-real-key"),
                price_minor=price,  # type: ignore[arg-type]
                writer=RecordingWriter(),
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json={})
                ),
            )


def settings_values(settings: Settings) -> dict[str, Any]:
    return settings.model_dump()


def test_settings_converts_yuan_to_integral_minor(settings: Settings) -> None:
    value = Settings(
        _env_file=None,
        **(
            settings_values(settings)
            | {
                "imagegen_primary_endpoint": ENDPOINT,
                "imagegen_primary_model": "gpt-image-2.5",
                "imagegen_primary_key": SecretStr("synthetic-not-real-key"),
                "imagegen_primary_price_per_call": Decimal("1.23"),
            }
        ),
    )
    assert value.imagegen_price_per_call_minor == 123


def test_settings_reject_zero_or_sub_cent_price(settings: Settings) -> None:
    base = settings_values(settings) | {
        "imagegen_primary_endpoint": ENDPOINT,
        "imagegen_primary_model": "gpt-image-2.5",
        "imagegen_primary_key": SecretStr("synthetic-not-real-key"),
    }
    for price in ("0", "0.001"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **(base | {"imagegen_primary_price_per_call": price}))


def test_settings_blank_price_means_unconfigured_not_zero(settings: Settings) -> None:
    value = Settings(
        _env_file=None,
        **(
            settings_values(settings)
            | {
                "imagegen_primary_endpoint": ENDPOINT,
                "imagegen_primary_model": "gpt-image-2.5",
                "imagegen_primary_key": SecretStr("synthetic-not-real-key"),
                "imagegen_primary_price_per_call": "",
            }
        ),
    )
    assert value.imagegen_price_per_call_minor is None


def test_settings_endpoint_is_exact_https_without_credentials(settings: Settings) -> None:
    base = settings_values(settings) | {"imagegen_primary_model": "gpt-image-2.5"}
    bad = (
        "http://relay.test.invalid/v1/api/generate",
        "https://user:pass@relay.test.invalid/v1/api/generate",
        "https://relay.test.invalid",
        "https://relay.test.invalid/v1/api/generate?token=leak",
    )
    for endpoint in bad:
        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                **(
                    base
                    | {
                        "imagegen_primary_endpoint": endpoint,
                        "imagegen_primary_key": SecretStr("synthetic-not-real-key"),
                        "imagegen_primary_price_per_call": "1",
                    }
                ),
            )


def test_settings_require_paired_endpoint_and_secret(settings: Settings) -> None:
    base = settings_values(settings) | {
        "imagegen_primary_model": "gpt-image-2.5",
        "imagegen_primary_price_per_call": "1",
    }
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **(base | {"imagegen_primary_endpoint": ENDPOINT}))
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            **(
                base
                | {"imagegen_primary_key": SecretStr("synthetic-not-real-key")}
            ),
        )
