"""V1决定输入不接受客户端策略批准方法，不新增必填comment。"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.execution.approval_models import ReviewJobRequest


@pytest.mark.parametrize("decision", ["APPROVE", "REJECT"])
def test_v1_uppercase_decision_without_comment(decision: str) -> None:
    value = {"job_id": str(uuid4()), "expected_version": 2, "decision": decision}
    request = ReviewJobRequest.model_validate(value)
    assert request.comment is None and request.decision == decision


@pytest.mark.parametrize(
    "change",
    [
        {"decision": "approve"},
        {"decision": "SERVER_POLICY"},
        {"method": "SERVER_POLICY"},
        {"expected_version": True},
        {"expected_version": 0},
        {"policy_version": 1},
        {"intent_hash": "a" * 64},
        {"comment": "x" * 501},
    ],
)
def test_client_cannot_select_internal_method_or_override_frozen_facts(
    change: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ReviewJobRequest.model_validate(
            {"job_id": str(uuid4()), "expected_version": 2, "decision": "APPROVE"} | change
        )
