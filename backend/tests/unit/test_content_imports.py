"""本地容器清单与稳定键边界；不冒充浏览器IndexedDB读取验收。"""

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.content.imports_models import ImportBundle


def value() -> dict[str, Any]:
    return dict(
        id=str(uuid4()),
        kind="planning",
        title="合法文档",
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
        settings=dict(
            ratio="9:16", duration=15, resolution="1080P", skill="任意保存标签不是执行授权"
        ),
        prompt="",
    )


def bundle() -> ImportBundle:
    return ImportBundle.model_validate(
        dict(
            importKey="local-session-1",
            owner=dict(mode="API", tenantId="1", userId=str(uuid4()), shopId=str(uuid4())),
            state=dict(schema=1, revision=9, documents=[value()], assets=[]),
            productMap={},
            assetMap={},
        )
    )


def test_digest_stable_order_and_cloud_id_exact_scope() -> None:
    b = bundle()
    assert b.digest() == ImportBundle.model_validate(b.model_dump(by_alias=True)).digest()
    actor, shop = uuid4(), uuid4()
    id = b.cloud_id(1, actor, shop, b.state.documents[0].id)
    assert id == b.cloud_id(1, actor, shop, b.state.documents[0].id)
    assert id != b.cloud_id(2, actor, shop, b.state.documents[0].id)
    assert id != b.cloud_id(1, uuid4(), shop, b.state.documents[0].id)


def test_local_container_revision_changes_manifest_but_not_cloud_id() -> None:
    b = bundle()
    altered = ImportBundle.model_validate(
        b.model_dump(by_alias=True)
        | {"state": b.state.model_dump(by_alias=True) | {"revision": 10}}
    )
    actor, shop = uuid4(), uuid4()
    assert b.digest() != altered.digest()
    assert b.cloud_id(1, actor, shop, "doc") == altered.cloud_id(1, actor, shop, "doc")


@pytest.mark.parametrize("alteration", ["duplicate", "path", "mock", "schema", "binary"])
def test_unknown_metadata_or_ambiguous_ids_rejected(alteration: str) -> None:
    b = bundle().model_dump(by_alias=True)
    if alteration == "duplicate":
        b["state"]["documents"] *= 2
    elif alteration == "path":
        b["filePath"] = "C:/private/file"
    elif alteration == "mock":
        b["owner"]["mode"] = "MOCK"
    elif alteration == "schema":
        b["state"]["schema"] = 2
    else:
        b["state"]["files"] = {"id": "base64"}
    with pytest.raises(ValidationError):
        ImportBundle.model_validate(b)
