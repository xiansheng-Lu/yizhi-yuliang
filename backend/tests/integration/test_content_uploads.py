"""真实PG/S3签名、对象字节、确认幂等、私有版本与权限；无平台/付费。"""

import hashlib
import io
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any, Literal
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.infrastructure.blob import BlobStore, private_blob_logs
from yuliang.main import create_app
from yuliang.modules.content.uploads import UploadService
from yuliang.modules.content.uploads_models import UploadCreate
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_cloud_documents import content_ready as content_ready
from .test_configuration_context import configuration_ready as configuration_ready
from .test_identity_database import database as database
from .test_infrastructure import local_url
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import challenge, sign_in
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_shop_product_details import details as details
from .test_shop_products import products as products

pytestmark = pytest.mark.integration


@pytest.fixture
def blob() -> Any:
    bucket = "yuliang-test-upload-" + uuid4().hex
    store = BlobStore(
        local_url("YULIANG_TEST_S3_URL"),
        bucket,
        SecretStr("yuliang-local"),
        SecretStr(os.environ["YULIANG_TEST_S3_SECRET"]),
        local=True,
    )
    client: Any = store.client
    with private_blob_logs():
        client.create_bucket(Bucket=bucket)
        client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    try:
        yield store
    finally:
        with private_blob_logs():
            versions = client.list_object_versions(Bucket=bucket)
            objects = [
                dict(Key=v["Key"], VersionId=v["VersionId"])
                for v in versions.get("Versions", []) + versions.get("DeleteMarkers", [])
            ]
            if objects:
                client.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
            client.delete_bucket(Bucket=bucket)
        store.close()


def png() -> bytes:
    target = io.BytesIO()
    Image.new("RGB", (2, 2), (12, 34, 56)).save(target, format="PNG")
    return target.getvalue()


