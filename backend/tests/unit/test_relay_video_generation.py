"""中转站视频适配器与后台执行器的纯单元验证；HTTP全部走MockTransport。"""

import hashlib
import json
import struct
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from yuliang.infrastructure.mp4 import Mp4Error, mp4_duration_seconds
from yuliang.modules.content.relay_video_calls import RelayVideoCall
from yuliang.modules.content.relay_video_generation_provider import (
    RelayVideoGenerationProvider,
    reconcile_abandoned_calls,
)
from yuliang.modules.content.relay_video_generation_worker import (
    RelayVideoError,
    execute_relay_video_call,
    request_artifact,
)
from yuliang.modules.content.video_generation_config import (
    VideoProviderConfig,
    VideoTransportConfig,
    load_video_provider_config,
)
from yuliang.modules.content.video_generation_models import VideoStartRequest
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.errors import ApiError, ErrorCode

CONFIG_ENV = {
    "YULIANG_VIDEOGEN_PRIMARY_ENDPOINT": "https://relay.example.com/v1/video",
    "YULIANG_VIDEOGEN_PRIMARY_MODEL": "minimax-h3",
    "YULIANG_VIDEOGEN_PRIMARY_KEY": "test-only-key",
    "YULIANG_VIDEOGEN_PRIMARY_PRICE_PER_SECOND": "0.5",
    "YULIANG_VIDEOGEN_PRIMARY_RESOLUTION": "480p",
    "YULIANG_VIDEOGEN_PRIMARY_ASPECT_RATIO": "portrait",
}


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + kind + payload


def _mvhd(timescale: int, duration: int, *, version: int = 0) -> bytes:
    if version == 0:
        payload = b"\x00\x00\x00\x00" + bytes(8) + struct.pack(">II", timescale, duration)
    else:
        payload = b"\x01\x00\x00\x00" + bytes(16) + struct.pack(">IQ", timescale, duration)
    return _box(b"mvhd", payload + bytes(80))


def _mp4(timescale: int = 1000, duration: int = 2000, *, version: int = 0) -> bytes:
    return _box(b"ftyp", b"isom\x00\x00\x02\x00") + _box(
        b"moov", _mvhd(timescale, duration, version=version)
    )


