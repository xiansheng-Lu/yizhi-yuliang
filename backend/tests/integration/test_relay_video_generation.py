"""真实PG/MinIO下的中转站视频闭环：Web登记、Worker执行、轮询取回与账本结算。

HTTP由MockTransport替代，不向任何真实供应商发起请求，也不产生真实费用。
"""

import hashlib
import struct
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.infrastructure.blob import BlobStore, private_blob_logs
from yuliang.modules.content.generation_models import GenerationCreate, GenerationInput
from yuliang.modules.content.relay_video_calls import PostgresVideoCallStore
from yuliang.modules.content.relay_video_generation_provider import (
    RelayVideoGenerationProvider,
    reconcile_abandoned_calls,
)
from yuliang.modules.content.relay_video_generation_worker import (
    execute_relay_video_call,
)
from yuliang.modules.content.video_generation_config import VideoProviderConfig
from yuliang.modules.content.video_generation_models import VideoPrice
from yuliang.modules.content.video_generation_service import VideoGenerationService
from yuliang.platform.db import business_transaction

from .test_capability_evidence import capability as capability
from .test_cloud_documents import content_ready as content_ready
from .test_configuration_context import configuration_ready as configuration_ready
from .test_content_assets import assets_ready as assets_ready
from .test_content_uploads import blob as blob
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_shop_product_details import details as details
from .test_shop_products import products as products
from .test_video_generation import video_ready as video_ready

pytestmark = pytest.mark.integration

PRICE_MICRO_MINOR_PER_SECOND = 20000000


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + kind + payload


def _mp4(timescale: int = 1000, duration: int = 2000) -> bytes:
    mvhd = b"\x00\x00\x00\x00" + bytes(8) + struct.pack(">II", timescale, duration)
    return _box(b"ftyp", b"isom\x00\x00\x02\x00") + _box(b"moov", _box(b"mvhd", mvhd + bytes(80)))


def _transport(
    *, body: bytes | None = None, seen: list[httpx.Request] | None = None
) -> httpx.MockTransport:
    content = _mp4() if body is None else body

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "id": "9-3891694e-integration",
                    "status": "succeeded",
                    "results": [{"url": "https://file.example.com/file/result.mp4"}],
                    "progress": 100,
                },
            )
        return httpx.Response(200, content=content, headers={"Content-Type": "video/mp4"})

    return httpx.MockTransport(handler)


def _forbidden_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("已领取过的调用不得再次发起外部请求")

    return httpx.MockTransport(handler)


def _config() -> VideoProviderConfig:
    return VideoProviderConfig(
        endpoint="https://relay.example.com/v1/video",
        model="minimax-h3",
        key=SecretStr("test-only-key"),
        resolution="480p",
        aspect_ratio="portrait",
        price=VideoPrice(mode="PER_SECOND", micro_minor_per_unit=PRICE_MICRO_MINOR_PER_SECOND),
    )


def _input(d: dict[str, Any], *, request_key: UUID, duration: int) -> GenerationInput:
    source = d["videoInput"]
    return GenerationInput.model_validate(
        dict(
            requestKey=str(request_key),
            runId=source.runId,
            stepCode=source.stepCode,
            expectedRunVersion=source.expectedRunVersion,
            expectedAttempt=source.expectedAttempt,
            documentId=source.documentId,
            documentVersion=source.documentVersion,
            kind="VIDEO",
            provider="relay-video",
            priceKey="synthetic.relay.video.second",
            prompt="中转站视频技术验证，不代表真实采纳",
            durationSeconds=duration,
        )
    )


def _accept(service: VideoGenerationService, value: GenerationInput) -> GenerationCreate:
    quote = service.quote(value)
    accepted = GenerationCreate(
        input=value,
        quoteId=quote["quote_id"],
        quoteHash=quote["quote_hash"],
        maximumMinor=quote["max_cost_minor"],
        currency=quote["currency"],
    )
    service.confirm(accepted)
    service.create(accepted)
    return accepted


def _latest_job(d: dict[str, Any]) -> UUID:
    with business_transaction(d["engine"], d["identity"]) as session:
        return UUID(
            str(
                session.execute(
                    text("SELECT id FROM generation_jobs ORDER BY created_at DESC")
                ).scalar_one()
            )
        )


def _price_source(d: dict[str, Any]) -> None:
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        price = owner.execute(
            text("""INSERT INTO ai_price_versions(tenant_id,price_key,revision,kind,
            currency,unit,unit_scale,input_rate_micro_minor,output_rate_micro_minor,valid_from,
            valid_until,verification,proof_hash)
            VALUES(:tenant,'synthetic.relay.video.second',1,'GENERATION','CNY','SECOND',1,
            :rate,0,now()-interval '1 day',now()+interval '1 day','VERIFIED',:proof)
            RETURNING id"""),
            dict(tenant=d["tenant"], rate=PRICE_MICRO_MINOR_PER_SECOND, proof="b" * 64),
        ).scalar_one()
        owner.execute(
            text("""INSERT INTO generation_price_sources(id,tenant_id,provider,kind,
            price_id,unit_semantic,proof_ref,source_sha256,checked_at,expires_at)
            VALUES(:id,:tenant,'relay-video','VIDEO',:price,'ONE_REQUEST_SECOND',
            'synthetic-relay-video-price',:sha,now()-interval '1 minute',
            now()+interval '1 day')"""),
            dict(id=uuid4(), tenant=d["tenant"], price=price, sha="b" * 64),
        )


