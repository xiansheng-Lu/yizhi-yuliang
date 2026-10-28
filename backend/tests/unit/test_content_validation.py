"""M3-16共享规则与三purpose一致性：业务入口未存在时不伪装实际集成。"""

from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

import pytest

from yuliang.modules.content.models import Document
from yuliang.modules.content.validation import MediaFact, validate_content
from yuliang.modules.content.validation_errors import schema_details
from yuliang.platform.errors import ApiError


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


def graph(edges: list[tuple[int, int]], count: int = 4) -> dict[str, Any]:
    v = value()
    v["nodes"] = [
        dict(id=f"n{i}", type="reference", x=-i, y=0, text="", assetId="") for i in range(count)
    ]
    v["edges"] = [
        dict(id=f"e{i}", sourceNodeId=f"n{a}", targetNodeId=f"n{b}")
        for i, (a, b) in enumerate(edges)
    ]
    return v


@pytest.mark.parametrize(
    "edges",
    [[(0, 0)], [(0, 1), (1, 0)], [(0, 1), (2, 3), (3, 2)], [(0, 1), (1, 2), (2, 3), (3, 1)]],
)
def test_cycles_any_component_rejected_with_edge_id(edges: list[tuple[int, int]]) -> None:
    v = graph(edges)
    with pytest.raises(ApiError) as exc:
        validate_content(v)
    issue = next(d for d in exc.value.details if d["reason"] == "节点图不能包含环")
    assert issue["field"].startswith("document.edges.") and "edgeId" in issue
    assert issue["documentId"] == v["id"]


def test_large_graph_is_iterative_and_negative_coordinates_compatible() -> None:
    v = graph([(i, i + 1) for i in range(999)], count=1000)
    assert len(validate_content(v).nodes) == 1000
    v["edges"][-1]["targetNodeId"] = "n0"
    with pytest.raises(ApiError):
        validate_content(v)


def test_missing_node_and_duplicate_node_have_exact_paths() -> None:
    v = graph([(0, 1)])
    v["edges"][0]["targetNodeId"] = "missing"
    with pytest.raises(ApiError) as exc:
        validate_content(v)
    assert exc.value.details[0]["field"] == "document.edges.0.targetNodeId"
    v["edges"] = []
    v["nodes"][1]["id"] = "n0"
    with pytest.raises(ApiError) as exc:
        validate_content(v)
    assert exc.value.details[0]["field"] == "document.nodes.1.id"


@pytest.mark.parametrize("purpose", ["SAVE", "GENERATION", "SOP"])
def test_purpose_uses_identical_rule_without_business_side_effects(
    purpose: Literal["SAVE", "GENERATION", "SOP"],
) -> None:
    v = graph([(0, 0)])
    with pytest.raises(ApiError) as exc:
        validate_content(v, purpose=purpose)
    assert exc.value.code.value == "INVALID_INPUT"
    assert exc.value.details[0]["reason"] == "节点图不能包含环"


@pytest.mark.parametrize(
    "source,duration,verified,known,allowed",
    [
        (0, 10.01, True, Decimal("10"), True),
        (1, 9.01, True, Decimal("10"), True),
        (0, 10.01001, True, Decimal("10"), False),
        (1, 9.01001, True, Decimal("10"), False),
        (0, 0.05, False, Decimal("0"), False),
        (0, 0.05, True, None, False),
        (0, 0.05, True, Decimal("NaN"), False),
    ],
)
def test_trusted_media_duration_boundaries_are_synthetic_only(
    source: float, duration: float, verified: bool, known: Decimal | None, allowed: bool
) -> None:
    v = value()
    identifier = uuid4()
    v["clips"] = [
        dict(
            id="clip-one",
            assetId=str(identifier),
            track="video",
            start=0,
            sourceStart=source,
            duration=duration,
            text="",
            volume=1,
        )
    ]
    facts = {identifier: MediaFact("video", known, verified)}
    outcomes = []
    purposes: tuple[Literal["SAVE", "GENERATION", "SOP"], ...] = ("SAVE", "GENERATION", "SOP")
    for purpose in purposes:
        try:
            validate_content(v, purpose=purpose, media=facts)
            outcomes.append("PASS")
        except ApiError as exc:
            assert exc.details[0]["clipId"] == "clip-one"
            outcomes.append(exc.details[0]["reason"])
    assert len(set(outcomes)) == 1
    assert (outcomes[0] == "PASS") == allowed


def test_missing_media_and_wrong_kind_never_fall_back_to_zero() -> None:
    v = value()
    identifier = uuid4()
    v["clips"] = [
        dict(
            id="clip-one",
            assetId=str(identifier),
            track="audio",
            start=0,
            sourceStart=0,
            duration=1,
            text="",
            volume=1,
        )
    ]
    with pytest.raises(ApiError) as exc:
        validate_content(v)
    assert exc.value.details[0]["reason"] == "素材不存在或未取得可信元数据"
    with pytest.raises(ApiError) as exc:
        validate_content(v, media={identifier: MediaFact("image", Decimal(0), True)})
    assert exc.value.details[0]["reason"] == "片段轨道与素材类型不一致"


def test_deep_field_path_and_identifiers_do_not_echo_input_or_ctx() -> None:
    v = value()
    board = str(uuid4())
    layer = str(uuid4())
    v["boards"] = [
        dict(id=board, name="PRIVATE", layers=[dict(id=layer, fontSize="PASSWORD-PRIVATE")])
    ]
    errors = [
        dict(
            type="float_type",
            loc=("body", "boards", 0, "layers", 0, "fontSize"),
            input="PASSWORD-PRIVATE",
            ctx=dict(secret="TOKEN-PRIVATE"),
        )
    ]
    result = schema_details(errors, v)
    assert result[0]["field"] == "document.boards.0.layers.0.fontSize"
    assert result[0]["boardId"] == board and result[0]["layerId"] == layer
    assert "PRIVATE" not in str(result) and "ctx" not in str(result)
    unknown = schema_details([dict(type="extra_forbidden", loc=("body", "PASSWORD-PRIVATE"))], v)
    assert unknown[0]["field"] == "document" and "PRIVATE" not in str(unknown)


def test_copy_construct_cannot_turn_invalid_graph_into_valid_common_input() -> None:
    original = Document.model_validate(graph([]))
    bad = original.model_copy(update=dict(nodes=original.nodes + original.nodes))
    with pytest.raises(ApiError):
        validate_content(bad)
    constructed = Document.model_construct(**graph([(0, 0)]))
    with pytest.raises(ApiError):
        validate_content(constructed)


@pytest.mark.parametrize("text", ["", "   \n\t"])
def test_existing_caption_requires_text_but_empty_clip_collection_allowed(text: str) -> None:
    v = value()
    assert validate_content(v).clips == []
    v["clips"] = [
        dict(
            id="caption-one",
            assetId="",
            track="caption",
            start=0,
            sourceStart=0,
            duration=1,
            text=text,
            volume=1,
        )
    ]
    with pytest.raises(ApiError) as exc:
        validate_content(v)
    assert exc.value.details[0]["field"] == "document.clips.0.text"
    assert exc.value.details[0]["clipId"] == "caption-one"
    assert exc.value.details[0]["reason"] == "字幕片段需要非空文本"
