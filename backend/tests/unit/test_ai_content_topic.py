"""独立选题prompt与旧场景兼容边界，不调用模型。"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import yuliang.modules.aikernel.content_prompts as registry
from yuliang.modules.aikernel.content_prompts import load_content_prompt
from yuliang.modules.content.ai_content_context import safe_text
from yuliang.modules.content.ai_content_models import Scenario, output_patch
from yuliang.modules.content.workflow_model_production import SCENARIO_STEPS


@pytest.mark.parametrize(
    "scenario,digest",
    [
        ("content_planning", "720f870f224db2cfbc37d5053be9977b01031a069af763fa9ef69fa461d98ecb"),
        ("content_script", "3dc695d148fb989df49955c1922dacd8471b4e5af865c37aa3e0d209333ce52a"),
        ("content_storyboard", "d70561d5cc3667dd60d863bf8362d00f70932c0077544f8eb9495a48b93d011d"),
    ],
)
def test_existing_three_prompts_keep_released_hash_and_version(
    scenario: Scenario, digest: str
) -> None:
    prompt = load_content_prompt(scenario)
    assert prompt.digest == digest and prompt.version == 1


def test_topic_is_dedicated_body_prompt_and_keeps_old_alias() -> None:
    topic = load_content_prompt("content_topic")
    assert (
        topic.prompt_key == "content_topic" and topic.kind == "planning" and topic.field == "body"
    )
    assert topic.schema == "content-body-v1" and topic.version == 1
    assert topic.digest != load_content_prompt("content_planning").digest
    assert SCENARIO_STEPS["content_topic"] == {"topic"}
    assert SCENARIO_STEPS["content_planning"] == {"topic", "planning"}
    assert "选题候选" in topic.template and "不得把选题直接扩写成完整策划或脚本" in topic.template


def test_topic_draft_golden_use_existing_body_contract_only() -> None:
    root = Path(registry.__file__).with_suffix("") / "topic" / "v1"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["status"] == "DRAFT" and manifest["version"] == 1
    for case in json.loads((root / "golden.json").read_text(encoding="utf-8-sig")):
        assert case["scenario"] == "content_topic"
        patch = output_patch("content_topic", case["output"])
        assert set(patch) == {"body"}
        safe_text(patch)


@pytest.mark.parametrize("extra", [{"productId": "forged"}, {"topics": []}, {"approval": True}])
def test_topic_output_cannot_add_unfrozen_document_or_identity_fields(
    extra: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        output_patch("content_topic", dict(body="选题") | extra)
