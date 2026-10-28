"""已确认素材团队范围、版本下载、统一文档引用与归档竞争，真实PG/MinIO。"""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.infrastructure.blob import BlobStore, private_blob_logs
from yuliang.main import create_app
from yuliang.modules.content.assets import AssetService
from yuliang.modules.content.models import Document
from yuliang.modules.content.service import ContentService
from yuliang.modules.content.uploads import UploadService
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_cloud_documents import content_ready as content_ready
from .test_cloud_documents import document
from .test_configuration_context import configuration_ready as configuration_ready
from .test_content_uploads import blob as blob
from .test_content_uploads import png, put, request
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
async def assets_ready(content_ready: dict[str, Any], blob: BlobStore) -> dict[str, Any]:
    d = content_ready
    upload = UploadService(d["engine"], d["identity"], d["shop"], blob)
    body = png()
    plan = upload.create(request(d, body))
    assert put(plan, body) == 200
    identifier = UUID(plan["uploadId"])
    upload.complete(identifier)
    return d | dict(
        blob=blob,
        asset=identifier,
        asset_body=body,
        assets=AssetService(d["engine"], d["identity"], d["shop"], blob),
        docs=ContentService(d["engine"], d["identity"], d["shop"], blob=blob),
    )


def graphic(d: dict[str, Any], asset: UUID) -> Document:
    v = document(d["product"], "graphics").model_dump()
    v["boards"] = [
        dict(
            id=str(uuid4()),
            name="画板",
            width=1080,
            height=1350,
            background="#ffffff",
            layers=[
                dict(
                    id=str(uuid4()),
                    type="image",
                    text="",
                    assetId=str(asset),
                    x=0,
                    y=0,
                    width=100,
                    height=100,
                    fontSize=12,
                    color="#000000",
                    visible=True,
                    locked=False,
                )
            ],
        )
    ]
    return Document.model_validate(v)


@pytest.mark.asyncio
async def test_team_read_does_not_inherit_private_upload_actor_and_no_unconfirmed_list(
    assets_ready: dict[str, Any],
) -> None:
    d = assets_ready
    blob = d["blob"]
    identifier = d["asset"]
    member = VerifiedIdentity(d["tenant"], d["approver_member"], d["approver"])
    other = AssetService(d["engine"], member, d["shop"], blob)
    assert [v["id"] for v in other.list()] == [str(identifier)]
    assert "object_key" not in other.get(identifier)
    pending = UploadService(d["engine"], d["identity"], d["shop"], blob).create(request(d, png()))
    assert pending["state"] == "PENDING"
    assert len(other.list()) == 1
    with pytest.raises(ApiError):
        UploadService(d["engine"], member, d["shop"], blob).get(identifier)
    wrong = AssetService(d["engine"], d["identity"], uuid4(), blob)
    with pytest.raises(ApiError):
        wrong.get(identifier)
    outsider = AssetService(
        d["engine"],
        VerifiedIdentity(d["tenant"] + 999999, d["approver_member"], d["approver"]),
        d["shop"],
        blob,
    )
    with pytest.raises(ApiError):
        outsider.get(identifier)


@pytest.mark.asyncio
async def test_download_is_exact_confirmed_version_and_missing_version_does_not_sign(
    assets_ready: dict[str, Any],
) -> None:
    d = assets_ready
    r = d["assets"]
    blob = d["blob"]
    identifier = d["asset"]
    client: Any = blob.client
    with business_transaction(d["engine"], d["identity"]) as s:
        row = (
            s.execute(text("SELECT * FROM content_assets WHERE id=:id"), dict(id=identifier))
            .mappings()
            .one()
        )
    with private_blob_logs():
        client.put_object(
            Bucket=blob.bucket, Key=row["object_key"], Body=b"NEW", ContentType="text/plain"
        )
    result = r.download(identifier)
    with private_blob_logs():
        assert httpx.get(result["url"], trust_env=False).content == d["asset_body"]
    with private_blob_logs():
        client.delete_object(
            Bucket=blob.bucket, Key=row["object_key"], VersionId=row["object_version"]
        )
    with patch.object(
        blob, "download_url", side_effect=AssertionError("must not sign missing file")
    ):
        with pytest.raises(ApiError) as error:
            r.download(identifier)
        assert error.value.code.value == "UPSTREAM_ERROR"
    v = graphic(d, identifier)
    with pytest.raises(ApiError):
        d["docs"].save(UUID(v.id), v)
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM content_documents WHERE id=:id"), dict(id=UUID(v.id))
            ).scalar_one()
            == 0
        )


