"""M3-16实际保存/内部入口与HTTP字段定位，未造生成/SOP业务接口。"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from yuliang.modules.content.models import Document, Edge
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_cloud_documents import content_ready as content_ready
from .test_cloud_documents import document
from .test_configuration_context import configuration_ready as configuration_ready
from .test_content_assets import assets_ready as assets_ready
from .test_content_assets import graphic
from .test_content_uploads import blob as blob
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import challenge, sign_in
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_shop_product_details import details as details
from .test_shop_products import products as products

pytestmark = pytest.mark.integration


def nodes() -> list[dict[str, Any]]:
    return [
        dict(id=str(uuid4()), type="reference", x=-10, y=0, text="", assetId="") for _ in range(2)
    ]


@pytest.mark.asyncio
async def test_http_cycle_and_deep_fields_have_safe_path_and_object_ids(
    content_ready: dict[str, Any], caplog: Any
) -> None:
    d = content_ready
    client = d["client"]
    client.cookies.clear()
    sign_in(d)
    headers = {"X-CSRF-Token": challenge(client), "X-Shop-Id": str(d["shop"])}
    value = document(d["product"]).model_dump()
    value["nodes"] = nodes()
    edgeid = str(uuid4())
    value["edges"] = [
        dict(id=edgeid, sourceNodeId=value["nodes"][0]["id"], targetNodeId=value["nodes"][0]["id"])
    ]
    response = client.put("/v1/content/documents/" + value["id"], json=value, headers=headers)
    assert response.status_code == 422
    issue = response.json()["error"]["details"][0]
    assert issue["edgeId"] == edgeid and issue["documentId"] == value["id"]
    assert issue["field"] == "document.edges.0.targetNodeId"
    value["edges"] = []
    boardid = str(uuid4())
    layerid = str(uuid4())
    value["boards"] = [
        dict(
            id=boardid,
            name="不会回显正文",
            width=1080,
            height=1350,
            background="#ffffff",
            layers=[
                dict(
                    id=layerid,
                    type="text",
                    text="PRIVATE-BODY",
                    assetId="",
                    x=0,
                    y=0,
                    width=20,
                    height=20,
                    fontSize="PRIVATE-PASSWORD",
                    color="#000000",
                    visible=True,
                    locked=False,
                )
            ],
        )
    ]
    response = client.put("/v1/content/documents/" + value["id"], json=value, headers=headers)
    assert response.status_code == 422
    issue = response.json()["error"]["details"][0]
    assert issue["field"] == "document.boards.0.layers.0.fontSize"
    assert issue["boardId"] == boardid and issue["layerId"] == layerid
    assert "PRIVATE" not in response.text and "PRIVATE" not in caplog.text
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM content_documents WHERE id=:id"),
                dict(id=UUID(value["id"])),
            ).scalar_one()
            == 0
        )


@pytest.mark.asyncio
async def test_private_save_construct_and_copy_are_revalidated_before_mutation(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    r = d["content"]
    raw = document(d["product"]).model_dump()
    raw["nodes"] = nodes()
    good = Document.model_validate(raw)
    identifier = UUID(good.id)
    r.save(identifier, good)
    bad = good.model_copy(
        update=dict(
            edges=[
                Edge(id="cycle-edge", sourceNodeId=good.nodes[0].id, targetNodeId=good.nodes[0].id)
            ]
        )
    )
    constructed = Document.model_construct(**bad.model_dump(warnings=False))
    for candidate in [bad, constructed]:
        with business_transaction(d["engine"], d["identity"]) as s:
            with pytest.raises(ApiError) as error:
                r._save(s, identifier, candidate)
            assert error.value.details[0]["reason"] == "节点图不能包含环"
    assert r.get(identifier)["version"] == 1
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM content_document_versions WHERE document_id=:id"),
                dict(id=identifier),
            ).scalar_one()
            == 1
        )


@pytest.mark.asyncio
async def test_actual_image_guard_survives_common_validator_and_zero_negative_coordinates(
    assets_ready: dict[str, Any],
) -> None:
    d = assets_ready
    value = graphic(d, d["asset"])
    value.boards[0].layers[0].x = -25
    value.boards[0].layers[0].y = 0
    saved = d["docs"].save(UUID(value.id), value)
    assert saved["boards"][0]["layers"][0]["x"] == -25
    assert saved["boards"][0]["layers"][0]["y"] == 0
    assert d["docs"].review(UUID(value.id), 1)["version"] == 2
    with pytest.raises(ApiError):
        d["assets"].archive(d["asset"])


@pytest.mark.asyncio
async def test_unknown_duration_never_enters_saved_content_or_opens_audio_upload(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    raw = document(d["product"], "editing").model_dump()
    identifier = uuid4()
    raw["clips"] = [
        dict(
            id="clip-under-test",
            assetId=str(identifier),
            track="video",
            start=0,
            sourceStart=0,
            duration=1,
            text="",
            volume=1,
        )
    ]
    value = Document.model_validate(raw)
    with pytest.raises(ApiError) as exc:
        d["content"].save(UUID(value.id), value)
    assert exc.value.details[0]["clipId"] == "clip-under-test"
    assert exc.value.details[0]["reason"] == "素材不存在或未取得可信元数据"
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM content_documents WHERE id=:id"), dict(id=UUID(value.id))
            ).scalar_one()
            == 0
        )


@pytest.mark.asyncio
async def test_actual_http_empty_caption_text_is_rejected_at_precise_clip(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    client = d["client"]
    client.cookies.clear()
    sign_in(d)
    headers = {"X-CSRF-Token": challenge(client), "X-Shop-Id": str(d["shop"])}
    raw = document(d["product"], "editing").model_dump()
    raw["clips"] = [
        dict(
            id="caption-under-test",
            assetId="",
            track="caption",
            start=0,
            sourceStart=0,
            duration=1,
            text="  ",
            volume=1,
        )
    ]
    response = client.put("/v1/content/documents/" + raw["id"], json=raw, headers=headers)
    assert response.status_code == 422
    issue = response.json()["error"]["details"][0]
    assert issue["field"] == "document.clips.0.text" and issue["clipId"] == "caption-under-test"
    assert issue["reason"] == "字幕片段需要非空文本"
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM content_documents WHERE id=:id"),
                dict(id=UUID(raw["id"])),
            ).scalar_one()
            == 0
        )
