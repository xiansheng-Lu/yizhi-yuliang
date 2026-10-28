from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.execution.approval_outbox import ApprovedEvent


def event() -> ApprovedEvent:
    return ApprovedEvent(
        tenant_id=1,
        event_id=uuid4(),
        job_id=uuid4(),
        approved_job_version=3,
        decision_id=uuid4(),
        approval_id=uuid4(),
        subject_user_id=uuid4(),
        subject_membership_id=uuid4(),
    )


def test_original_subject_is_only_identity_and_envelope_is_five_ids() -> None:
    source = event()
    assert source.identity.user_id == source.subject_user_id
    assert source.identity.membership_id == source.subject_membership_id
    assert set(source.message().model_dump()) == {
        "tenant_id",
        "event_id",
        "job_id",
        "membership_id",
        "user_id",
    }
    assert source.message().user_id == source.subject_user_id


@pytest.mark.parametrize("field", ["token", "payload", "reviewer_user_id"])
def test_unknown_fields_rejected_without_sensitive_input(field: str) -> None:
    with pytest.raises(ValidationError) as error:
        ApprovedEvent.model_validate(event().model_dump() | {field: "private-sensitive-marker"})
    assert "private-sensitive-marker" not in str(error.value)
