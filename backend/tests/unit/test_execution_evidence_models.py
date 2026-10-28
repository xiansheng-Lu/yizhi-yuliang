from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.execution.evidence_read_models import ExecutionEvidence


def payload() -> dict[str, object]:
    now = datetime.now(UTC)
    return dict(
        job_id=uuid4(),
        job_status="DRAFT",
        job_version=1,
        job_updated_at=now,
        as_of=now,
        orders=[],
        attempts=[],
        receipts=[],
        recoveries=[],
        recovery_evidence=[],
        readbacks=[],
        verified_facts=[],
    )


def test_missing_execution_is_explicit_empty_not_fake_order() -> None:
    result = ExecutionEvidence.model_validate(payload())
    assert not any((result.orders, result.attempts, result.receipts))
    assert "NO_HTTP" in result.warnings[0]


@pytest.mark.parametrize(
    "field", ["payload", "token", "dispatch_secret", "fence", "context_digest"]
)
def test_sensitive_or_unknown_fields_rejected_without_echo(field: str) -> None:
    with pytest.raises(ValidationError) as error:
        ExecutionEvidence.model_validate(payload() | {field: "private-sensitive-marker"})
    assert "private-sensitive-marker" not in str(error.value)


def test_foreign_attempt_never_serialized_as_coherent_snapshot() -> None:
    value = payload() | dict(
        attempts=[
            dict(
                id=uuid4(),
                order_id=uuid4(),
                attempt_no=1,
                state="SENT",
                sent_at=datetime.now(UTC),
                finished_at=None,
                version=1,
            )
        ]
    )
    with pytest.raises(ValidationError):
        ExecutionEvidence.model_validate(value)


def test_collection_over_limit_not_truncated() -> None:
    item = dict(id=uuid4(), action_type="CHANGE_BID", verified_at=datetime.now(UTC))
    with pytest.raises(ValidationError):
        ExecutionEvidence.model_validate(payload() | dict(verified_facts=[item] * 1001))
