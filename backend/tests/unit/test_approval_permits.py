"""后台白名单的纯比较边界，不能把纯结果当运行审批许可。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from state_cases import APPROVER, REQUESTER, facts_for, state_job

from yuliang.modules.command.jobs import CommandJob, JobStatus
from yuliang.modules.command.state import JobEvent, evolve
from yuliang.modules.execution.approval_models import (
    ServerApprovalPermit,
    human_required,
    permit_matches,
)
from yuliang.platform.errors import ApiError


def test_permit_bounds_and_three_high_risk_classes_remain_human() -> None:
    now = datetime.now(UTC)
    pause = state_job(JobStatus.PENDING_APPROVAL, now)
    assert human_required(pause) == ("IMPORTANT_OR_UNCLASSIFIED_PAUSE",)
    assert human_required(pause, important=True) and not human_required(pause, important=False)
    permit = ServerApprovalPermit(
        id=uuid4(),
        tenant_id=1,
        policy_id=uuid4(),
        policy_version=1,
        policy_actor_membership_id=uuid4(),
        account_id=pause.account_id,
        campaign_id=pause.target_id,
        action="PAUSE_AD_PLAN",
        important=False,
        classification_ref="synthetic/noncritical",
        valid_from=now - timedelta(seconds=1),
        valid_until=now + timedelta(minutes=1),
        proof_ref="synthetic/policy",
        active=True,
    )
    assert permit_matches(pause, permit, at=now, actor_membership=permit.policy_actor_membership_id)
    assert not permit_matches(
        pause, permit, at=permit.valid_until, actor_membership=permit.policy_actor_membership_id
    )
    assert not permit_matches(pause, permit, at=now, actor_membership=uuid4())
    assert not permit_matches(
        pause,
        permit.model_copy(update={"active": False}),
        at=now,
        actor_membership=permit.policy_actor_membership_id,
    )
    for action, before, after, required in (
        ("CHANGE_DAILY_BUDGET", 100, 110, "BUDGET_INCREASE"),
        ("CHANGE_BID", 100, 110, "EXPOSURE_INCREASE"),
        ("CHANGE_DAILY_BUDGET", 100, 90, None),
    ):
        job = CommandJob.model_validate(
            pause.model_dump()
            | {
                "action_type": action,
                "old_value": {"kind": "MONEY", "amount_minor": before},
                "new_value": {"kind": "MONEY", "amount_minor": after},
            }
        )
        assert human_required(job) == ((required,) if required else ())
    for changes in (
        {"important": None},
        {"classification_ref": None},
        {"valid_until": permit.valid_from},
    ):
        with pytest.raises(ValidationError):
            ServerApprovalPermit.model_validate(permit.model_dump() | changes)


def test_policy_method_needs_reference_cannot_reject_or_self_approve() -> None:
    now = datetime.now(UTC)
    job = state_job(JobStatus.PENDING_APPROVAL, now)

    base = facts_for(JobEvent.APPROVE, job, now)
    facts = replace(base, approval_method="SERVER_POLICY", approval_policy_ref=uuid4())
    after = evolve(job, JobEvent.APPROVE, facts, at=now, actor_id=APPROVER, requester_id=REQUESTER)
    assert after.approval.method == "SERVER_POLICY" and after.approval.actor_id == str(APPROVER)
    for invalid in (
        replace(facts, approval_policy_ref=None),
        replace(facts, proof_ref=None),
        replace(facts, approval_method="MANUAL"),
    ):
        with pytest.raises(ApiError):
            evolve(
                job, JobEvent.APPROVE, invalid, at=now, actor_id=APPROVER, requester_id=REQUESTER
            )
    with pytest.raises(ApiError):
        evolve(job, JobEvent.APPROVE, facts, at=now, actor_id=REQUESTER, requester_id=REQUESTER)
    with pytest.raises(ApiError):
        evolve(job, JobEvent.REJECT, facts, at=now, actor_id=APPROVER, requester_id=REQUESTER)