def _transport(
    *,
    receipt_status: int = 200,
    receipt: dict[str, Any] | None = None,
    download_status: int = 200,
    mime: str = "video/mp4",
    data: bytes | None = None,
    seen: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    body = (
        {
            "id": "9-3891694e-0001",
            "status": "succeeded",
            "results": [{"url": "https://file.example.com/file/result.mp4"}],
            "progress": 100,
        }
        if receipt is None
        else receipt
    )
    content = _mp4() if data is None else data

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.method == "POST":
            return httpx.Response(receipt_status, json=body)
        return httpx.Response(download_status, content=content, headers={"Content-Type": mime})

    return httpx.MockTransport(handler)


@pytest.fixture
def config() -> VideoProviderConfig:
    return load_video_provider_config(CONFIG_ENV)


@pytest.fixture
def transport_config(config: VideoProviderConfig) -> VideoTransportConfig:
    return VideoTransportConfig(endpoint=config.endpoint, model=config.model, key=config.key)


class FakeCallStore:
    def __init__(self, identity: VerifiedIdentity, shop: UUID) -> None:
        self.identity = identity
        self.shop = shop
        self.rows: dict[UUID, RelayVideoCall] = {}

    def shop_for_request(self, request_key: UUID) -> UUID | None:
        return self.shop

    def create(
        self,
        *,
        request_key: UUID,
        shop_id: UUID,
        prompt: str,
        duration_seconds: int,
        resolution: str,
        aspect_ratio: str,
        pricing_mode: str,
        unit_price_micro_minor: int,
        currency: str,
    ) -> tuple[RelayVideoCall, bool]:
        for row in self.rows.values():
            if row.request_key == request_key:
                return row, False
        now = datetime.now(UTC)
        row = RelayVideoCall(
            id=uuid4(),
            tenant_id=self.identity.tenant_id,
            membership_id=self.identity.membership_id,
            actor_id=self.identity.user_id,
            shop_id=shop_id,
            request_key=request_key,
            state="PENDING",
            prompt=prompt,
            duration_seconds=duration_seconds,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            pricing_mode=pricing_mode,
            unit_price_micro_minor=unit_price_micro_minor,
            currency=currency,
            provider_task_id=None,
            blob_key=None,
            mime=None,
            size_bytes=None,
            sha256=None,
            measured_duration_seconds=None,
            settled_amount_micro_minor=None,
            error_code=None,
            error_message=None,
            created_at=now,
            updated_at=now,
        )
        self.rows[row.id] = row
        return row, True

    def get(self, call_id: UUID) -> RelayVideoCall | None:
        return self.rows.get(call_id)

    def claim(self, call_id: UUID) -> RelayVideoCall | None:
        row = self.rows.get(call_id)
        if row is None or row.state != "PENDING":
            return None
        row = replace(row, state="RUNNING", updated_at=datetime.now(UTC))
        self.rows[call_id] = row
        return row

    def succeed(
        self,
        call_id: UUID,
        *,
        provider_task_id: str,
        blob_key: str,
        mime: str,
        size_bytes: int,
        sha256: str,
        measured_duration_seconds: int,
        settled_amount_micro_minor: int,
    ) -> None:
        row = self.rows[call_id]
        self.rows[call_id] = replace(
            row,
            state="SUCCEEDED",
            provider_task_id=provider_task_id,
            blob_key=blob_key,
            mime=mime,
            size_bytes=size_bytes,
            sha256=sha256,
            measured_duration_seconds=measured_duration_seconds,
            settled_amount_micro_minor=settled_amount_micro_minor,
        )

    def fail(self, call_id: UUID, *, expected: str, error_code: str, error_message: str) -> bool:
        row = self.rows.get(call_id)
        if row is None or row.state != expected:
            return False
        self.rows[call_id] = replace(
            row, state="FAILED", error_code=error_code, error_message=error_message
        )
        return True

    def reconcile(self, older_than_seconds: int) -> int:
        assert older_than_seconds >= 60
        changed = 0
        for call_id, row in list(self.rows.items()):
            if row.state == "RUNNING":
                self.rows[call_id] = replace(
                    row,
                    state="FAILED",
                    error_code="OUTCOME_UNKNOWN",
                    error_message="超时",
                )
                changed += 1
        return changed


class FakeBlob:
    def __init__(self) -> None:
        self.puts: list[tuple[str, int, str, UUID]] = []

    def put_bytes(self, key: str, body: bytes, mime: str, upload: UUID) -> None:
        self.puts.append((key, len(body), mime, upload))


def _provider(
    config: VideoProviderConfig,
    store: FakeCallStore,
    sent: list[UUID],
) -> RelayVideoGenerationProvider:
    return RelayVideoGenerationProvider(
        config=config,
        calls=store,
        sender=lambda call_id, identity: sent.append(call_id),
    )


@pytest.fixture
def identity() -> VerifiedIdentity:
    return VerifiedIdentity(1, uuid4(), uuid4())


@pytest.fixture
def store(identity: VerifiedIdentity) -> FakeCallStore:
    return FakeCallStore(identity, uuid4())


def test_start_task_registers_and_enqueues_once(
    config: VideoProviderConfig, store: FakeCallStore
) -> None:
    sent: list[UUID] = []
    provider = _provider(config, store, sent)
    key = str(uuid4())
    started = provider.start_task(key, VideoStartRequest(prompt="测试视频", duration_seconds=5))
    row = store.get(UUID(started.external_id))
    assert row is not None
    assert row.state == "PENDING"
    assert row.duration_seconds == 5
    assert row.resolution == "480p" and row.aspect_ratio == "portrait"
    assert row.unit_price_micro_minor == 50000000
    assert sent == [row.id]


def test_duplicate_request_key_is_idempotent(
    config: VideoProviderConfig, store: FakeCallStore
) -> None:
    sent: list[UUID] = []
    provider = _provider(config, store, sent)
    key = str(uuid4())
    request = VideoStartRequest(prompt="测试视频", duration_seconds=5)
    first = provider.start_task(key, request)
    second = provider.start_task(key, request)
    assert first.external_id == second.external_id
    assert sent == [UUID(first.external_id)]
    assert len(store.rows) == 1
    with pytest.raises(ApiError) as error:
        provider.start_task(key, VideoStartRequest(prompt="另一提示", duration_seconds=5))
    assert error.value.code == ErrorCode.IDEMPOTENCY_MISMATCH


@pytest.mark.parametrize(
    "resolution,duration,code",
    [("1080p", 11, ErrorCode.INVALID_INPUT), ("480p", 16, ErrorCode.INVALID_INPUT)],
)
def test_start_task_rejects_unbillable_combinations(
    config: VideoProviderConfig,
    store: FakeCallStore,
    resolution: str,
    duration: int,
    code: str,
) -> None:
    strict = replace(config, resolution=resolution)
    provider = _provider(strict, store, [])
    with pytest.raises(ApiError) as error:
        provider.start_task(
            str(uuid4()), VideoStartRequest(prompt="测试视频", duration_seconds=duration)
        )
    assert error.value.code == code
    assert store.rows == {}


def test_start_task_requires_duration(config: VideoProviderConfig, store: FakeCallStore) -> None:
    provider = _provider(config, store, [])
    with pytest.raises(ApiError) as error:
        provider.start_task(str(uuid4()), VideoStartRequest(prompt="测试视频"))
    assert error.value.code == ErrorCode.INVALID_INPUT


def test_provider_rejects_unlisted_configuration(store: FakeCallStore) -> None:
    base = load_video_provider_config(CONFIG_ENV)
    for bad in (
        replace(base, resolution="720p"),
        replace(base, aspect_ratio="square"),
    ):
        with pytest.raises(ValueError):
            RelayVideoGenerationProvider(config=bad, calls=store)


def test_poll_and_fetch_only_read_local_record(store: FakeCallStore) -> None:
    config = load_video_provider_config(CONFIG_ENV)
    provider = _provider(config, store, [])
    started = provider.start_task(
        str(uuid4()), VideoStartRequest(prompt="测试视频", duration_seconds=2)
    )
    call_id = UUID(started.external_id)
    assert provider.poll_task(str(call_id)).state == "RUNNING"
    with pytest.raises(ApiError) as error:
        provider.fetch_result(str(call_id))
    assert error.value.code == ErrorCode.RESULT_UNKNOWN
    store.succeed(
        call_id,
        provider_task_id="9-3891694e-0001",
        blob_key=f"tenants/1/shops/{store.shop}/videos/{call_id}",
        mime="video/mp4",
        size_bytes=123,
        sha256="d" * 64,
        measured_duration_seconds=2,
        settled_amount_micro_minor=100000000,
    )
    assert provider.poll_task(str(call_id)).state == "SUCCEEDED"
    result = provider.fetch_result(str(call_id))
    assert result.duration_seconds == 2 and result.billing.amount_minor == 100
    assert result.billing.state == "KNOWN" and result.billing.proof_hash


def test_per_call_billing_is_fixed(store: FakeCallStore) -> None:
    values = dict(CONFIG_ENV)
    values.pop("YULIANG_VIDEOGEN_PRIMARY_PRICE_PER_SECOND")
    values["YULIANG_VIDEOGEN_PRIMARY_PRICE_PER_CALL"] = "2"
    config = load_video_provider_config(values)
    provider = _provider(config, store, [])
    started = provider.start_task(
        str(uuid4()), VideoStartRequest(prompt="测试视频", duration_seconds=2)
    )
    call_id = UUID(started.external_id)
    store.claim(call_id)
    store.succeed(
        call_id,
        provider_task_id="9-3891694e-0001",
        blob_key=f"tenants/1/shops/{store.shop}/videos/{call_id}",
        mime="video/mp4",
        size_bytes=123,
        sha256="d" * 64,
        measured_duration_seconds=2,
        settled_amount_micro_minor=200000000,
    )
    assert provider.fetch_result(str(call_id)).billing.amount_minor == 200


def test_reconcile_marks_running_as_unknown(store: FakeCallStore) -> None:
    config = load_video_provider_config(CONFIG_ENV)
    provider = _provider(config, store, [])
    started = provider.start_task(
        str(uuid4()), VideoStartRequest(prompt="测试视频", duration_seconds=2)
    )
    call_id = UUID(started.external_id)
    store.claim(call_id)
    assert reconcile_abandoned_calls(store, 1800) == 1
    row = store.get(call_id)
    assert row is not None and row.state == "FAILED" and row.error_code == "OUTCOME_UNKNOWN"


def _receipt_tests() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("http-400", {"receipt_status": 400}),
        ("http-401", {"receipt_status": 401}),
        ("status-failed", {"receipt": {"id": "9-1", "status": "failed", "results": []}}),
        ("empty-results", {"receipt": {"id": "9-1", "status": "succeeded", "results": []}}),
        (
            "http-url",
            {
                "receipt": {
                    "id": "9-1",
                    "status": "succeeded",
                    "results": [{"url": "http://file.example.com/a.mp4"}],
                }
            },
        ),
    ]


