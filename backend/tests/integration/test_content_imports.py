"""真实PG/MinIO本地稿元数据导入；浏览器IndexedDB/F09未接。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from yuliang.modules.content.assets import AssetService
from yuliang.modules.content.imports_models import ImportBundle
from yuliang.modules.content.imports_service import ContentImports
from yuliang.modules.content.service import ContentService
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_capability_evidence import capability as capability
from .test_cloud_documents import content_ready as content_ready
from .test_cloud_documents import document
from .test_configuration_context import configuration_ready as configuration_ready
from .test_content_assets import assets_ready as assets_ready
from .test_content_assets import graphic
from .test_content_uploads import blob as blob
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_shop_product_details import details as details
from .test_shop_products import products as products

pytestmark = pytest.mark.integration


@pytest.fixture
async def imports_ready(assets_ready: dict[str, Any]) -> dict[str, Any]:
    d = assets_ready
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        native = owner.execute(
            text("SELECT native_id FROM commerce_products WHERE id=:id"),
            dict(id=UUID(d["product"])),
        ).scalar_one()
        canonical = owner.execute(
            text("""INSERT INTO products(tenant_id,shop_id,native_id,name)
            VALUES(:tenant,:shop,:native,'合成商品') ON CONFLICT(tenant_id,shop_id,native_id)
            DO UPDATE SET name=products.name RETURNING id"""),
            dict(tenant=d["tenant"], shop=d["shop"], native=native),
        ).scalar_one()
    local_asset = str(uuid4())
    doc = graphic(d, UUID(local_asset)).model_dump()
    doc.update(productId=str(canonical), version=27)
    payload = dict(
        importKey="test-import",
        owner=dict(
            mode="API",
            tenantId=str(d["tenant"]),
            userId=str(d["identity"].user_id),
            shopId=str(d["shop"]),
        ),
        state=dict(
            schema=1,
            revision=123,
            documents=[doc],
            assets=[
                dict(
                    id=local_asset,
                    name="本地元数据",
                    kind="image",
                    productId=str(canonical),
                    documentId=doc["id"],
                    mime="not-authority",
                    size=1,
                    duration=999,
                    createdAt="2026-10-28T00:00:00Z",
                )
            ],
        ),
        productMap={str(canonical): d["product"]},
        assetMap={local_asset: str(d["asset"])},
    )
    return d | dict(
        imports=ContentImports(d["docs"]),
        bundle=ImportBundle.model_validate(payload),
        local=doc["id"],
        canonical=canonical,
    )


def counts(d: dict[str, Any]) -> tuple[int, ...]:
    with business_transaction(d["engine"], d["identity"]) as s:
        return tuple(
            s.execute(
                text("SELECT count(*) FROM " + t)  # noqa: S608 -- fixed internal table tuple only
            ).scalar_one()
            for t in (
                "content_documents",
                "content_document_asset_versions",
                "content_imports",
                "content_import_entries",
            )
        )


async def test_preview_readonly_real_mapping_and_atomic_stable_resume(
    imports_ready: dict[str, Any],
) -> None:
    d = imports_ready
    service, bundle = d["imports"], d["bundle"]
    before = counts(d)
    preview = service.preview(bundle)
    assert preview["writes"] == 0 and counts(d) == before
    item = preview["items"][0]
    assert item["status"] == "READY" and item["document"]["productId"] == d["product"]
    assert item["document"]["version"] == 1
    assert service.progress(bundle)["pending"] == [d["local"]]
    first = service.apply(bundle, d["local"])
    assert first["version"] == 1 and str(d["asset"]) in first["assetVersions"]
    assert service.apply(bundle, d["local"]) == first
    assert counts(d) == (1, 1, 1, 1)
    assert service.progress(bundle)["pending"] == []
    assert service.preview(bundle)["items"][0]["status"] == "ALREADY_IMPORTED"


async def test_failure_after_body_and_references_rolls_back_checkpoint(
    imports_ready: dict[str, Any],
) -> None:
    d = imports_ready
    original = ContentService._save

    def fail(self: ContentService, *args: Any, **kwargs: Any) -> Any:
        original(self, *args, **kwargs)
        raise RuntimeError("synthetic-after-save")

    with patch.object(ContentService, "_save", fail), pytest.raises(RuntimeError):
        d["imports"].apply(d["bundle"], d["local"])
    assert counts(d) == (0, 0, 0, 0)
    assert d["imports"].progress(d["bundle"])["pending"] == [d["local"]]
    assert d["imports"].apply(d["bundle"], d["local"])["version"] == 1


async def test_concurrent_resume_does_not_duplicate(imports_ready: dict[str, Any]) -> None:
    d = imports_ready
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: d["imports"].apply(d["bundle"], d["local"]), range(2)))
    assert receipts[0] == receipts[1] and counts(d) == (1, 1, 1, 1)


async def test_normal_editor_competes_before_import_create_only_check(
    imports_ready: dict[str, Any],
) -> None:
    d = imports_ready
    service, bundle = d["imports"], d["bundle"]
    target = bundle.cloud_id(d["tenant"], d["identity"].user_id, d["shop"], d["local"])
    reached, ordinary_done = Event(), Event()
    original_lock = ContentService._lock
    editor = ContentService(d["engine"], d["identity"], d["shop"], blob=d["blob"])
    ordinary = document(d["product"]).model_copy(
        update=dict(id=str(target), body="ordinary keeps body")
    )

    def lock(self: ContentService, session: Any, identifier: UUID) -> None:
        if self is service.content and identifier == target:
            reached.set()
            assert ordinary_done.wait(10), "ordinary writer did not finish"
        original_lock(self, session, identifier)

    def normal_save() -> None:
        assert reached.wait(10), "import never reached shared document lock"
        try:
            editor.save(target, ordinary)
        finally:
            ordinary_done.set()

    with patch.object(ContentService, "_lock", lock), ThreadPoolExecutor(max_workers=2) as pool:
        importing = pool.submit(service.apply, bundle, d["local"])
        ordinary_write = pool.submit(normal_save)
        ordinary_write.result(timeout=20)
        with pytest.raises(ApiError) as refused:
            importing.result(timeout=20)
        assert refused.value.code == ErrorCode.VERSION_CONFLICT
    assert editor.get(target)["body"] == "ordinary keeps body"
    assert counts(d) == (1, 0, 0, 0)


async def test_completed_replay_ignores_offline_head_but_rechecks_permissions(
    imports_ready: dict[str, Any],
) -> None:
    d = imports_ready
    service, bundle = d["imports"], d["bundle"]
    first = service.apply(bundle, d["local"])
    offline = ApiError(ErrorCode.UPSTREAM_ERROR, "synthetic private HEAD offline")
    with patch.object(AssetService, "_check_blob", side_effect=offline) as head:
        assert service.apply(bundle, d["local"]) == first
        assert head.call_count == 0
        with d["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(
                text("DELETE FROM role_grants WHERE membership_id=:member"),
                dict(member=d["identity"].membership_id),
            )
        with pytest.raises(ApiError) as refused:
            service.apply(bundle, d["local"])
        assert refused.value.code == ErrorCode.FORBIDDEN
    assert counts(d) == (1, 1, 1, 1)


@pytest.mark.parametrize("change", ["owner", "product", "asset", "metadata_only"])
async def test_scope_and_unknown_file_rejected_no_partial_write(
    imports_ready: dict[str, Any], change: str
) -> None:
    d = imports_ready
    payload = d["bundle"].model_dump(by_alias=True)
    if change == "owner":
        payload["owner"]["tenantId"] = "9999999"
    elif change == "product":
        payload["productMap"][str(d["canonical"])] = str(uuid4())
    elif change == "asset":
        payload["assetMap"][next(iter(payload["assetMap"]))] = str(uuid4())
    else:
        payload["assetMap"] = {}
    bundle = ImportBundle.model_validate(payload)
    if change == "owner":
        with pytest.raises(ApiError):
            d["imports"].preview(bundle)
    else:
        assert d["imports"].preview(bundle)["items"][0]["status"] == "BLOCKED"
    with pytest.raises(ApiError):
        d["imports"].apply(bundle, d["local"])
    assert counts(d) == (0, 0, 0, 0)


async def test_key_manifest_changed_and_original_id_conflict_explicit_copy(
    imports_ready: dict[str, Any],
) -> None:
    d = imports_ready
    # 原本地ID已有云端文档必须明确COPY，不覆盖其正文。
    cloud = document(d["product"])
    cloud = cloud.model_copy(update=dict(id=d["local"]))
    d["docs"].save(UUID(cloud.id), cloud)
    assert d["imports"].preview(d["bundle"])["items"][0]["issues"] == [
        "CLOUD_ID_CONFLICT_REQUIRES_EXPLICIT_COPY"
    ]
    payload = d["bundle"].model_dump(by_alias=True) | dict(decisions={d["local"]: "COPY"})
    bundle = ImportBundle.model_validate(payload)
    receipt = d["imports"].apply(bundle, d["local"])
    assert receipt["cloudId"] != d["local"]
    payload["state"]["documents"][0]["body"] = "changed"
    with pytest.raises(ApiError):
        d["imports"].apply(ImportBundle.model_validate(payload), d["local"])
    assert counts(d) == (2, 1, 1, 1)


async def test_parent_before_child_resume_keeps_local_relationship(
    imports_ready: dict[str, Any],
) -> None:
    d = imports_ready
    payload = d["bundle"].model_dump(by_alias=True)
    parent = payload["state"]["documents"][0]
    child = dict(parent, id=str(uuid4()), parentId=parent["id"], kind="scripts")
    payload["state"]["documents"].append(child)
    bundle = ImportBundle.model_validate(payload)
    with pytest.raises(ApiError):
        d["imports"].apply(bundle, child["id"])
    assert counts(d) == (0, 0, 0, 0)
    first = d["imports"].apply(bundle, parent["id"])
    second = d["imports"].apply(bundle, child["id"])
    assert first["cloudId"] != second["cloudId"]
    saved = d["docs"].get(UUID(second["cloudId"]))
    assert saved["parentId"] == first["cloudId"]
    assert counts(d) == (2, 2, 1, 2)


async def test_untracked_target_cloud_id_never_overwritten(imports_ready: dict[str, Any]) -> None:
    d = imports_ready
    bundle = d["bundle"]
    target = bundle.cloud_id(d["tenant"], d["identity"].user_id, d["shop"], d["local"])
    old = document(d["product"]).model_copy(update=dict(id=str(target), body="keep existing"))
    d["docs"].save(target, old)
    assert "CLOUD_TARGET_ID_CONFLICT" in d["imports"].preview(bundle)["items"][0]["issues"]
    with pytest.raises(ApiError):
        d["imports"].apply(bundle, d["local"])
    assert d["docs"].get(target)["body"] == "keep existing"
    assert counts(d) == (1, 0, 0, 0)


async def test_real_http_preview_apply_progress_csrf_and_scope(
    imports_ready: dict[str, Any],
) -> None:
    import os

    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from yuliang.main import create_app

    from .test_infrastructure import local_url
    from .test_password_sessions import challenge, sign_in

    d = imports_ready
    settings = d["settings"].model_copy(
        update=dict(
            blob_enabled=True,
            blob_endpoint_url=local_url("YULIANG_TEST_S3_URL"),
            blob_bucket=d["blob"].bucket,
            blob_access_key=SecretStr("yuliang-local"),
            blob_secret_key=SecretStr(os.environ["YULIANG_TEST_S3_SECRET"]),
        )
    )
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        sign_in(d | dict(client=client))
        payload = d["bundle"].model_dump(by_alias=True)
        assert (
            client.post(
                "/v1/content/imports/preview", json=payload, headers={"X-Shop-Id": str(d["shop"])}
            ).status_code
            == 403
        )
        headers = {"X-CSRF-Token": challenge(client), "X-Shop-Id": str(d["shop"])}
        preview = client.post("/v1/content/imports/preview", json=payload, headers=headers)
        assert preview.status_code == 200, preview.text
        assert preview.json()["data"]["items"][0]["status"] == "READY"
        assert counts(d) == (0, 0, 0, 0)
        assert (
            client.post(
                "/v1/content/imports/preview?tenant_id=forged", json=payload, headers=headers
            ).status_code
            == 422
        )
        saved = client.post(
            "/v1/content/imports/apply/" + d["local"], json=payload, headers=headers
        )
        assert saved.status_code == 200, saved.text
        progress = client.post("/v1/content/imports/progress", json=payload, headers=headers)
        assert progress.status_code == 200 and progress.json()["data"]["pending"] == []
        assert counts(d) == (1, 1, 1, 1)
