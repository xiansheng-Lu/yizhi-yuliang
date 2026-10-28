"""SOP DAG有界校验与开线输入，未调用外部能力。"""

from typing import Any

import pytest
from pydantic import ValidationError

from yuliang.modules.content.workflows_models import RunCreate, TemplateDefinition


def template(dependencies: list[list[str]]) -> dict[str, Any]:
    return dict(
        code="test",
        name="测试",
        steps=[
            dict(code=f"s{i}", name="步骤", kind="MANUAL", dependsOn=deps)
            for i, deps in enumerate(dependencies)
        ],
    )


@pytest.mark.parametrize(
    "deps", [[["missing"]], [["s0"]], [["s1"], ["s0"]], [[], ["s2"], ["s1"]], [["s1", "s1"], []]]
)
def test_invalid_graph(deps: list[list[str]]) -> None:
    with pytest.raises(ValidationError):
        TemplateDefinition.model_validate(template(deps))


def test_hundred_node_chain_and_independent_branches() -> None:
    value = TemplateDefinition.model_validate(
        template([[]] + [[f"s{i - 1}"] for i in range(1, 100)])
    )
    assert len(value.steps) == 100
    assert len(TemplateDefinition.model_validate(template([[], [], ["s0", "s1"]])).steps) == 3


def test_duplicate_step_rejected() -> None:
    value = template([[], []])
    value["steps"][1]["code"] = "s0"
    with pytest.raises(ValidationError):
        TemplateDefinition.model_validate(value)


def test_malformed_uuid_and_unbounded_graph_rejected() -> None:
    with pytest.raises(ValidationError):
        RunCreate(templateId="-" * 36, productId="-" * 36, creationKey="test")
    with pytest.raises(ValidationError):
        TemplateDefinition.model_validate(template([[]] * 101))