@pytest.mark.parametrize("name,options", _receipt_tests())
def test_request_artifact_rejects_bad_receipts(
    transport_config: VideoTransportConfig, name: str, options: dict[str, Any]
) -> None:
    with pytest.raises(RelayVideoError) as error:
        request_artifact(
            config=transport_config,
            prompt="测试视频",
            duration_seconds=2,
            resolution="480p",
            aspect_ratio="portrait",
            transport=_transport(**options),
        )
    expected = {
        "http-400": "PROVIDER_REJECTED",
        "http-401": "PROVIDER_REJECTED",
        "status-failed": "PROVIDER_FAILED",
        "empty-results": "RESPONSE_INVALID",
        "http-url": "RESPONSE_INVALID",
    }[name]
    assert error.value.code == expected


def test_request_artifact_success_measures_mp4_duration(
    transport_config: VideoTransportConfig,
) -> None:
    seen: list[httpx.Request] = []
    artifact = request_artifact(
        config=transport_config,
        prompt="测试视频",
        duration_seconds=5,
        resolution="480p",
        aspect_ratio="portrait",
        transport=_transport(seen=seen),
    )
    assert artifact.provider_task_id == "9-3891694e-0001"
    assert artifact.measured_duration_seconds == 2
    assert len(artifact.data) == len(_mp4())
    body = json.loads(seen[0].content)
    assert body == {
        "model": "minimax-h3",
        "prompt": "测试视频",
        "aspectRatio": "portrait",
        "resolution": "480p",
        "duration": 5,
    }
    assert seen[0].headers["authorization"] == "Bearer test-only-key"


