"""真实执行/回执/恢复事实上的人工陈述；不变更业务/资金/隔离。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.execution.manual_evidence_models import ManualEvidenceInput
from yuliang.modules.execution.manual_evidence_service import ExecutionManualEvidence
from yuliang.modules.execution.receipts import ExecutionReceipts
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_budget_initialization import budget_ready as budget_ready
from .test_capability_evidence import capability as capability
from .test_execution_evidence_reads import evidence_ready as evidence_ready
from .test_execution_receipts import observation
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import risk_ready as risk_ready
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


def service(d: dict[str, Any], identity: VerifiedIdentity | None = None) -> ExecutionManualEvidence:
    return ExecutionManualEvidence(d["engine"], identity or d["identity"])


async def inputs(d: dict[str, Any], mode: str = "timeout") -> tuple[UUID, ManualEvidenceInput]:
    prepared, _, raw = await observation(d, mode)
    receipt = ExecutionReceipts(d["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    with business_transaction(d["engine"], d["identity"]) as session:
        recovery = session.execute(
            text("SELECT id FROM execution_recoveries WHERE attempt_id=:attempt"),
            dict(attempt=prepared.attempt.attempt_id),
        ).scalar_one()
    return prepared.attempt.business.id, ManualEvidenceInput(
        request_key=uuid4(),
        attempt_id=prepared.attempt.attempt_id,
        receipt_id=receipt.receipt_id,
        recovery_id=recovery,
        note_kind="FOLLOW_UP",
        reference_code="support-case-42",
        note_text="人工核对陈述，尚无平台核验结论。",
    )


def facts(d: dict[str, Any], job: UUID) -> dict[str, Any]:
    """仅测试合成数据；比较安全列，不输出原payload/凭据。"""
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        queries = {
            "job": "SELECT status,version,updated_at FROM command_jobs WHERE id=:job",
            "attempt": "SELECT a.id,a.state,a.version FROM execution_attempts a "
            "JOIN execution_orders o "
            "ON (o.tenant_id,o.id)=(a.tenant_id,a.order_id) WHERE o.command_job_id=:job",
            "guard": "SELECT state,fence,lease_until FROM target_execution_guards "
            "WHERE job_id=:job",
            "recovery": "SELECT state,outcome,generation,next_due,checks,current_event_id "
            "FROM execution_recoveries WHERE job_id=:job",
            "holds": "SELECT state FROM job_budget_holds WHERE job_id=:job",
            "reserved": "SELECT state,amount_minor FROM budget_reservations WHERE job_id=:job",
            "ledger": "SELECT confirmed_total_minor,reserved_increase_minor "
            "FROM budget_ledgers WHERE tenant_id=:tenant",
            "receipts": "SELECT r.id,r.source,r.accepted,r.evidence_level "
            "FROM execution_receipts r JOIN execution_orders o "
            "ON (o.tenant_id,o.id)=(r.tenant_id,r.order_id) WHERE o.command_job_id=:job",
            "finalizations": "SELECT id FROM budget_job_finalizations WHERE job_id=:job",
            "verified": "SELECT id FROM verified_action_facts WHERE job_id=:job",
            "outbox": "SELECT event_id,state,attempts FROM outbox_events "
            "WHERE tenant_id=:tenant ORDER BY id",
        }
        return {
            key: owner.execute(text(sql), dict(job=job, tenant=d["tenant"])).all()
            for key, sql in queries.items()
        }


@pytest.mark.parametrize("mode", ["accepted", "timeout"])
async def test_actual_receipt_reference_stays_human_and_all_facts_unchanged(
    evidence_ready: dict[str, Any],
    mode: str,
) -> None:
    d = evidence_ready
    job, value = await inputs(d, mode)
    before = facts(d, job)
    note = service(d).append(job, value)
    assert note.source == "HUMAN_STATEMENT" and note.evidence_level == "UNVERIFIED_NOTE"
    assert note.receipt_id == value.receipt_id and service(d).list(job) == (note,)
    assert facts(d, job) == before
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        rows = owner.execute(
            text(
                "SELECT action,object_id,detail FROM audit_logs "
                "WHERE tenant_id=:tenant AND action='execution.manual_note'"
            ),
            dict(tenant=d["tenant"]),
        ).all()
        assert len(rows) == 1 and rows[0].object_id == str(note.id)
        assert value.note_text not in str(rows)


async def test_same_key_concurrent_once_changed_payload_conflicts(
    evidence_ready: dict[str, Any],
) -> None:
    d = evidence_ready
    job, value = await inputs(d)
    gate = Barrier(2)

    def append() -> Any:
        gate.wait(timeout=10)
        return service(d).append(job, value)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(append) for _ in range(2)]
        notes = [f.result(timeout=15) for f in futures]
    assert notes[0] == notes[1] and service(d).list(job) == (notes[0],)
    assert service(d).append(job, value) == notes[0]
    with pytest.raises(ApiError) as error:
        service(d).append(job, value.model_copy(update=dict(note_text="另一份不同陈述")))
    assert error.value.code == ErrorCode.IDEMPOTENCY_MISMATCH


async def test_metadata_write_does_not_require_plan_write_but_read_is_separate(
    evidence_ready: dict[str, Any],
) -> None:
    d = evidence_ready
    job, value = await inputs(d)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "DELETE FROM role_permissions WHERE tenant_id=:tenant AND "
                "permission_code NOT IN ('task.create','snapshot.read')"
            ),
            dict(tenant=d["tenant"]),
        )
    note = service(d).append(job, value)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "DELETE FROM role_permissions WHERE tenant_id=:tenant "
                "AND permission_code='task.create'"
            ),
            dict(tenant=d["tenant"]),
        )
    assert service(d).list(job) == (note,)
    with pytest.raises(ApiError) as error:
        service(d).append(job, value.model_copy(update=dict(request_key=uuid4())))
    assert error.value.code == ErrorCode.FORBIDDEN


@pytest.mark.parametrize("change", ["read_permission", "scope", "member", "shop"])
async def test_current_read_scope_and_write_scope_do_not_expand(
    evidence_ready: dict[str, Any],
    change: str,
) -> None:
    d = evidence_ready
    job, value = await inputs(d)
    service(d).append(job, value)
    commands = {
        "read_permission": "DELETE FROM role_permissions WHERE tenant_id=:tenant "
        "AND permission_code='snapshot.read'",
        "scope": "UPDATE data_scopes SET permissions_mask=ARRAY['overview.read'] "
        "WHERE membership_id=:member",
        "member": "UPDATE memberships SET status='REMOVED' WHERE id=:member",
        "shop": "UPDATE shops SET deleted_at=clock_timestamp() WHERE id=:shop",
    }
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(commands[change]),
            dict(tenant=d["tenant"], member=d["identity"].membership_id, shop=d["shop"]),
        )
    with pytest.raises(ApiError):
        service(d).list(job)
    if change != "read_permission":
        with pytest.raises(ApiError):
            service(d).append(job, value.model_copy(update=dict(request_key=uuid4())))


async def test_other_actor_and_cross_tenant_cannot_append_or_read(
    evidence_ready: dict[str, Any],
) -> None:
    d = evidence_ready
    job, value = await inputs(d)
    service(d).append(job, value)
    for identity in (
        VerifiedIdentity(d["tenant"], d["approver_member"], d["approver"]),
        VerifiedIdentity(d["tenant"] + 1, d["identity"].membership_id, d["identity"].user_id),
    ):
        with pytest.raises(ApiError):
            service(d, identity).append(job, value)
        with pytest.raises(ApiError):
            service(d, identity).list(job)


@pytest.mark.parametrize("field", ["attempt_id", "receipt_id", "recovery_id"])
async def test_unknown_actual_reference_rejected(
    evidence_ready: dict[str, Any], field: str
) -> None:
    d = evidence_ready
    job, value = await inputs(d)
    with pytest.raises(ApiError) as error:
        service(d).append(job, value.model_copy(update={field: uuid4()}))
    assert error.value.code == ErrorCode.FORBIDDEN and service(d).list(job) == ()


@pytest.mark.parametrize(
    "text_value", ["Bearer private-sensitive-marker", "Ｂｅａｒｅｒ private-sensitive-marker"]
)
async def test_direct_sql_cannot_bypass_known_secret_or_shape_guard(
    evidence_ready: dict[str, Any],
    text_value: str,
) -> None:
    d = evidence_ready
    job, value = await inputs(d)
    payload = value.model_dump(mode="json") | dict(note_text=text_value)
    import json

    with (
        pytest.raises(DBAPIError) as error,
        business_transaction(d["engine"], d["identity"]) as session,
    ):
        session.execute(
            text(
                "SELECT access.execution_manual_note_append(:member,:user,:job,"
                "CAST(:input AS jsonb),:request)"
            ),
            dict(
                member=d["identity"].membership_id,
                user=d["identity"].user_id,
                job=job,
                input=json.dumps(payload),
                request=uuid4(),
            ),
        )
    assert getattr(error.value.orig, "sqlstate", None) == "22023"
    assert service(d).list(job) == ()


async def test_runtime_cannot_update_delete_or_direct_insert_notes(
    evidence_ready: dict[str, Any],
) -> None:
    d = evidence_ready
    job, value = await inputs(d)
    note = service(d).append(job, value)
    for sql in (
        "UPDATE execution_manual_notes SET note_text='changed' WHERE id=:id",
        "DELETE FROM execution_manual_notes WHERE id=:id",
    ):
        with (
            pytest.raises(DBAPIError) as error,
            business_transaction(d["engine"], d["identity"]) as session,
        ):
            session.execute(text(sql), dict(id=note.id))
        assert getattr(error.value.orig, "sqlstate", None) == "42501"
    with pytest.raises(DBAPIError) as error, d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE execution_manual_notes SET note_text='changed' WHERE id=:id"),
            dict(id=note.id),
        )
    assert getattr(error.value.orig, "sqlstate", None) == "22023"


async def test_audit_failure_rolls_back_note_and_key(evidence_ready: dict[str, Any]) -> None:
    d = evidence_ready
    job, value = await inputs(d)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""CREATE FUNCTION public.synthetic_note_audit_fail() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='execution.manual_note' THEN
            RAISE EXCEPTION 'synthetic audit unavailable'; END IF; RETURN NEW; END $$""")
        )
        owner.execute(
            text(
                "CREATE TRIGGER synthetic_note_audit_fail BEFORE INSERT ON audit_logs "
                "FOR EACH ROW EXECUTE FUNCTION public.synthetic_note_audit_fail()"
            )
        )
    try:
        with pytest.raises(DBAPIError):
            service(d).append(job, value)
        assert service(d).list(job) == ()
    finally:
        with d["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(text("DROP TRIGGER synthetic_note_audit_fail ON audit_logs"))
            owner.execute(text("DROP FUNCTION public.synthetic_note_audit_fail()"))
    assert service(d).append(job, value).attempt_id == value.attempt_id