@pytest.mark.asyncio
async def test_document_actual_asset_save_history_and_internal_paths(
    assets_ready: dict[str, Any],
) -> None:
    d = assets_ready
    v = graphic(d, d["asset"])
    identifier = UUID(v.id)
    assert d["docs"].save(identifier, v)["version"] == 1
    assert d["docs"].review(identifier, 1)["version"] == 2
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM content_document_asset_versions WHERE document_id=:id"),
                dict(id=identifier),
            ).scalar_one()
            == 2
        )
    v2 = Document.model_validate(d["docs"].get(identifier))
    v2.boards = []
    d["docs"].save(identifier, v2)
    with pytest.raises(ApiError) as error:
        d["assets"].archive(d["asset"])
    assert error.value.code.value == "LOCKED"
    # No store, missing attestation, or forged snapshot cannot bypass even private save entry.
    fresh = graphic(d, d["asset"])
    without = ContentService(d["engine"], d["identity"], d["shop"])
    with pytest.raises(ApiError):
        without.save(UUID(fresh.id), fresh)
    with business_transaction(d["engine"], d["identity"]) as s:
        with pytest.raises(ApiError):
            d["docs"]._save(s, UUID(fresh.id), fresh)
    proofs = d["assets"].prepare_document(fresh)
    proofs[d["asset"]] = replace(proofs[d["asset"]], storage_token="forged")
    with business_transaction(d["engine"], d["identity"]) as s:
        with pytest.raises(ApiError):
            d["docs"]._save(s, UUID(fresh.id), fresh, proofs)
    assert d["assets"].get(d["asset"])["id"] == str(d["asset"])


@pytest.mark.asyncio
async def test_wrong_product_kind_or_forged_id_never_partially_saves(
    assets_ready: dict[str, Any],
) -> None:
    d = assets_ready
    blob = d["blob"]
    upload = UploadService(d["engine"], d["identity"], d["shop"], blob)
    body = b"actual text"
    plan = upload.create(request(d, body, mime="text/plain", kind="text"))
    assert put(plan, body) == 200
    textid = UUID(plan["uploadId"])
    upload.complete(textid)
    for asset in [textid, uuid4()]:
        value = graphic(d, asset)
        with pytest.raises(ApiError):
            d["docs"].save(UUID(value.id), value)
        with business_transaction(d["engine"], d["identity"]) as s:
            assert (
                s.execute(
                    text("SELECT count(*) FROM content_documents WHERE id=:id"),
                    dict(id=UUID(value.id)),
                ).scalar_one()
                == 0
            )
    other = uuid4()
    with d["maintenance"].begin() as c:
        c.execute(text("SET LOCAL ROLE app_owner"))
        c.execute(
            text(
                "INSERT INTO commerce_products(id,tenant_id,shop_id,account_id,native_id) "
                "VALUES(:id,:tenant,:shop,:account,'second')"
            ),
            dict(id=other, tenant=d["tenant"], shop=d["shop"], account=d["detailctx"].account_id),
        )
    value = graphic(d, d["asset"])
    value.productId = str(other)
    with pytest.raises(ApiError):
        d["docs"].save(UUID(value.id), value)


@pytest.mark.asyncio
async def test_archive_and_reference_race_is_serialized_and_not_physical_delete(
    assets_ready: dict[str, Any],
) -> None:
    d = assets_ready
    r = d["assets"]
    identifier = d["asset"]
    proof = r.prepare_reference(identifier)
    ref = uuid4()

    def act(action: str) -> str:
        try:
            if action == "archive":
                r.archive(identifier)
            else:
                with business_transaction(d["engine"], d["identity"]) as s:
                    r.reference_in_transaction(s, identifier, "GENERATION", ref, proof=proof)
            return "OK"
        except ApiError:
            return "BLOCKED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(act, ["archive", "reference"])) == ["BLOCKED", "OK"]
    with business_transaction(d["engine"], d["identity"]) as s:
        row = (
            s.execute(text("SELECT * FROM content_assets WHERE id=:id"), dict(id=identifier))
            .mappings()
            .one()
        )
        assert not s.execute(
            text(
                "SELECT 1 FROM content_assets a JOIN content_asset_references r "
                "ON (r.tenant_id,r.asset_id)=(a.tenant_id,a.id) "
                "WHERE a.id=:id AND a.deleted_at IS NOT NULL AND r.active"
            ),
            dict(id=identifier),
        ).first()
    d["blob"].check_version(
        row["object_key"],
        row["object_version"],
        row["size_bytes"],
        row["mime"],
        row["sha256"],
        identifier,
    )
    if row["deleted_at"] is not None:
        assert r.list() == []
    else:
        with business_transaction(d["engine"], d["identity"]) as s:
            r.reference_in_transaction(s, identifier, "GENERATION", ref, active=False)
        r.archive(identifier)
        r.archive(identifier)
        assert r.list() == []
    with pytest.raises(ApiError) as error:
        UploadService(d["engine"], d["identity"], d["shop"], d["blob"]).complete(identifier)
    assert error.value.code.value == "RESOURCE_TERMINAL"


