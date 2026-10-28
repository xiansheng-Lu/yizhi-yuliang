from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_cloud_documents import value

from yuliang.modules.content.flows import FlowCreate, derive_document
from yuliang.modules.content.models import Document, Kind
from yuliang.platform.errors import ApiError


@pytest.mark.parametrize("kind", ["planning", "scripts", "graphics", "videos", "editing"])
def test_frontend_inheritance_fresh_ids_and_source_unchanged(kind: Kind) -> None:
    payload = value() | dict(
        title="原稿 · 视频",
        body="原始正文",
        brief=dict(sellingPoints="卖点"),
        scenes=[dict(id=str(uuid4()), title="一", narration="旁白", visual="画面", duration=1)],
        settings=dict(ratio="1:1", duration=27, resolution="4K", skill="自有标签"),
        messages=[dict(id="m", text="旧对话", context="", createdAt="2026-10-28T00:00:00Z")],
    )
    original = Document.model_validate(payload)
    before = deepcopy(original.model_dump())
    child = derive_document(original, kind)
    assert child.id != original.id and child.parentId == original.id
    assert child.version == 1 and child.status == "draft" and child.kind == kind
    assert child.brief == original.brief and child.body == original.body
    assert child.scenes[0].id != original.scenes[0].id
    assert child.scenes[0].visual == original.scenes[0].visual
    assert child.messages is None and not child.clips and not child.edges
    if kind == "editing":
        assert child.settings == original.settings
    else:
        assert child.settings.ratio == "9:16" and child.settings.duration == 15
    assert original.model_dump() == before


def test_flow_does_not_silently_truncate_oversized_inherited_title() -> None:
    source = Document.model_validate(value() | dict(title="长" * 120))
    with pytest.raises(ApiError) as issue:
        derive_document(source, "scripts")
    assert issue.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("change", [dict(version=True), dict(version=0), dict(idempotency_key=" ")])
def test_invalid_flow_version_and_key_rejected(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FlowCreate.model_validate(
            dict(kind="scripts", version=1, idempotency_key="stable:key") | change
        )
