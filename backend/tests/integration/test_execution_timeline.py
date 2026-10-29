"""内部安全时间线真实来源；不修改状态、不发送平台请求。"""

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from yuliang.modules.execution.manual_evidence_models import ManualEvidenceInput
from yuliang.modules.execution.manual_evidence_service import ExecutionManualEvidence
from yuliang.modules.execution.timeline_reads import ExecutionTimelineReads
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.errors import ApiError, ErrorCode

from .test_approved_execution_dispatch import event, runtime, service
from .test_execution_evidence_reads import (
    applications as applications,
)
from .test_execution_evidence_reads import (
    budget_ready as budget_ready,
)
from .test_execution_evidence_reads import (
    capability as capability,
)
from .test_execution_evidence_reads import (
    database as database,
)
from .test_execution_evidence_reads import (
    evidence_ready as evidence_ready,
)
from .test_execution_evidence_reads import (
    governance as governance,
)
from .test_execution_evidence_reads import (
    health_ready as health_ready,
)
from .test_execution_evidence_reads import (
    login_api as login_api,
)
from .test_execution_evidence_reads import (
    oauth_ready as oauth_ready,
)
from .test_execution_evidence_reads import (
    risk_ready as risk_ready,
)
from .test_execution_evidence_reads import (
    sync_ready as sync_ready,
)

pytestmark = pytest.mark.integration


def read(d: dict[str, Any], job: UUID, identity: VerifiedIdentity | None = None) -> Any:
    return ExecutionTimelineReads(d["engine"], identity or d["identity"]).read(job)


def test_actual_creation_is_not_fake_empty_or_current_snapshot_history(
    evidence_ready: dict[str, Any],
) -> None:
    d = evidence_ready
    job = d["applications"].create(d["input"])
    value = read(d, UUID(job.job_id))
    assert len(value.events) == 1 and value.events[0].source_kind == "JOB_EVENT"
    assert value.events[0].job_version == 1
    snapshot = value.current_snapshots[0]
    assert snapshot.source_kind == "JOB" and snapshot.state == "DRAFT" and snapshot.version == 1
    assert snapshot.interpretation == "CURRENT_SNAPSHOT_NOT_HISTORICAL_EVENT"


def test_actual_approval_dispatch_receipt_and_sent_history_preserves_meaning(
    evidence_ready: dict[str, Any],
) -> None:
    d = evidence_ready
    item = event(d)
    calls: list[str] = []
    dispatcher = service(d, runtime(d, calls))
    assert dispatcher.handle(item)["state"] == "RECEIPT_RECORDED"
    value = read(d, item.job_id)
    kinds = {e.source_kind for e in value.events}
    assert {
        "JOB_EVENT",
        "APPROVAL",
        "APPROVAL_DECISION",
        "APPROVED_ORIGIN",
        "DISPATCH",
        "ATTEMPT",
        "RECEIPT",
    } <= kinds
    assert not any(e.evidence_level == "E2" for e in value.events)
    sent = next(e for e in value.events if e.source_kind == "ATTEMPT")
    assert "SENT" in sent.summary and "不代表平台受理" in sent.summary
    assert (
        next(s for s in value.current_snapshots if s.source_kind == "ATTEMPT").state
        == "ACK_ACCEPTED"
    )
    approval = next(e for e in value.events if e.source_kind == "APPROVAL")
    assert approval.job_version is None and approval.warnings == ("HISTORICAL_VERSION_UNKNOWN",)
    assert calls == ["POST"]
    safe = json.dumps(value.model_dump(mode="json"), ensure_ascii=False)
    for forbidden in (
        "payload",
        "wire",
        "headers",
        "token",
        "digest",
        "outcome",
        "note_text",
        "reference_code",
    ):
        assert forbidden not in safe
    assert read(d, item.job_id).model_dump(exclude={"as_of"}) == value.model_dump(exclude={"as_of"})
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE execution_recoveries SET outcome='bearer_token_secret' WHERE job_id=:job"),
            dict(job=item.job_id),
        )
        owner.execute(
            text(
                "DELETE FROM role_permissions WHERE tenant_id=:tenant "
                "AND permission_code<>'snapshot.read'"
            ),
            dict(tenant=d["tenant"]),
        )
        owner.execute(
            text("UPDATE provider_authorizations SET status='REVOKED' WHERE id=:id"),
            dict(id=d["ctx"].authorization_id),
        )
    after = read(d, item.job_id)
    assert after.events == value.events
    assert "bearer_token_secret" not in after.model_dump_json()


def test_actual_manual_notes_never_inherit_receipt_level_and_total_limit_is_explicit(
    evidence_ready: dict[str, Any],
) -> None:
    d = evidence_ready
    item = event(d)
    dispatcher = service(d, runtime(d, []))
    result = dispatcher.handle(item)
    before = read(d, item.job_id)
    receipt = next(e for e in before.events if e.source_kind == "RECEIPT")
    notes = ExecutionManualEvidence(d["engine"], d["identity"])

    def append() -> Any:
        return notes.append(
            item.job_id,
            ManualEvidenceInput(
                request_key=uuid4(),
                attempt_id=UUID(result["attempt_id"]),
                receipt_id=UUID(receipt.source_id),
                note_kind="FOLLOW_UP",
                reference_code="synthetic-limit",
                note_text="Synthetic unverified statement",
            ),
        )

    note = append()
    current = read(d, item.job_id)
    manual = next(e for e in current.events if e.source_kind == "HUMAN_NOTE")
    assert manual.source_id == str(note.id) and manual.evidence_level == "UNVERIFIED_NOTE"
    assert "Synthetic unverified statement" not in current.model_dump_json()
    for _ in range(999):
        append()
    with pytest.raises(ApiError) as raised:
        read(d, item.job_id)
    assert raised.value.code == ErrorCode.DATA_COVERAGE_INSUFFICIENT


@pytest.mark.parametrize("change", ["permission", "scope", "actor", "tenant"])
def test_current_reader_permission_scope_and_owner_rechecked(
    evidence_ready: dict[str, Any], change: str
) -> None:
    d = evidence_ready
    job = UUID(d["applications"].create(d["input"]).job_id)
    identity = d["identity"]
    if change == "actor":
        identity = VerifiedIdentity(d["tenant"], uuid4(), uuid4())
    elif change == "tenant":
        identity = VerifiedIdentity(999999, identity.membership_id, identity.user_id)
    else:
        with d["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            if change == "permission":
                owner.execute(
                    text(
                        "DELETE FROM role_permissions WHERE tenant_id=:tenant "
                        "AND permission_code='snapshot.read'"
                    ),
                    dict(tenant=d["tenant"]),
                )
            else:
                owner.execute(
                    text("DELETE FROM data_scopes WHERE membership_id=:id"),
                    dict(id=identity.membership_id),
                )
    with pytest.raises(ApiError) as raised:
        read(d, job, identity)
    assert raised.value.code in {ErrorCode.FORBIDDEN, ErrorCode.NO_DATA}
