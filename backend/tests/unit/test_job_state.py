"""声明式合法/非法状态边及UNKNOWN/safe-retry；事实为明确合成输入。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from state_cases import APPROVER, REQUESTER, facts_for, state_job

from yuliang.modules.command.jobs import JobError, JobStatus, MoneyValue
from yuliang.modules.command.state import RULES, JobEvent, StateFacts, evolve
from yuliang.platform.errors import ApiError, ErrorCode

EDGES = [(event, status) for event, rule in RULES.items() for status in sorted(rule.sources)]
ILLEGAL = [
    (event, status)
    for event, rule in RULES.items()
    for status in JobStatus
    if status not in rule.sources
]


@pytest.mark.parametrize("event,status", EDGES)
def test_every_declarative_edge(event: JobEvent, status: JobStatus) -> None:
    now = datetime.now(UTC)
    job = state_job(status, now)
    if event == JobEvent.EXPIRE:
        now = job.expires_at + timedelta(seconds=1)
    after = evolve(
        job,
        event,
        facts_for(event, job, now),
        at=now,
        actor_id=APPROVER if event in {JobEvent.APPROVE, JobEvent.REJECT} else REQUESTER,
        requester_id=REQUESTER,
    )
    assert after.status == RULES[event].destination and after.version == job.version + 1
    assert after.job_id == job.job_id and after.history[:-1] == job.history
    assert after.history[-1].detail == RULES[event].detail


@pytest.mark.parametrize("event,status", ILLEGAL)
def test_every_illegal_pair_is_409(event: JobEvent, status: JobStatus) -> None:
    now = datetime.now(UTC)
    job = state_job(status, now)
    with pytest.raises(ApiError) as denied:
        evolve(
            job,
            event,
            facts_for(event, job, now),
            at=now,
            actor_id=APPROVER,
            requester_id=REQUESTER,
        )
    assert denied.value.code == ErrorCode.ILLEGAL_TRANSITION


@pytest.mark.parametrize("event", [JobEvent.SUBMIT_UNKNOWN, JobEvent.READ_UNKNOWN])
def test_unknown_does_not_become_failure_or_retry(event: JobEvent) -> None:
    now = datetime.now(UTC)
    status = JobStatus.EXECUTING if event == JobEvent.SUBMIT_UNKNOWN else JobStatus.VERIFYING
    job = state_job(status, now)
    after = evolve(
        job, event, facts_for(event, job, now), at=now, actor_id=REQUESTER, requester_id=REQUESTER
    )
    assert after.status == status and after.execution_receipt is not None
    assert after.execution_receipt.verification == "UNKNOWN"
    assert after.execution_receipt.accepted == (None if event == JobEvent.SUBMIT_UNKNOWN else True)
    if event == JobEvent.SUBMIT_UNKNOWN:
        assert (
            after.error is not None
            and after.error.code == "RESULT_UNKNOWN"
            and not after.error.retryable
        )


def test_transport_unknown_keeps_platform_receipt_absent_and_requires_explicit_fact() -> None:
    now = datetime.now(UTC)
    job = state_job(JobStatus.EXECUTING, now)
    facts = StateFacts(proof_ref="execution-raw/synthetic/1", transport_unknown=True)
    after = evolve(
        job, JobEvent.SUBMIT_UNKNOWN, facts, at=now, actor_id=REQUESTER, requester_id=REQUESTER
    )
    assert after.execution_receipt is None and after.status == JobStatus.EXECUTING
    assert (
        after.error is not None
        and after.error.code == "RESULT_UNKNOWN"
        and not after.error.retryable
    )
    for missing in (replace(facts, proof_ref=None), replace(facts, transport_unknown=False)):
        with pytest.raises(ApiError):
            evolve(
                job,
                JobEvent.SUBMIT_UNKNOWN,
                missing,
                at=now,
                actor_id=REQUESTER,
                requester_id=REQUESTER,
            )


def test_transport_not_sent_requires_e1_and_cannot_erase_platform_acceptance() -> None:
    now = datetime.now(UTC)
    job = state_job(JobStatus.EXECUTING, now)
    facts = StateFacts(
        transport_not_sent=True,
        proof_ref="execution-recovery/synthetic-proof",
        evidence_level="E1",
        error=JobError(code="NOT_SENT", message="synthetic", retryable=False, at=now),
    )
    after = evolve(
        job,
        JobEvent.CONFIRMED_NOT_EXECUTED,
        facts,
        at=now,
        actor_id=REQUESTER,
        requester_id=REQUESTER,
    )
    assert after.status == JobStatus.FAILED and after.execution_receipt is None
    assert after.error is not None and not after.error.retryable
    assert facts.error is not None
    for invalid in (
        replace(facts, transport_not_sent=False),
        replace(facts, proof_ref=None),
        replace(facts, evidence_level="E0"),
        replace(facts, error=None),
        replace(facts, error=facts.error.model_copy(update={"retryable": True})),
    ):
        with pytest.raises(ApiError):
            evolve(
                job,
                JobEvent.CONFIRMED_NOT_EXECUTED,
                invalid,
                at=now,
                actor_id=REQUESTER,
                requester_id=REQUESTER,
            )
    with pytest.raises(ApiError):
        evolve(
            state_job(JobStatus.VERIFYING, now),
            JobEvent.CONFIRMED_NOT_EXECUTED,
            facts,
            at=now,
            actor_id=REQUESTER,
            requester_id=REQUESTER,
        )


@pytest.mark.parametrize(
    "reason",
    [
        "missing_risk",
        "old_risk",
        "policy_changed",
        "self_approval",
        "E0_match",
        "E0_reject",
        "retry_current",
        "increase",
        "retry_expired",
        "failed_check",
    ],
)
def test_required_facts_cannot_be_omitted_or_overridden(reason: str) -> None:
    now = datetime.now(UTC)
    event = (
        JobEvent.APPROVE
        if reason in {"missing_risk", "old_risk", "policy_changed", "self_approval"}
        else JobEvent.MATCH
        if reason == "E0_match"
        else JobEvent.REJECT_SEND
        if reason == "E0_reject"
        else JobEvent.SAFE_RETRY
    )
    job = state_job(next(iter(RULES[event].sources)), now)
    facts = facts_for(event, job, now)
    actor = REQUESTER if reason == "self_approval" else APPROVER
    if reason == "missing_risk":
        facts = replace(facts, risk=None)
    elif reason in {"old_risk", "policy_changed"}:
        assert facts.risk is not None
        facts = replace(
            facts,
            risk=facts.risk.model_copy(
                update={"checked_at": now - timedelta(seconds=1)}
                if reason == "old_risk"
                else {"policy_version": 2}
            ),
        )
    elif reason.startswith("E0"):
        facts = replace(facts, evidence_level="E0")
    elif reason == "retry_current":
        facts = replace(facts, retry_current=None)
    elif reason == "increase":
        job = job.model_copy(
            update={
                "action_type": "CHANGE_DAILY_BUDGET",
                "old_value": MoneyValue(amount_minor=100),
                "new_value": MoneyValue(amount_minor=200),
            }
        )
        facts = replace(
            facts, retry_current=job.old_value, retry_actions=frozenset({"CHANGE_DAILY_BUDGET"})
        )
    elif reason == "retry_expired":
        job = job.model_copy(update={"expires_at": now - timedelta(seconds=1)})
    elif reason == "failed_check":
        assert facts.risk is not None
        check = facts.risk.checks[0].model_copy(update={"passed": False})
        facts = replace(facts, risk=facts.risk.model_copy(update={"checks": (check,)}))
    with pytest.raises(ApiError):
        evolve(job, event, facts, at=now, actor_id=actor, requester_id=REQUESTER)


@pytest.mark.parametrize("missing", ["risk", "proof", "E1", "error", "receipt"])
def test_unsent_rejection_requires_failed_risk_and_no_platform_receipt(missing: str) -> None:
    now = datetime.now(UTC)
    job = state_job(JobStatus.APPROVED, now)
    facts = facts_for(JobEvent.UNSENT_REJECT, job, now)
    if missing == "risk":
        facts = replace(facts, risk=None)
    elif missing == "proof":
        facts = replace(facts, proof_ref=None)
    elif missing == "E1":
        facts = replace(facts, evidence_level="E0")
    elif missing == "error":
        facts = replace(facts, error=None)
    else:
        facts = replace(facts, receipt=facts_for(JobEvent.ACCEPT, job, now).receipt)
    with pytest.raises(ApiError):
        evolve(
            job, JobEvent.UNSENT_REJECT, facts, at=now, actor_id=REQUESTER, requester_id=REQUESTER
        )
