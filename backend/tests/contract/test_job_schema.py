"""与冻结前端字段/黄金样例对齐；不把黄金JSON称为平台证据。"""

import copy
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.command.jobs import CommandJob, CreateJobRequest, JobStatus

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = json.loads((ROOT / "docs/backend/contracts/golden.json").read_text(encoding="utf-8"))


def request() -> dict[str, Any]:
    return dict(
        source="PC_MANUAL",
        shop_id=str(uuid4()),
        target_type="AD_PLAN",
        target_id=str(uuid4()),
        action_type="CHANGE_DAILY_BUDGET",
        new_value={"kind": "MONEY", "currency": "CNY", "amount_minor": 200},
        intent="测试申请",
        idempotency_key="contract-job-key",
    )


def test_frozen_frontend_golden_and_exact_field_names() -> None:
    job = CommandJob.model_validate(GOLDEN["job"])
    assert set(job.model_dump()) == set(GOLDEN["job"])
    assert len(JobStatus) == 12 and job.risk_summary.source == "SERVER"
    assert job.model_dump(mode="json")["new_value"] == GOLDEN["job"]["new_value"]


def test_known_zero_old_ratio_is_preserved() -> None:
    job = CommandJob.model_validate(
        GOLDEN["job"]
        | {
            "action_type": "SET_ROI_TARGET",
            "old_value": {"kind": "RATIO", "value": 0},
            "new_value": {"kind": "RATIO", "value": 1.25},
        }
    )
    assert job.model_dump(mode="json")["old_value"]["value"] == 0


@pytest.mark.parametrize(
    "action,value,target",
    [
        ("CHANGE_DAILY_BUDGET", {"kind": "MONEY", "currency": "CNY", "amount_minor": 1}, "AD_PLAN"),
        (
            "CHANGE_BID",
            {"kind": "MONEY", "currency": "CNY", "amount_minor": 9007199254740991},
            "AD_PLAN",
        ),
        ("SET_ROI_TARGET", {"kind": "RATIO", "value": 1.25}, "AD_PLAN"),
        ("PAUSE_AD_PLAN", {"kind": "BOOLEAN", "value": False}, "AD_PLAN"),
        ("RESUME_AD_PLAN", {"kind": "BOOLEAN", "value": True}, "AD_PLAN"),
        ("REQUEST_LIVE_START", {"kind": "HANDOFF", "value": "START"}, "LIVE_SESSION"),
        ("REQUEST_LIVE_END", {"kind": "HANDOFF", "value": "END"}, "LIVE_SESSION"),
    ],
)
def test_seven_action_value_pairs(action: str, value: dict[str, Any], target: str) -> None:
    assert CreateJobRequest.model_validate(
        request() | {"action_type": action, "new_value": value, "target_type": target}
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "AUTOPILOT"},
        {"action_type": "SET_ROI_TARGET", "new_value": {"kind": "RATIO", "value": 0}},
        {"old_value": {"kind": "MONEY", "amount_minor": 1}},
        {"tenant_id": "other"},
        {"risk_summary": GOLDEN["job"]["risk_summary"]},
        {"approval": GOLDEN["job"]["approval"]},
        {"status": "SUCCEEDED"},
        {"target_id": "456"},
        {"target_type": "LIVE_SESSION"},
        {"intent": "  "},
        {"idempotency_key": "bad/key"},
        {"new_value": {"kind": "MONEY", "currency": "USD", "amount_minor": 1}},
        {"new_value": {"kind": "MONEY", "currency": "CNY", "amount_minor": 0}},
        {"new_value": {"kind": "MONEY", "currency": "CNY", "amount_minor": True}},
        {"new_value": {"kind": "MONEY", "currency": "CNY", "amount_minor": 1.5}},
        {"new_value": {"kind": "MONEY", "currency": "CNY", "amount_minor": 9007199254740992}},
        {"action_type": "SET_ROI_TARGET", "new_value": {"kind": "RATIO", "value": float("inf")}},
        {"action_type": "PAUSE_AD_PLAN", "new_value": {"kind": "BOOLEAN", "value": True}},
        {"action_type": "PAUSE_AD_PLAN", "new_value": {"kind": "BOOLEAN", "value": 0}},
        {"action_type": "RESUME_AD_PLAN", "new_value": {"kind": "BOOLEAN", "value": "true"}},
    ],
)
def test_external_input_cannot_assert_server_facts(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CreateJobRequest.model_validate(request() | changes)


@pytest.mark.parametrize(
    "mutation",
    ["mock_risk", "empty_checks", "mock_receipt", "unverified_success", "history", "approval"],
)
def test_response_cannot_forge_server_or_success(mutation: str) -> None:
    job = copy.deepcopy(GOLDEN["job"])
    if mutation == "mock_risk":
        job["risk_summary"]["source"] = "MOCK"
    elif mutation == "empty_checks":
        job["risk_summary"]["checks"] = []
    elif mutation == "mock_receipt":
        job["execution_receipt"] = GOLDEN["receipt"] | {"source": "MOCK"}
    elif mutation == "unverified_success":
        job["status"] = "SUCCEEDED"
        job["history"][-1]["status"] = "SUCCEEDED"
    elif mutation == "history":
        job["history"] = []
    else:
        job["approval"]["state"] = "APPROVED"
    with pytest.raises(ValidationError):
        CommandJob.model_validate(job)