@pytest.mark.asyncio
async def test_real_http_team_download_document_reference_and_archive(
    assets_ready: dict[str, Any],
) -> None:
    d = assets_ready
    blob = d["blob"]
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
        result = client.get("/v1/content/assets", headers=headers)
        assert result.status_code == 200
        assert result.json()["data"][0]["id"] == str(d["asset"])
        link = client.get("/v1/content/assets/" + str(d["asset"]) + "/download", headers=headers)
        assert link.status_code == 200
        with private_blob_logs():
            assert httpx.get(link.json()["data"]["url"], trust_env=False).content == d["asset_body"]
        assert (
            client.get(
                "/v1/content/assets/" + str(d["asset"]) + "/download?versionId=forged",
                headers=headers,
            ).status_code
            == 422
        )
        value = graphic(d, d["asset"])
        save = client.put(
            "/v1/content/documents/" + value.id, json=value.model_dump(), headers=headers
        )
        assert save.status_code == 200, save.status_code
        assert (
            client.delete("/v1/content/assets/" + str(d["asset"]), headers=headers).status_code
            == 423
        )


@pytest.mark.asyncio
async def test_runtime_sql_cannot_archive_history_or_mutate_history_references(
    assets_ready: dict[str, Any],
) -> None:
    from sqlalchemy.exc import DBAPIError

    d = assets_ready
    value = graphic(d, d["asset"])
    d["docs"].save(UUID(value.id), value)
    with business_transaction(d["engine"], d["identity"]) as s:
        with pytest.raises(DBAPIError):
            s.execute(
                text("UPDATE content_assets SET deleted_at=clock_timestamp() WHERE id=:id"),
                dict(id=d["asset"]),
            )
    for sql in [
        "DELETE FROM content_document_asset_versions WHERE asset_id=:id",
        "UPDATE content_document_asset_versions SET revision=revision+1 WHERE asset_id=:id",
    ]:
        with business_transaction(d["engine"], d["identity"]) as s:
            with pytest.raises(DBAPIError):
                s.execute(text(sql), dict(id=d["asset"]))


@pytest.mark.asyncio
async def test_direct_sql_reference_archive_competition_preserves_invariant(
    assets_ready: dict[str, Any],
) -> None:
    from threading import Barrier

    from sqlalchemy.exc import DBAPIError

    d = assets_ready
    barrier = Barrier(2)
    identifier = d["asset"]
    ref = uuid4()

    def action(kind: str) -> str:
        try:
            with business_transaction(d["engine"], d["identity"]) as s:
                barrier.wait(timeout=5)
                if kind == "archive":
                    s.execute(
                        text("UPDATE content_assets SET deleted_at=clock_timestamp() WHERE id=:id"),
                        dict(id=identifier),
                    )
                else:
                    s.execute(
                        text(
                            "INSERT INTO "
                            "content_asset_references(tenant_id,asset_id,kind,reference_id,active)"
                            " VALUES(:tenant,:id,'SOP',:ref,true)"
                        ),
                        dict(tenant=d["tenant"], id=identifier, ref=ref),
                    )
            return "OK"
        except DBAPIError:
            return "BLOCKED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(action, ["archive", "reference"])) == ["BLOCKED", "OK"]
    with business_transaction(d["engine"], d["identity"]) as s:
        assert not s.execute(
            text(
                "SELECT 1 FROM content_assets a JOIN content_asset_references r ON"
                " (r.tenant_id,r.asset_id)=(a.tenant_id,a.id) WHERE a.id=:id AND "
                "a.deleted_at IS NOT NULL AND r.active"
            ),
            dict(id=identifier),
        ).first()


@pytest.mark.asyncio
async def test_removed_product_is_rejected_for_new_document_and_sql_reference(
    assets_ready: dict[str, Any],
) -> None:
    from sqlalchemy.exc import DBAPIError

    d = assets_ready
    with d["maintenance"].begin() as c:
        c.execute(text("SET LOCAL ROLE app_owner"))
        c.execute(
            text("UPDATE commerce_products SET deleted_at=clock_timestamp() WHERE id=:id"),
            dict(id=UUID(d["product"])),
        )
    plain = document(d["product"])
    with pytest.raises(ApiError):
        d["docs"].save(UUID(plain.id), plain)
    with pytest.raises(ApiError):
        d["assets"].prepare_reference(d["asset"])
    with business_transaction(d["engine"], d["identity"]) as s:
        with pytest.raises(DBAPIError):
            s.execute(
                text(
                    "INSERT INTO "
                    "content_asset_references(tenant_id,asset_id,kind,reference_id,active)"
                    " VALUES(:tenant,:id,'DELIVERY',:ref,true)"
                ),
                dict(tenant=d["tenant"], id=d["asset"], ref=uuid4()),
            )
