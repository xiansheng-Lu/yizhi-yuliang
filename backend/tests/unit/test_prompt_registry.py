"""冻结预期确认可溯源；用户确认不代造BE/FE签名或真实模型质量结论。"""

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
    assert len(bundle.cases) == 28 and not bundle.pending_review_ids()
    assert bundle.confirmation_source() == "USER"
    assert bundle.confirmation is not None
    assert bundle.confirmation.confirmed_by == "USER"
    assert all(not case.reviews for case in bundle.cases)
    bundle.require_human_labels()
    result = bundle.compare(
        {case.id: case.expected.model_dump(mode="json") for case in bundle.cases}
    )
    assert result["matched"] == 28 and result["human_labels_confirmed"]
    assert result["confirmation_source"] == "USER"
    assert not result["release_allowed"]


def test_m5_03a_confirmation_absent_keeps_original_review_guard(tmp_path: Path) -> None:
    directory = tmp_path / "candidate"
    shutil.copytree(ROOT, directory)
    (directory / "confirmation.json").unlink()
    bundle = load_bundle(directory)
    assert len(bundle.pending_review_ids()) == 28
    assert bundle.confirmation_source() == "PENDING"
    with pytest.raises(ValueError, match="人工"):
        bundle.require_human_labels()


@pytest.mark.parametrize(
    "mutation", ["case_hash", "missing_case", "extra_case", "cases_digest", "template", "version"]
)
def test_m5_03a_stale_user_confirmation_fails_closed(tmp_path: Path, mutation: str) -> None:
    directory = tmp_path / "candidate"
    shutil.copytree(ROOT, directory)
    path = directory / "confirmation.json"
    confirmation = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "case_hash":
        confirmation["case_hashes"]["CP-001"] = "0" * 64
    elif mutation == "missing_case":
        del confirmation["case_hashes"]["CP-028"]
    elif mutation == "extra_case":
        confirmation["case_hashes"]["CP-029"] = "0" * 64
    elif mutation == "cases_digest":
        confirmation["cases_sha256"] = "0" * 64
    elif mutation == "template":
        confirmation["template_sha256"] = "0" * 64
    else:
        confirmation["prompt_version"] += 1
    path.write_text(json.dumps(confirmation), encoding="utf-8")
    with pytest.raises(ValueError, match="用户确认失效"):
        load_bundle(directory)


def test_m5_03a_confirmation_rechecks_changed_in_memory_case() -> None:
    bundle = load_bundle()
    changed = bundle.cases[0].model_copy(update={"input": "新的未确认输入"})
    changed_bundle = bundle.model_copy(update={"cases": (changed, *bundle.cases[1:])})
    with pytest.raises(ValueError, match="用户确认失效"):
        changed_bundle.require_human_labels()


@pytest.mark.parametrize("mutation", ["baseline", "role", "unknown_field"])
def test_m5_03a_confirmation_requires_real_record_shape(tmp_path: Path, mutation: str) -> None:
    directory = tmp_path / "candidate"
    shutil.copytree(ROOT, directory)
    path = directory / "confirmation.json"
    confirmation = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "baseline":
        confirmation["baseline"] = "not-a-code-baseline"
    elif mutation == "role":
        confirmation["confirmed_by"] = "BE_FE"
    else:
        confirmation["platform_execution_approved"] = True
    path.write_text(json.dumps(confirmation), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_bundle(directory)


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
