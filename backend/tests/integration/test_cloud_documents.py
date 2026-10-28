"""真实PG与HTTP云文档，当前卡不声称已完成F09浏览器接线。"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from yuliang.modules.content.models import Document
from yuliang.modules.content.service import ContentService, DocumentConflict
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_configuration_context import configuration_ready as configuration_ready
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import challenge, sign_in
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_shop_product_details import details as details
from .test_shop_products import products as products

pytestmark = pytest.mark.integration


def document(product: str = "", kind: str = "planning") -> Document:
    return Document.model_validate(
        dict(
            id=str(uuid4()),
            kind=kind,
            title="测试",
            productId=product,
            parentId=None,
            version=1,
            updatedAt="2026-10-28T00:00:00Z",
            status="draft",
            brief=dict(
                audience="", objective="", sellingPoints="", channel="视频号", requirements=""
            ),
            body="正文",
            scenes=[],
            boards=[],
            nodes=[],
            edges=[],
            clips=[],
            settings=dict(ratio="9:16", duration=15, resolution="1080P", skill="口播精剪"),
            prompt="",
        )
    )


@pytest.fixture
async def content_ready(details: dict[str, Any]) -> dict[str, Any]:
    d = details
    await d["detailreader"].sync(d["detailctx"], uuid4(), "1")
    product = d["detailreader"].view(d["detailctx"], "1")["internalId"]
    return d | dict(content=ContentService(d["engine"], d["identity"], d["shop"]), product=product)


@pytest.mark.asyncio
async def test_cloud_doc_versions_conflict_concurrency_and_history(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    r = d["content"]
    doc = document(d["product"])
    id = UUID(doc.id)
    assert r.save(id, doc)["version"] == 1

    def save(body: str) -> str:
        try:
            r.save(id, doc.model_copy(update=dict(body=body)))
            return "OK"
        except DocumentConflict as e:
            assert e.current["version"] == 2
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, ["一", "二"])) == ["CONFLICT", "OK"]
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM content_document_versions WHERE document_id=:id"),
                dict(id=id),
            ).scalar_one()
            == 2
        )
    with pytest.raises(ApiError):
        r.save(id, doc.model_copy(update=dict(productId=str(uuid4()), version=2)))
    assert r.get(id)["version"] == 2


@pytest.mark.asyncio
async def test_flow_planning_lists_and_active_reference_delete_guard(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    r = d["content"]
    doc = document(d["product"])
    id = UUID(doc.id)
    r.save(id, doc)
    child = r.flow(id, "scripts")
    assert child["parentId"] == str(id) and child["version"] == 1
    assert len(r.list("planning")) == 2
    video = r.flow(id, "videos")
    assert len(video["nodes"]) == 3 and video["edges"] == []
    ref = uuid4()
    r.reference(id, ref, "GENERATION")
    with pytest.raises(ApiError):
        r.delete(id, 1)
    r.reference(id, ref, "GENERATION", False)
    r.delete(id, 1)
    with pytest.raises(ApiError):
        r.get(id)
    assert r.get(UUID(child["id"]))["body"] == "正文"


@pytest.mark.asyncio
async def test_document_explicit_shop_and_parent_product_scope(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    r = d["content"]
    doc = document(d["product"])
    id = UUID(doc.id)
    r.save(id, doc)
    other = ContentService(d["engine"], d["identity"], uuid4())
    with pytest.raises(ApiError):
        other.get(id)
    with pytest.raises(ApiError):
        other.save(UUID(str(uuid4())), document(d["product"]))
    child = document("")
    child.parentId = doc.id
    with pytest.raises(ApiError):
        r.save(UUID(child.id), child)


@pytest.mark.asyncio
async def test_http_csrf_shop_header_and_conflict_current(content_ready: dict[str, Any]) -> None:
    d = content_ready
    d["client"].cookies.clear()
    sign_in(d)
    client = d["client"]
    doc = document(d["product"])
    url = "/v1/content/documents/" + doc.id
    csrf = challenge(client)
    headers = {"X-CSRF-Token": csrf, "X-Shop-Id": str(d["shop"])}
    assert client.put(url, json=doc.model_dump()).status_code == 403
    assert client.put(url, json=doc.model_dump(), headers={"X-CSRF-Token": csrf}).status_code == 422
    first = client.put(url, json=doc.model_dump(), headers=headers)
    assert first.status_code == 200, first.text
    assert client.put(url, json=doc.model_dump(), headers=headers).status_code == 200
    conflict = client.put(url, json=doc.model_dump(), headers=headers)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["current"]["version"] == 2
    bad = doc.model_dump()
    bad["revision"] = 999
    assert client.put(url, json=bad, headers=headers).status_code == 422


@pytest.mark.asyncio
async def test_cross_tenant_reference_race_and_history_immutable(
    content_ready: dict[str, Any],
) -> None:
    from sqlalchemy.exc import DBAPIError

    from yuliang.platform.db import VerifiedIdentity

    d = content_ready
    r = d["content"]
    doc = document(d["product"])
    identifier = UUID(doc.id)
    r.save(identifier, doc)
    outsider = ContentService(
        d["engine"],
        VerifiedIdentity(d["tenant"] + 999999, d["identity"].membership_id, d["identity"].user_id),
        d["shop"],
    )
    with pytest.raises(ApiError):
        outsider.get(identifier)
    with business_transaction(d["engine"], outsider.identity) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM content_documents WHERE id=:id"), dict(id=identifier)
            ).scalar_one()
            == 0
        )
    with business_transaction(d["engine"], d["identity"]) as s:
        with pytest.raises(DBAPIError):
            s.execute(
                text("UPDATE content_document_versions SET payload='{}' WHERE document_id=:id"),
                dict(id=identifier),
            )
    ref = uuid4()

    def action(kind: str) -> str:
        try:
            if kind == "reference":
                r.reference(identifier, ref, "RUN")
            else:
                r.delete(identifier, 1)
            return "OK"
        except ApiError:
            return "BLOCKED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(action, ["reference", "delete"])) == ["BLOCKED", "OK"]
    with business_transaction(d["engine"], d["identity"]) as s:
        assert not s.execute(
            text(
                "SELECT 1 FROM content_documents d JOIN content_document_references r "
                "ON (r.tenant_id,r.document_id)=(d.tenant_id,d.id) "
                "WHERE d.id=:id AND d.deleted_at IS NOT NULL AND r.active"
            ),
            dict(id=identifier),
        ).first()