def test_relay_video_start_worker_poll_fetch(video_ready: dict[str, Any], blob: BlobStore) -> None:
    d = video_ready
    _price_source(d)
    config = _config()
    store = PostgresVideoCallStore(d["engine"], d["identity"])
    sent: list[UUID] = []
    provider = RelayVideoGenerationProvider(
        config=config, calls=store, sender=lambda call_id, identity: sent.append(call_id)
    )
    service = VideoGenerationService(d["docs"], provider)
    value = _input(d, request_key=uuid4(), duration=5)
    _accept(service, value)
    job = _latest_job(d)
    ack = service.dispatch(job)
    assert ack["status"] == "WAITING_PROVIDER" and ack["submitState"] == "ACKNOWLEDGED"
    task = service.get_task(job)
    assert task["state"] == "RUNNING" and task["externalTaskId"]
    call_id = UUID(str(task["externalTaskId"]))
    assert sent == [call_id]
    pending = store.get(call_id)
    assert pending is not None and pending.state == "PENDING"
    with business_transaction(d["engine"], d["identity"]) as session:
        reserved, settled = session.execute(
            text(
                "SELECT reserved_minor,settled_minor FROM ai_cost_budgets "
                "WHERE scenario='content_generation'"
            )
        ).one()
    assert (int(reserved), int(settled)) == (100, 0)

    outcome = execute_relay_video_call(
        store=store, blob=blob, call_id=call_id, config=config, transport=_transport()
    )
    assert outcome["state"] == "SUCCEEDED"
    stored = store.get(call_id)
    assert stored is not None and stored.state == "SUCCEEDED"
    assert stored.measured_duration_seconds == 2
    assert stored.size_bytes == len(_mp4())
    assert stored.sha256 == hashlib.sha256(_mp4()).hexdigest()
    assert stored.settled_amount_micro_minor == PRICE_MICRO_MINOR_PER_SECOND * 2
    assert stored.blob_key == f"tenants/{d['tenant']}/shops/{d['shop']}/videos/{call_id}"
    assert stored.blob_key is not None
    with private_blob_logs():
        response = blob.client.get_object(Bucket=blob.bucket, Key=stored.blob_key)
    assert response["Body"].read() == _mp4()
    assert response["ContentType"] == "video/mp4"

    done = service.query(job)
    assert done["billingState"] == "KNOWN" and done["actualCostMinor"] == 40
    settled_task = service.get_task(job)
    assert settled_task["state"] == "SUCCEEDED"
    assert settled_task["billedSeconds"] == 2 and settled_task["resultMime"] == "video/mp4"
    assert settled_task["resultDurationSeconds"] == 2
    with business_transaction(d["engine"], d["identity"]) as session:
        reserved, settled = session.execute(
            text(
                "SELECT reserved_minor,settled_minor FROM ai_cost_budgets "
                "WHERE scenario='content_generation'"
            )
        ).one()
    assert (int(reserved), int(settled)) == (0, 40)


def test_relay_video_reconcile_marks_unknown_without_rerun(
    video_ready: dict[str, Any], blob: BlobStore
) -> None:
    d = video_ready
    _price_source(d)
    config = _config()
    store = PostgresVideoCallStore(d["engine"], d["identity"])
    provider = RelayVideoGenerationProvider(
        config=config, calls=store, sender=lambda call_id, identity: None
    )
    service = VideoGenerationService(d["docs"], provider)
    _accept(service, _input(d, request_key=uuid4(), duration=2))
    job = _latest_job(d)
    service.dispatch(job)
    call_id = UUID(str(service.get_task(job)["externalTaskId"]))
    assert store.claim(call_id) is not None
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE relay_video_calls SET updated_at=clock_timestamp()-interval '1 hour' "
                "WHERE id=:id"
            ),
            dict(id=call_id),
        )
    assert reconcile_abandoned_calls(store, 1800) == 1
    reconciled = store.get(call_id)
    assert reconciled is not None and reconciled.state == "FAILED"
    assert reconciled.error_code == "OUTCOME_UNKNOWN"
    skipped = execute_relay_video_call(
        store=store,
        blob=blob,
        call_id=call_id,
        config=config,
        transport=_forbidden_transport(),
    )
    assert skipped == {"callId": str(call_id), "state": "SKIPPED"}
