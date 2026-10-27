"""固定样例是待人审的测试资产；不把给定预期自比当作真实模型质量通过。"""

import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.aikernel.prompts.registry import (
    FrozenRoute,
    Interpretation,
    load_bundle,
    validate_successor,
)

ROOT = Path(__file__).parents[2] / "src/yuliang/modules/aikernel/prompts/v1"


def test_m5_03a_frozen_cases_cover_categories_and_require_human_labels() -> None:
    bundle = load_bundle()
    assert len(bundle.cases) == 28 and len(bundle.pending_review_ids()) == 28
    with pytest.raises(ValueError, match="人工"):
        bundle.require_human_labels()
    result = bundle.compare(
        {case.id: case.expected.model_dump(mode="json") for case in bundle.cases}
    )
    assert result["matched"] == 28 and not result["human_labels_confirmed"]
    assert not result["release_allowed"]


@pytest.mark.parametrize("filename", ["template.txt", "cases.json"])
def test_m5_03a_in_place_changes_fail_frozen_hash(tmp_path: Path, filename: str) -> None:
    shutil.copytree(ROOT, tmp_path / "candidate")
    changed = tmp_path / "candidate" / filename
    changed.write_text(changed.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="冻结"):
        load_bundle(tmp_path / "candidate")


@pytest.mark.parametrize("mutation", ["unknown_id", "no_candidates", "approval", "wrong_units"])
def test_m5_03a_unsafe_prediction_and_missing_case_remain_failures(mutation: str) -> None:
    bundle = load_bundle()
    predictions = {case.id: case.expected.model_dump(mode="json") for case in bundle.cases}
    current = predictions["CP-001"]
    if mutation == "unknown_id":
        current["candidate_ids"] = [str(uuid4())]
    elif mutation == "no_candidates":
        current["candidate_ids"] = []
    elif mutation == "approval":
        current["approval"] = "APPROVED"
    else:
        current["new_value"] = {"kind": "RATIO", "value": 5000}
    del predictions["CP-028"]
    result = bundle.compare(predictions)
    assert result["failed_ids"] == ["CP-001", "CP-028"] and result["matched"] == 26


def test_m5_03a_revision_cannot_erase_badcases_or_silently_relabel() -> None:
    original = load_bundle()
    with pytest.raises(ValueError, match="更高版本"):
        validate_successor(original, original)
    version = original.manifest.model_copy(
        update={"prompt_version": 2, "change_reason": "测试新版本"}
    )
    removed = original.model_copy(update={"manifest": version, "cases": original.cases[:-1]})
    with pytest.raises(ValueError, match="删除"):
        validate_successor(original, removed)
    changed = original.cases[0].model_copy(update={"input": "新的测试输入"})
    cases = (changed, *original.cases[1:])
    candidate = original.model_copy(update={"manifest": version, "cases": cases})
    with pytest.raises(ValueError, match="理由"):
        validate_successor(original, candidate)
    candidate = candidate.model_copy(
        update={
            "manifest": version.model_copy(
                update={"case_changes": {"CP-001": "合成测试理由，仅内存，不写入真实人工标注"}}
            )
        }
    )
    validate_successor(original, candidate)


def test_m5_03a_human_signature_cannot_survive_changed_case_hash() -> None:
    original = load_bundle().cases[0]
    value = original.model_dump(mode="json")
    value["reviews"] = [
        {
            "role": "BE",
            "name": "IN_MEMORY_TEST_ONLY",
            "reviewed_on": "2026-10-27",
            "baseline": "0" * 40,
            "source": "synthetic memory fixture",
            "case_hash": original.case_hash(),
        }
    ]
    approved = type(original).model_validate(value)
    assert len(approved.reviews) == 1
    value["input"] = "被修改的输入"
    with pytest.raises(ValidationError, match="失效"):
        type(original).model_validate(value)
    assert not original.reviews


def test_m5_03a_route_freezes_model_and_prompt_versions() -> None:
    bundle = load_bundle()
    spec = {
        "model_key": "example.primary",
        "provider": "test",
        "model": "example-v1",
        "endpoint_ref": "PRIMARY_ENDPOINT",
        "secret_ref": "PRIMARY_KEY",
        "billing_currency": "CNY",
    }
    snapshot: dict[str, Any] = {
        "config_id": str(uuid4()),
        "revision": 1,
        "primary": spec,
        "backup": None,
        "selected": spec,
        "prompt_hash": bundle.manifest.template_sha256,
        "output_schema_ref": bundle.manifest.output_schema_ref,
    }
    route: FrozenRoute = bundle.bind_route(snapshot)
    assert route.selected.model_key == "example.primary"
    assert "api_key" not in route.model_dump_json()
    with pytest.raises(ValueError, match="版本"):
        bundle.bind_route(snapshot | {"prompt_hash": "0" * 64})
    with pytest.raises(ValidationError):
        bundle.bind_route(snapshot | {"selected": spec | {"model": "unfrozen"}})
    with pytest.raises(ValidationError):
        bundle.bind_route(snapshot | {"primary": spec | {"api_key": "forbidden"}})


def test_m5_03a_output_never_contains_platform_execution_authority() -> None:
    with pytest.raises(ValidationError):
        Interpretation.model_validate_json(
            json.dumps(
                {
                    "kind": "UNRECOGNIZED",
                    "connector": "platform.execute",
                    "approval": "PASS",
                }
            )
        )
