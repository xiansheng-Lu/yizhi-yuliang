"""独立边界与确认样例；不访问平台或模型。"""

from uuid import uuid4

import pytest

from yuliang.modules.aikernel.prompts import load_bundle
from yuliang.modules.aikernel.prompts.registry import CandidateObject, Interpretation
from yuliang.modules.command.parser import guard_model, parse_rules, wire


@pytest.mark.parametrize("case", load_bundle().cases, ids=lambda case: case.id)
def test_m2_03_confirmed_expected_semantics(case: object) -> None:
    from yuliang.modules.aikernel.prompts.registry import GoldenCase

    assert isinstance(case, GoldenCase)
    assert parse_rules(case.input, case.context).semantic() == case.expected.semantic()


@pytest.mark.parametrize(
    "statement",
    [
        "不要暂停A计划",
        "A计划暂停吗",
        "如果效果差就暂停A计划",
        "暂停A计划，暂停B计划",
        "暂停A计划B计划",
        "看看数据并暂停A计划",
        "删除用户并暂停A计划",
        "A计划预算增加100元",
        "A计划预算改为1,00元",
        "A计划预算改为1.001元",
        "A计划预算改为1.5分",
        "A计划预算改为9007199254740992分",
        "AA计划预算改为100元",
        "A计划ROI设为1e2",
    ],
)
def test_m2_03_unsupported_or_non_absolute_inputs_are_clarified(statement: str) -> None:
    assert parse_rules(statement, load_bundle().cases[0].context).kind == "UNRECOGNIZED"


def test_m2_03_integer_fen_and_positive_roi_keep_original_units() -> None:
    context = load_bundle().cases[0].context
    value = wire(parse_rules("A计划预算改为320分", context))
    assert value["new_value"] == {"kind": "MONEY", "currency": "CNY", "amount_minor": 320}
    value = wire(parse_rules("A计划ROI设为0.01", context))
    assert value["new_value"] == {"kind": "RATIO", "value": 0.01}


def test_m2_03_same_name_ambiguity_never_picks_first_or_accepts_model_pick() -> None:
    case = load_bundle().cases[0]
    original = case.context.candidates[0]
    duplicate = CandidateObject(id=uuid4(), name=original.name, target_type="AD_PLAN")
    context = case.context.model_copy(update={"candidates": (original, duplicate)})
    result = parse_rules(case.input, context)
    assert set(result.candidate_ids) == {original.id, duplicate.id}
    assert guard_model(case.input, context, case.expected).kind == "UNRECOGNIZED"


def test_m2_03_model_cannot_fabricate_completion_text_query_data_or_authority() -> None:
    case = load_bundle().cases[0]
    proposed = case.expected.model_copy(update={"text": "预算已成功修改并审批"})
    safe = wire(guard_model(case.input, case.context, proposed))
    assert "已成功" not in safe["text"] and "尚未执行" in safe["text"]
    denied = guard_model(
        case.input, case.context, Interpretation(kind="QUERY", live_session_id=None)
    )
    assert denied.kind == "UNRECOGNIZED"
    assert set(wire(denied)) == {"kind", "text"}


def test_m2_03_too_many_candidates_are_not_silently_truncated() -> None:
    case = load_bundle().cases[8]
    context = case.context.model_copy(
        update={
            "candidates": tuple(
                CandidateObject(id=uuid4(), name=f"计划{index}", target_type="AD_PLAN")
                for index in range(21)
            )
        }
    )
    assert parse_rules(case.input, context).kind == "UNRECOGNIZED"


def test_m2_03_explicit_unknown_query_never_selects_the_only_visible_session() -> None:
    case = load_bundle().cases[7]
    assert parse_rules("查看B场次数据", case.context).kind == "UNRECOGNIZED"
    result = parse_rules("查看测试场次数据", case.context)
    assert result.kind == "QUERY" and result.live_session_id == case.expected.live_session_id
    context = case.context.model_copy(update={"candidates": ()})
    assert parse_rules("看看当前直播间情况", context).live_session_id is None


@pytest.mark.parametrize("index", [3, 25])
def test_m2_04_named_object_resolves_authorized_model_superset_without_guessing(index: int) -> None:
    case = load_bundle().cases[index]
    proposed = case.expected.model_copy(
        update={
            "candidate_ids": tuple(
                c.id for c in case.context.candidates if c.target_type == "AD_PLAN"
            )
        }
    )
    assert guard_model(case.input, case.context, proposed).semantic() == case.expected.semantic()
    missing_target = proposed.model_copy(update={"candidate_ids": (proposed.candidate_ids[1],)})
    assert guard_model(case.input, case.context, missing_target).kind == "UNRECOGNIZED"


def test_m2_04_current_query_can_fill_missing_model_session_but_never_wrong_session() -> None:
    case = load_bundle().cases[7]
    proposed = Interpretation(kind="QUERY", live_session_id=None)
    assert guard_model(case.input, case.context, proposed).semantic() == case.expected.semantic()
    assert guard_model("查看不存在的场次数据", case.context, proposed).kind == "UNRECOGNIZED"


def test_m2_04_model_cannot_replace_named_query_or_choose_ambiguous_session() -> None:
    case = load_bundle().cases[7]
    first = [c for c in case.context.candidates if c.target_type == "LIVE_SESSION"][0]
    second = CandidateObject(id=uuid4(), target_type="LIVE_SESSION", name="另一场次")
    context = case.context.model_copy(update={"candidates": (first, second)})
    wrong = Interpretation(kind="QUERY", live_session_id=second.id)
    assert guard_model("查看测试场次数据", context, wrong).kind == "UNRECOGNIZED"
    selected = Interpretation(kind="QUERY", live_session_id=first.id)
    assert guard_model("看看当前直播间情况", context, selected).kind == "UNRECOGNIZED"
