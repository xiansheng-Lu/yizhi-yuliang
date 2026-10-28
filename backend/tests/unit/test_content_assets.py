"""素材引用结构与短期实际存储证明不可自报。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr, ValidationError

from yuliang.infrastructure.blob import BlobError, BlobStore, VersionCheck
from yuliang.modules.content.asset_models import document_assets
from yuliang.modules.content.models import Document


def value() -> dict[str, Any]:
    return dict(
        id=str(uuid4()),
        kind="planning",
        title="草稿",
        productId="",
        parentId=None,
        version=1,
        updatedAt="2026-10-28T00:00:00Z",
        status="draft",
        brief={},
        body="",
        scenes=[],
        boards=[],
        nodes=[],
        edges=[],
        clips=[],
        settings=dict(ratio="9:16", duration=15, resolution="1080P", skill="口播精剪"),
        prompt="",
    )


def test_references_are_uuid_structure_and_roles_not_blanket_acceptance() -> None:
    v = value()
    identifier = str(uuid4())
    v["nodes"] = [dict(id="one", type="reference", x=0, y=0, text="", assetId=identifier)]
    doc = Document.model_validate(v)
    assert document_assets(doc) == {UUID(identifier): {"ANY"}}
    v["nodes"][0]["assetId"] = "unverified"
    with pytest.raises(ValidationError):
        Document.model_validate(v)


def test_reference_count_bounded_before_storage_work() -> None:
    v = value()
    v["nodes"] = [
        dict(id=str(i), type="reference", x=0, y=0, text="", assetId=str(uuid4()))
        for i in range(33)
    ]
    with pytest.raises(ValidationError):
        Document.model_validate(v)


def test_storage_attestation_is_instance_bound_context_bound_and_expiring() -> None:
    store = BlobStore(
        "http://127.0.0.1:19000",
        "synthetic-private",
        SecretStr("synthetic"),
        SecretStr("synthetic"),
        local=True,
    )
    other = BlobStore(
        "http://127.0.0.1:19000",
        "synthetic-private",
        SecretStr("synthetic"),
        SecretStr("synthetic"),
        local=True,
    )
    identifier = uuid4()
    key = store.key(1, uuid4(), identifier)
    now = datetime.now(UTC)
    try:
        with pytest.raises(BlobError):
            store.download_url(key, "version")
        check = VersionCheck(
            now, store._version_token(key, "version", 1, "text/plain", "a" * 64, identifier, now)
        )
        assert store.valid_version_check(
            check, key, "version", 1, "text/plain", "a" * 64, identifier
        )
        assert not other.valid_version_check(
            check, key, "version", 1, "text/plain", "a" * 64, identifier
        )
        assert not store.valid_version_check(
            check, key, "another", 1, "text/plain", "a" * 64, identifier
        )
        assert not store.valid_version_check(
            replace(check, token="forged"), key, "version", 1, "text/plain", "a" * 64, identifier
        )
        assert not store.valid_version_check(
            replace(check, checked_at=now - timedelta(seconds=61)),
            key,
            "version",
            1,
            "text/plain",
            "a" * 64,
            identifier,
        )
    finally:
        store.close()
        other.close()
