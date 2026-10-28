"""V1兼容、V2可选依赖、闭包与动作字段边界。"""

from typing import Any

import pytest
from pydantic import ValidationError

from yuliang.modules.content.workflows_execution_models import Execute, Skip
from yuliang.modules.content.workflows_execution_rules import downstream, satisfied
from yuliang.modules.content.workflows_models import TemplateDefinition


def graph(version: int = 2) -> dict[str, Any]:
    value: dict[str, Any] = dict(
        code="branches",
        name="分支",
        steps=[
            dict(code="a", name="a", kind="MANUAL"),
            dict(code="b", name="b", kind="MANUAL", dependsOn=["a"]),
            dict(code="c", name="c", kind="MANUAL", dependsOn=["a"]),
            dict(code="d", name="d", kind="MANUAL", dependsOn=["b", "c"]),
            dict(code="independent", name="独立", kind="REVIEW"),
        ],
    )
    if version == 2:
        value["schemaVersion"] = 2
    return value


def test_legacy_roundtrip_does_not_inject_optional_or_schema() -> None:
    value = TemplateDefinition.model_validate(graph(1))
    dumped = value.model_dump()
    assert "schemaVersion" not in dumped
    assert all(
        "optional" not in step and "optionalDependsOn" not in step for step in dumped["steps"]
    )
    assert TemplateDefinition.model_validate(dumped).schemaVersion == 1
    assert not satisfied(value, "b", {"a": "SKIPPED"})


@pytest.mark.parametrize("field,value", [("optional", False), ("optionalDependsOn", [])])
def test_legacy_rejects_new_fields(field: str, value: Any) -> None:
    old = graph(1)
    old["steps"][0][field] = value
    with pytest.raises(ValidationError):
        TemplateDefinition.model_validate(old)


def test_optional_skip_satisfies_only_explicit_dependency() -> None:
    data = graph()
    data["steps"][0]["optional"] = True
    data["steps"][1]["optionalDependsOn"] = ["a"]
    value = TemplateDefinition.model_validate(data)
    assert satisfied(value, "b", {"a": "SKIPPED"})
    assert not satisfied(value, "c", {"a": "SKIPPED"})
    assert satisfied(value, "c", {"a": "DONE"})
    assert downstream(value, "b") == {"b", "d"}
    assert downstream(value, "a") == {"a", "b", "c", "d"}


@pytest.mark.parametrize("deps", [["not_dependency"], ["a", "a"]])
def test_optional_dependency_subset_unique(deps: list[str]) -> None:
    data = graph()
    data["steps"][1]["optionalDependsOn"] = deps
    with pytest.raises(ValidationError):
        TemplateDefinition.model_validate(data)


def test_unknown_schema_version_rejected() -> None:
    data = graph()
    data["schemaVersion"] = 3
    with pytest.raises(ValidationError):
        TemplateDefinition.model_validate(data)


@pytest.mark.parametrize("field", ["productId", "version", "id", "nodes.0.text"])
def test_candidate_identity_arbitrary_path_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        Execute(
            actionKey="exec",
            expectedRunVersion=1,
            expectedAttempt=1,
            documentId="00000000-0000-4000-8000-000000000001",
            documentVersion=1,
            patch={field: "bad"},
        )


def test_blank_skip_rejected() -> None:
    with pytest.raises(ValidationError):
        Skip(actionKey="skip", expectedRunVersion=1, expectedAttempt=1, reason="  ")
