"""云稿输入版本与五种kind、有限结构、未接素材守卫。"""

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

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


@pytest.mark.parametrize("kind", ["planning", "scripts", "graphics", "videos", "editing"])
def test_five_document_kinds_preserve_null_parent(kind: str) -> None:
    v = value()
    v["kind"] = kind
    assert Document.model_validate(v).model_dump()["parentId"] is None


@pytest.mark.parametrize(
    "change",
    [
        dict(revision=1),
        dict(version=True),
        dict(version=0),
        dict(kind="assets"),
        dict(title=" "),
        dict(updatedAt="bad"),
        dict(settings=dict(ratio="2:3", duration=0, resolution="480P", skill="")),
        dict(body="大" * 1048576),
        dict(nodes=[dict(id="n", type="reference", x=0, y=0, text="", assetId="unverified")]),
    ],
)
def test_invalid_or_unverified_content_rejected(change: dict[str, Any]) -> None:
    v = value()
    v.update(change)
    with pytest.raises(ValidationError):
        Document.model_validate(v)