def request(
    data: dict[str, Any],
    body: bytes,
    *,
    mime: str = "image/png",
    kind: Literal["image", "video", "audio", "text"] = "image",
) -> UploadCreate:
    return UploadCreate(
        clientRequestKey=str(uuid4()),
        name="实际图片.png",
        kind=kind,
        productId=data["product"],
        mime=mime,
        size=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def put(plan: dict[str, Any], body: bytes, headers: dict[str, str] | None = None) -> int:
    target = plan["upload"]
    with private_blob_logs():
        result = httpx.put(
            target["url"],
            content=body,
            headers=headers or target["headers"],
            trust_env=False,
            timeout=10,
        )
    return result.status_code


def service(d: dict[str, Any], blob: BlobStore) -> UploadService:
    return UploadService(d["engine"], d["identity"], d["shop"], blob)


@pytest.mark.asyncio
async def test_real_upload_content_confirmation_idempotency_and_version_pin(
    content_ready: dict[str, Any], blob: BlobStore
) -> None:
    d = content_ready
    r = service(d, blob)
    body = png()
    value = request(d, body)
    plan = r.create(value)
    identifier = UUID(plan["uploadId"])
    assert r.create(value)["uploadId"] == str(identifier)
    with pytest.raises(ApiError):
        r.create(value.model_copy(update=dict(name="different.png")))
    with pytest.raises(ApiError):
        r.complete(identifier)
    with business_transaction(d["engine"], d["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM content_assets")).scalar_one() == 0
    assert put(plan, body) == 200
    asset = r.complete(identifier)
    assert asset["id"] == str(identifier) and asset["duration"] == 0
    with patch.object(blob, "verify", side_effect=AssertionError("must not reverify")):
        assert r.complete(identifier) == asset
    with business_transaction(d["engine"], d["identity"]) as session:
        row = (
            session.execute(text("SELECT * FROM content_assets WHERE id=:id"), dict(id=identifier))
            .mappings()
            .one()
        )
        key, version = row["object_key"], row["object_version"]
        assert row["sha256"] == value.sha256
    client: Any = blob.client
    with private_blob_logs():
        client.put_object(
            Bucket=blob.bucket, Key=key, Body=b"new untrusted version", ContentType="text/plain"
        )
        old = client.get_object(Bucket=blob.bucket, Key=key, VersionId=version)
        assert old["Body"].read() == body
        old["Body"].close()
    assert r.get(identifier)["asset"]["id"] == str(identifier)


@pytest.mark.asyncio
async def test_signed_type_tamper_and_actual_fake_png_rejected(
    content_ready: dict[str, Any], blob: BlobStore
) -> None:
    d = content_ready
    r = service(d, blob)
    body = png()
    plan = r.create(request(d, body))
    headers = plan["upload"]["headers"] | {"Content-Type": "text/plain"}
    assert put(plan, body, headers) == 403
    assert put(plan, body) == 200
    invalid = b"not a real PNG"
    bad = r.create(request(d, invalid))
    assert put(bad, invalid) == 200
    with pytest.raises(ApiError) as error:
        r.complete(UUID(bad["uploadId"]))
    assert error.value.code.value == "INVALID_INPUT"
    with business_transaction(d["engine"], d["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM content_assets")).scalar_one() == 0


@pytest.mark.asyncio
async def test_completion_lease_scope_and_cross_tenant_key(
    content_ready: dict[str, Any], blob: BlobStore
) -> None:
    d = content_ready
    r = service(d, blob)
    body = png()
    plan = r.create(request(d, body))
    identifier = UUID(plan["uploadId"])
    assert put(plan, body) == 200
    wrong = UploadService(d["engine"], d["identity"], uuid4(), blob)
    with pytest.raises(ApiError):
        wrong.complete(identifier)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE content_uploads SET object_key=:key WHERE id=:id"),
            dict(id=identifier, key=blob.key(d["tenant"] + 1, d["shop"], identifier)),
        )
    with pytest.raises(ApiError):
        r.complete(identifier)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE content_uploads SET object_key=:key WHERE id=:id"),
            dict(id=identifier, key=blob.key(d["tenant"], d["shop"], identifier)),
        )
    entered, release = Event(), Event()
    verify = blob.verify

    def wait_verify(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(10)
        return verify(*args, **kwargs)

    with (
        patch.object(blob, "verify", side_effect=wait_verify),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        future = pool.submit(r.complete, identifier)
        assert entered.wait(5)
        try:
            with pytest.raises(ApiError) as error:
                r.complete(identifier)
            assert error.value.code.value == "LOCKED"
        finally:
            release.set()
        assert future.result(timeout=15)["id"] == str(identifier)


@pytest.mark.asyncio
async def test_private_download_expiry_and_utf8_actual_decode(
    content_ready: dict[str, Any], blob: BlobStore
) -> None:
    d = content_ready
    r = service(d, blob)
    body = "实际文本".encode()
    plan = r.create(request(d, body, mime="text/plain", kind="text"))
    assert put(plan, body) == 200
    identifier = UUID(plan["uploadId"])
    r.complete(identifier)
    with business_transaction(d["engine"], d["identity"]) as session:
        row = (
            session.execute(text("SELECT * FROM content_assets WHERE id=:id"), dict(id=identifier))
            .mappings()
            .one()
        )
    url = blob.download_url(row["object_key"], row["object_version"], expires=1)
    with private_blob_logs():
        assert httpx.get(url.split("?")[0], trust_env=False).status_code == 403
        assert httpx.get(url, trust_env=False).content == body
        time.sleep(2)
        assert httpx.get(url, trust_env=False).status_code == 403
    bad = r.create(request(d, b"\xff\x00", mime="text/plain", kind="text"))
    assert put(bad, b"\xff\x00") == 200
    with pytest.raises(ApiError):
        r.complete(UUID(bad["uploadId"]))


@pytest.mark.asyncio
async def test_real_http_lifecycle_upload_route_scope_csrf_and_disabled(
    content_ready: dict[str, Any], blob: BlobStore
) -> None:
    d = content_ready
    settings = d["settings"].model_copy(
        update=dict(
            blob_enabled=True,
            blob_endpoint_url=local_url("YULIANG_TEST_S3_URL"),
            blob_bucket=blob.bucket,
            blob_access_key=SecretStr("yuliang-local"),
            blob_secret_key=SecretStr(os.environ["YULIANG_TEST_S3_SECRET"]),
        )
    )
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        api = d | dict(client=client)
        sign_in(api)
        csrf = challenge(client)
        headers = {"X-CSRF-Token": csrf, "X-Shop-Id": str(d["shop"])}
        value = request(d, png())
        assert client.post("/v1/content/uploads", json=value.model_dump()).status_code == 403
        response = client.post("/v1/content/uploads", json=value.model_dump(), headers=headers)
        assert response.status_code == 200
        plan = response.json()["data"]
        assert put(plan, png()) == 200
        complete = client.post(
            "/v1/content/uploads/" + plan["uploadId"] + "/complete", headers=headers
        )
        assert complete.status_code == 200
        assert complete.json()["data"]["id"] == plan["uploadId"]
        bad = value.model_dump() | dict(object_key="evil")
        assert client.post("/v1/content/uploads", json=bad, headers=headers).status_code == 422


@pytest.mark.asyncio
async def test_actual_checksum_and_size_mismatch_do_not_create_assets(
    content_ready: dict[str, Any], blob: BlobStore
) -> None:
    d = content_ready
    r = service(d, blob)
    body = png()
    client: Any = blob.client
    for mode in ["checksum", "size"]:
        value = request(d, body)
        plan = r.create(value)
        identifier = UUID(plan["uploadId"])
        changed = bytes([body[0] ^ 1]) + body[1:] if mode == "checksum" else body + b"extra"
        with private_blob_logs():
            client.put_object(
                Bucket=blob.bucket,
                Key=blob.key(d["tenant"], d["shop"], identifier),
                Body=changed,
                ContentType=value.mime,
                Metadata={"upload-id": str(identifier)},
            )
        with pytest.raises(ApiError) as error:
            r.complete(identifier)
        assert error.value.code.value == "INVALID_INPUT"
        reason = "OBJECT_CHECKSUM_MISMATCH" if mode == "checksum" else "OBJECT_METADATA_MISMATCH"
        assert error.value.details[0]["reason"] == reason
    with business_transaction(d["engine"], d["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM content_assets")).scalar_one() == 0


@pytest.mark.asyncio
async def test_expired_plan_and_unversioned_bucket_never_confirm(
    content_ready: dict[str, Any], blob: BlobStore
) -> None:
    d = content_ready
    r = service(d, blob)
    body = png()
    plan = r.create(request(d, body))
    identifier = UUID(plan["uploadId"])
    assert put(plan, body) == 200
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE content_uploads SET expires_at=clock_timestamp()-interval '1 second' "
                "WHERE id=:id"
            ),
            dict(id=identifier),
        )
    with pytest.raises(ApiError) as error:
        r.complete(identifier)
    assert error.value.code.value == "RESOURCE_TERMINAL"
    client: Any = blob.client
    with private_blob_logs():
        client.put_bucket_versioning(
            Bucket=blob.bucket, VersioningConfiguration={"Status": "Suspended"}
        )
    with pytest.raises(ApiError):
        r.create(request(d, body))
    with business_transaction(d["engine"], d["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM content_assets")).scalar_one() == 0