@pytest.mark.parametrize(
    "options,code",
    [
        ({"mime": "text/plain"}, "ARTIFACT_MIME_INVALID"),
        (
            {
                "receipt": {
                    "id": "9-1",
                    "status": "succeeded",
                    "results": [
                        {
                            "url": "https://file.example.com/a.mp4",
                            "sha256": "e" * 64,
                        }
                    ],
                }
            },
            "ARTIFACT_CHECKSUM_MISMATCH",
        ),
        ({"data": b"\x00\x00\x00\x10mdat\x00\x00\x00\x00"}, "MP4_DURATION_UNREADABLE"),
    ],
)
def test_request_artifact_rejects_bad_downloads(
    transport_config: VideoTransportConfig, options: dict[str, Any], code: str
) -> None:
    with pytest.raises(RelayVideoError) as error:
        request_artifact(
            config=transport_config,
            prompt="测试视频",
            duration_seconds=2,
            resolution="480p",
            aspect_ratio="portrait",
            transport=_transport(**options),
        )
    assert error.value.code == code


def test_request_artifact_timeout_is_outcome_unknown(
    transport_config: VideoTransportConfig,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(RelayVideoError) as error:
        request_artifact(
            config=transport_config,
            prompt="测试视频",
            duration_seconds=2,
            resolution="480p",
            aspect_ratio="portrait",
            transport=httpx.MockTransport(handler),
        )
    assert error.value.code == "OUTCOME_UNKNOWN"


def test_execute_success_stores_artifact_and_freezes_amount(
    config: VideoProviderConfig, transport_config: VideoTransportConfig, store: FakeCallStore
) -> None:
    provider = _provider(config, store, [])
    started = provider.start_task(
        str(uuid4()), VideoStartRequest(prompt="测试视频", duration_seconds=2)
    )
    call_id = UUID(started.external_id)
    blob = FakeBlob()
    outcome = execute_relay_video_call(
        store=store,
        blob=blob,
        call_id=call_id,
        config=transport_config,
        transport=_transport(),
    )
    row = store.get(call_id)
    assert row is not None and row.state == "SUCCEEDED"
    assert row.measured_duration_seconds == 2
    assert row.settled_amount_micro_minor == 100000000
    assert row.sha256 == hashlib.sha256(_mp4()).hexdigest()
    assert row.blob_key == f"tenants/1/shops/{store.shop}/videos/{call_id}"
    assert blob.puts == [(row.blob_key, len(_mp4()), "video/mp4", call_id)]
    assert outcome["state"] == "SUCCEEDED"


def test_execute_failure_marks_call_failed(
    config: VideoProviderConfig, transport_config: VideoTransportConfig, store: FakeCallStore
) -> None:
    provider = _provider(config, store, [])
    started = provider.start_task(
        str(uuid4()), VideoStartRequest(prompt="测试视频", duration_seconds=2)
    )
    call_id = UUID(started.external_id)
    blob = FakeBlob()
    outcome = execute_relay_video_call(
        store=store,
        blob=blob,
        call_id=call_id,
        config=transport_config,
        transport=_transport(receipt_status=401),
    )
    row = store.get(call_id)
    assert row is not None and row.state == "FAILED"
    assert row.error_code == "PROVIDER_REJECTED"
    assert blob.puts == []
    assert outcome["state"] == "FAILED"


def test_execute_skips_when_already_claimed(
    config: VideoProviderConfig, transport_config: VideoTransportConfig, store: FakeCallStore
) -> None:
    provider = _provider(config, store, [])
    started = provider.start_task(
        str(uuid4()), VideoStartRequest(prompt="测试视频", duration_seconds=2)
    )
    call_id = UUID(started.external_id)
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(500)

    store.claim(call_id)
    outcome = execute_relay_video_call(
        store=store,
        blob=FakeBlob(),
        call_id=call_id,
        config=transport_config,
        transport=httpx.MockTransport(handler),
    )
    assert outcome == {"callId": str(call_id), "state": "SKIPPED"}
    assert called == []


@pytest.mark.parametrize(
    "version,timescale,duration,expected",
    [(0, 1000, 2000, 2), (0, 1000, 2500, 3), (1, 1000, 2000, 2)],
)
def test_mp4_duration_reads_mvhd(
    version: int, timescale: int, duration: int, expected: int
) -> None:
    assert mp4_duration_seconds(_mp4(timescale, duration, version=version)) == expected


@pytest.mark.parametrize("data", [b"", b"\x00\x00\x00\x00", _box(b"ftyp", b"isom")])
def test_mp4_duration_rejects_missing_moov(data: bytes) -> None:
    with pytest.raises(Mp4Error):
        mp4_duration_seconds(data)


def test_mp4_duration_rejects_zero_timescale() -> None:
    with pytest.raises(Mp4Error):
        mp4_duration_seconds(_mp4(0, 1000))
