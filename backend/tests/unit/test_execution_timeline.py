from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.execution.timeline_models import ExecutionTimeline, TimelineEvent


def event(**changes: object) -> dict[str, object]:
    return (
        dict(
            source_kind="ATTEMPT", source_id=str(uuid4()), at=datetime.now(UTC), evidence_level="E0"
        )
        | changes
    )


def test_sent_is_local_registration_and_not_platform_acceptance() -> None:
    value = TimelineEvent.model_validate(event())
    assert "不代表平台受理" in value.summary
    assert value.job_version is None
    with pytest.raises(ValidationError):
        TimelineEvent.model_validate(event(evidence_level="E2"))


@pytest.mark.parametrize("kind", ["APPROVAL", "DISPATCH"])
def test_mutable_rows_cannot_guess_historical_version(kind: str) -> None:
    valid = event(source_kind=kind, warnings=["HISTORICAL_VERSION_UNKNOWN"])
    assert TimelineEvent.model_validate(valid).job_version is None
    with pytest.raises(ValidationError):
        TimelineEvent.model_validate(valid | dict(job_version=1))


def test_manual_note_cannot_inherit_receipt_proof_or_inject_body() -> None:
    valid = event(source_kind="HUMAN_NOTE", evidence_level="UNVERIFIED_NOTE", note_kind="FOLLOW_UP")
    assert TimelineEvent.model_validate(valid).evidence_level == "UNVERIFIED_NOTE"
    for change in (
        dict(evidence_level="E1"),
        dict(summary="bearer secret"),
        dict(note_text="secret"),
    ):
        with pytest.raises(ValidationError):
            TimelineEvent.model_validate(valid | change)


def test_timeline_rejects_duplicate_sources() -> None:
    value = event()
    with pytest.raises(ValidationError):
        ExecutionTimeline.model_validate(
            dict(
                job_id=str(uuid4()),
                as_of=datetime.now(UTC),
                events=[value, value],
                current_snapshots=[],
                warnings=[],
            )
        )
