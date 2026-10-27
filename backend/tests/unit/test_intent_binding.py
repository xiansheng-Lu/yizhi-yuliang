"""冻结语义变化与生命周期元数据变化具有不同结果。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from state_cases import state_job

from yuliang.modules.command.jobs import JobStatus
from yuliang.modules.policy.intent import seal_intent


def test_normal_version_and_risk_time_do_not_change_original_intent() -> None:
    at = datetime.now(UTC)
    job = state_job(JobStatus.DRAFT, at)
    requester = uuid4()
    key = SecretStr("synthetic-intent-unit-secret-more-than-32-chars")
    proof = {"sourceId": str(uuid4()), "fetchedAt": at.isoformat()}
    updated = job.model_copy(
        update={
            "version": 4,
            "updated_at": at + timedelta(seconds=2),
            "risk_summary": job.risk_summary.model_copy(update={"checked_at": at}),
        }
    )
    assert seal_intent(key, job, requester, proof) == seal_intent(key, updated, requester, proof)


@pytest.mark.parametrize("field", ["intent", "expires_at", "job_id", "policy", "proof", "actor"])
def test_semantic_or_origin_change_breaks_seal(field: str) -> None:
    job = state_job(JobStatus.DRAFT, datetime.now(UTC))
    original = job
    actor = requester = uuid4()
    proof = {"sourceId": str(uuid4())}
    original_proof = dict(proof)
    key = SecretStr("synthetic-intent-unit-secret-more-than-32-chars")
    if field == "policy":
        job = job.model_copy(
            update={"risk_summary": job.risk_summary.model_copy(update={"policy_version": 2})}
        )
    elif field == "proof":
        proof = {"sourceId": str(uuid4())}
    elif field == "actor":
        actor = uuid4()
    else:
        value = (
            "改动后的意图"
            if field == "intent"
            else job.expires_at + timedelta(seconds=1)
            if field == "expires_at"
            else str(uuid4())
        )
        job = job.model_copy(update={field: value})
    assert seal_intent(key, original, requester, original_proof) != seal_intent(
        key, job, actor, proof
    )
