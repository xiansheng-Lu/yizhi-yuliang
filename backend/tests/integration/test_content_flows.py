"""真实PG原子派生、当前范围/版本与原键恢复；无生成/平台调用。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.content.flows import FlowCreate, FlowService, derive_document
from yuliang.modules.content.models import Document
from yuliang.modules.content.service import ContentService, DocumentConflict
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_cloud_documents import (  # noqa: F401 -- pytest共享夹具
    capability,
    configuration_ready,
    database,
    document,
    governance,
    login_api,
    oauth_ready,
    products,
)
from .test_cloud_documents import details as details
from .test_password_sessions import challenge
from .test_read_route_sources import legacy as legacy

pytestmark = pytest.mark.integration


@pytest.fixture
def parent(details: dict[str, Any]) -> dict[str, Any]:
    d = details
    content = ContentService(d["engine"], d["identity"], d["shop"])
    source = document()
    source.body = "Synthetic body"
    source.brief.sellingPoints = "Synthetic selling points"
    source.settings.ratio = "1:1"
    source.settings.duration = 27
    source.settings.resolution = "4K"
    source.settings.skill = "Synthetic skill"
    saved = content.save(UUID(source.id), source)
    return d | dict(content=content, parent=saved, flows=FlowService(content))


def request(d: dict[str, Any], kind: str = "scripts", key: str | None = None) -> dict[str, Any]:
    return dict(kind=kind, version=d["parent"]["version"], idempotencyKey=key or str(uuid4()))


def test_http_five_derivations_match_frontend_and_current_scope(parent: dict[str, Any]) -> None:
    d = parent
    route = "/v1/content/documents/" + d["parent"]["id"] + "/flow"
    headers = {"X-Shop-Id": str(d["shop"]), "X-CSRF-Token": challenge(d["client"])}
    assert (
        d["client"].post(route, json=request(d), headers={"X-Shop-Id": str(d["shop"])}).status_code
        == 403
    )
    children = {}
    for kind in ["planning", "scripts", "graphics", "videos", "editing"]:
        value = request(d, kind)
        response = d["client"].post(route, json=value, headers=headers)
        assert response.status_code == 200, response.text
        saved = response.json()["data"]
        assert saved["parentId"] == d["parent"]["id"] and saved["version"] == 1
        assert d["client"].post(route, json=value, headers=headers).json()["data"] == saved
        children[kind] = saved
    assert len({c["id"] for c in children.values()}) == 5
    with business_transaction(d["engine"], d["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM content_derivations")).scalar_one() == 5
    if target := os.getenv("YULIANG_CONTENT_FLOW_CAPTURE"):
        Path(target).write_text(
            json.dumps(dict(source=d["parent"], children=children), ensure_ascii=False),
            encoding="utf-8",
        )
    assert (
        d["client"]
        .post(route, json=request(d), headers=headers | {"X-Shop-Id": str(uuid4())})
        .status_code
        == 403
    )


def test_concurrent_key_one_child_and_retry_after_parent_edit(parent: dict[str, Any]) -> None:
    d = parent
    identifier = UUID(d["parent"]["id"])
    value = FlowCreate.model_validate(request(d))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: d["flows"].create(identifier, value), range(2)))
    assert results[0] == results[1]
    changed_child = Document.model_validate(results[0] | dict(body="child independently edited"))
    d["content"].save(UUID(changed_child.id), changed_child)
    edited = Document.model_validate(d["parent"] | dict(body="later edited"))
    d["content"].save(identifier, edited)
    assert d["flows"].create(identifier, value) == results[0]
    with pytest.raises(DocumentConflict):
        d["flows"].create(identifier, FlowCreate.model_validate(request(d)))
    with pytest.raises(ApiError) as mismatch:
        d["flows"].create(identifier, value.model_copy(update=dict(kind="graphics")))
    assert mismatch.value.code == "IDEMPOTENCY_MISMATCH"
    with business_transaction(d["engine"], d["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM content_derivations")).scalar_one() == 1


def test_failed_registration_rolls_back_child_and_history(
    parent: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    d = parent
    original = d["content"]._save

    def invalid_id(session: Any, identifier: UUID, document: Document, proofs: Any = None) -> Any:
        original(session, identifier, document, proofs)
        raise ApiError(ErrorCode.INVALID_INPUT, "Synthetic failure")

    monkeypatch.setattr(d["content"], "_save", invalid_id)
    with pytest.raises(ApiError):
        d["flows"].create(UUID(d["parent"]["id"]), FlowCreate.model_validate(request(d)))
    with business_transaction(d["engine"], d["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM content_documents")).scalar_one() == 1
        assert (
            session.execute(text("SELECT count(*) FROM content_document_versions")).scalar_one()
            == 1
        )
        assert session.execute(text("SELECT count(*) FROM content_derivations")).scalar_one() == 0


def test_revoked_scope_and_archived_child_do_not_recreate(parent: dict[str, Any]) -> None:
    d = parent
    key = FlowCreate.model_validate(request(d))
    source = UUID(d["parent"]["id"])
    child = d["flows"].create(source, key)
    d["content"].delete(UUID(child["id"]), 1)
    with pytest.raises(ApiError) as archived:
        d["flows"].create(source, key)
    assert archived.value.code == "RESOURCE_TERMINAL"
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:id"),
            dict(id=d["identity"].membership_id),
        )
    with pytest.raises(ApiError):
        d["flows"].create(source, FlowCreate.model_validate(request(d)))


def test_caller_transaction_rollback_keeps_no_child_or_derivation(parent: dict[str, Any]) -> None:
    d = parent
    source = UUID(d["parent"]["id"])
    value = FlowCreate.model_validate(request(d, key="workflow:run:step:attempt:1"))
    with pytest.raises(RuntimeError, match="caller rollback"):
        with business_transaction(d["engine"], d["identity"]) as session:
            child = d["flows"].create_in_transaction(session, source, value)
            assert d["flows"].create_in_transaction(session, source, value) == child
            assert session.execute(text("SELECT count(*) FROM content_documents")).scalar_one() == 2
            raise RuntimeError("caller rollback")
    with business_transaction(d["engine"], d["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM content_documents")).scalar_one() == 1
        assert (
            session.execute(text("SELECT count(*) FROM content_document_versions")).scalar_one()
            == 1
        )
        assert session.execute(text("SELECT count(*) FROM content_derivations")).scalar_one() == 0
        saved = d["flows"].create_in_transaction(session, source, value)
    assert d["flows"].create(source, value) == saved
    assert saved["parentId"] == str(source)


def test_derivation_links_immutable_to_runtime(parent: dict[str, Any]) -> None:
    d = parent
    d["flows"].create(UUID(d["parent"]["id"]), FlowCreate.model_validate(request(d)))
    for sql in ["UPDATE content_derivations SET kind='editing'", "DELETE FROM content_derivations"]:
        with business_transaction(d["engine"], d["identity"]) as session:
            with pytest.raises(DBAPIError):
                session.execute(text(sql))
    with business_transaction(d["engine"], d["identity"]) as session:
        with pytest.raises(DBAPIError) as forged:
            session.execute(
                text(
                    "INSERT INTO content_derivations(tenant_id,actor_id,request_key,parent_id,"
                    "parent_revision,child_id,child_revision,kind) "
                    "SELECT tenant_id,actor_id,'forged',parent_id,parent_revision,child_id,"
                    "child_revision,'graphics' FROM content_derivations"
                )
            )
        assert getattr(forged.value.orig, "sqlstate", None) == "22023"


@pytest.mark.parametrize("collision", [False, True])
def test_frozen_prepared_child_first_version_replay_and_create_only(
    parent: dict[str, Any], collision: bool
) -> None:
    d = parent
    identifier = UUID(d["parent"]["id"])
    candidate = Document.model_validate(d["parent"] | dict(body="frozen new body"))
    prepared = derive_document(candidate, "graphics")
    value = FlowCreate.model_validate(request(d, "graphics", "workflow:frozen:candidate"))
    if collision:
        d["content"].save(UUID(prepared.id), prepared)
        with pytest.raises(ApiError) as refused:
            with business_transaction(d["engine"], d["identity"]) as session:
                d["flows"].create_in_transaction(session, identifier, value, prepared=prepared)
        assert refused.value.code == ErrorCode.VERSION_CONFLICT
        assert d["content"].get(UUID(prepared.id))["version"] == 1
        return
    with business_transaction(d["engine"], d["identity"]) as session:
        saved = d["flows"].create_in_transaction(session, identifier, value, prepared=prepared)
    assert saved["version"] == 1 and saved["body"] == "frozen new body"
    assert "frozen new body" in saved["prompt"]
    with business_transaction(d["engine"], d["identity"]) as session:
        replay = d["flows"].create_in_transaction(session, identifier, value, prepared=prepared)
        assert replay == saved
    with pytest.raises(ApiError) as mismatch:
        with business_transaction(d["engine"], d["identity"]) as session:
            d["flows"].create_in_transaction(
                session,
                identifier,
                value,
                prepared=prepared.model_copy(update=dict(body="changed")),
            )
    assert mismatch.value.code == ErrorCode.IDEMPOTENCY_MISMATCH
