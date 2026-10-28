"""V1十二态与V8十态单一事实投影；显式合成状态不作为供应商验收。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from state_cases import state_job

from yuliang.modules.command.jobs import CommandJob, JobError, JobReceipt, JobStatus
from yuliang.modules.command.projections import JobProjection, StoredJobEvent, project_job
from yuliang.modules.delivery.projections import ExecutionTask, execution_view


def projection(job: CommandJob, *, execution_status: str | None = None) -> JobProjection:
    return project_job(
        job.model_dump(mode="json"),
        tuple(StoredJobEvent(status=e.status, at=e.at, detail=e.detail) for e in job.history),
        execution_status=execution_status,
    )


@pytest.mark.parametrize("status", list(JobStatus))
def test_all_twelve_public_states_keep_exact_fields(status: JobStatus) -> None:
    job = state_job(status, datetime.now(UTC))
    result = projection(job)
    assert result.job.model_dump(mode="json") == job.model_dump(mode="json")
    assert len(JobStatus) == 12 and "UNKNOWN" not in JobStatus.__members__
    view = execution_view(result, platform="adq")
    if status == JobStatus.MANUAL_REQUIRED:
        assert view is None
    else:
        assert view is not None and view.legacy_job_id == job.job_id
        wire = view.model_dump(mode="json", by_alias=True)
        assert "requestStatus" in wire and "safeRetry" in wire and "account_id" not in wire
        assert ExecutionTask.model_validate(wire) == view


@pytest.mark.parametrize("accepted", [False, True])
def test_unknown_has_two_v1_stages_and_one_v8_status_without_fake_receipt(accepted: bool) -> None:
    now = datetime.now(UTC)
    job = state_job(JobStatus.VERIFYING if accepted else JobStatus.EXECUTING, now)
    result = projection(job, execution_status="UNKNOWN")
    assert result.job.status == job.status and result.verification == "UNKNOWN"
    view = execution_view(result, platform="adq")
    assert view is not None and view.status == "UNKNOWN" and not view.safe_retry
    assert result.confirmed_change is None and view.history[-1].status == "UNKNOWN"
    if accepted:
        assert result.job.execution_receipt is not None
        assert result.job.execution_receipt.accepted is True
        assert result.job.execution_receipt.verification == "UNKNOWN" and result.job.error is None
    else:
        assert result.job.execution_receipt is None and view.response is None
        assert result.job.error is not None and result.job.error.code == "RESULT_UNKNOWN"
        assert not result.job.error.retryable
    assert result.job.model_dump(mode="json") != job.model_dump(mode="json")
    assert job.execution_receipt is None or job.execution_receipt.verification == "PENDING"


def test_explicit_platform_rejection_unknown_verification_does_not_hide_failure() -> None:
    now = datetime.now(UTC)
    job = state_job(JobStatus.FAILED, now)
    rejected = JobReceipt(
        receipt_id="synthetic-rejection",
        platform_receipt_id=None,
        accepted=False,
        received_at=now,
        verification="UNKNOWN",
        observed_value=None,
        verified_at=None,
    )
    result = projection(
        CommandJob.model_validate(job.model_dump() | {"execution_receipt": rejected})
    )
    view = execution_view(result, platform="adq")
    assert not result.unknown and view is not None and view.status == "FAILED"


def test_approval_and_acceptance_do_not_project_effective_change() -> None:
    for status in (JobStatus.APPROVED, JobStatus.VERIFYING):
        result = projection(state_job(status, datetime.now(UTC)))
        view = execution_view(result, platform="adq")
        assert result.confirmed_change is None and view is not None
        assert view.status in {"PENDING", "VERIFYING"}
    success = projection(state_job(JobStatus.SUCCEEDED, datetime.now(UTC)))
    assert success.confirmed_change == success.job.new_value
    effective = execution_view(success, platform="adq")
    assert effective is not None and effective.status == "EFFECTIVE"


@pytest.mark.parametrize("mutation", ["source", "accepted", "verification", "value", "handoff"])
def test_success_requires_exact_platform_match_and_handoff_has_no_receipt(mutation: str) -> None:
    job = state_job(JobStatus.SUCCEEDED, datetime.now(UTC)).model_dump(mode="json")
    if mutation == "source":
        job["execution_receipt"]["source"] = "SYSTEM"
    elif mutation == "accepted":
        job["execution_receipt"]["accepted"] = False
    elif mutation == "verification":
        job["execution_receipt"]["verification"] = "PENDING"
    elif mutation == "value":
        job["execution_receipt"]["observed_value"]["value"] = True
    else:
        manual = state_job(JobStatus.MANUAL_REQUIRED, datetime.now(UTC)).model_dump(mode="json")
        manual["execution_receipt"] = job["execution_receipt"]
        job = manual
    with pytest.raises(ValidationError):
        CommandJob.model_validate(job)


def test_unknown_cannot_relabel_terminal_or_clear_accepted_phase() -> None:
    with pytest.raises(ValueError):
        projection(state_job(JobStatus.SUCCEEDED, datetime.now(UTC)), execution_status="UNKNOWN")
    job = state_job(JobStatus.VERIFYING, datetime.now(UTC))
    invalid = job.model_dump() | {
        "execution_receipt": None,
        "error": JobError(
            code="RESULT_UNKNOWN", message="synthetic", retryable=False, at=job.updated_at
        ),
    }
    with pytest.raises(ValueError):
        projection(CommandJob.model_validate(invalid), execution_status="UNKNOWN")
