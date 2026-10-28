"""只读已有生产者事实：真实PG/Redis、合成平台，不授予跨actor访问。"""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.execution.evidence_read_service import ExecutionEvidenceReads
from yuliang.modules.execution.receipts import ExecutionReceipts
from yuliang.modules.execution.recovery import ExecutionRecovery
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_budget_initialization import budget_ready as budget_ready
from .test_capability_evidence import capability as capability
from .test_execution_attempts import prepare
from .test_execution_receipts import observation
from .test_execution_recovery import event, expire
from .test_identity_database import database as database
from .test_independent_readback import reader
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import risk_ready as risk_ready
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


@pytest.fixture
def evidence_ready(budget_ready: dict[str, Any]) -> dict[str, Any]:
    d = budget_ready
    entity = uuid4()
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO business_entities(id,tenant_id,name,kind)
            VALUES(:id,:tenant,'Synthetic evidence reader entity','ADVERTISER')"""),
            dict(id=entity, tenant=d["tenant"]),
        )
        owner.execute(
            text("UPDATE ad_accounts SET entity_id=:entity WHERE id=:account"),
            dict(entity=entity, account=d["ctx"].account_id),
        )
        owner.execute(
            text("UPDATE shops SET entity_id=:entity WHERE id=:shop"),
            dict(entity=entity, shop=d["shop"]),
        )
    return d | dict(read_entity=entity)


def service(d: dict[str, Any], identity: VerifiedIdentity | None = None) -> ExecutionEvidenceReads:
    return ExecutionEvidenceReads(d["engine"], identity or d["identity"])


async def test_actual_receipt_then_read_only_actor_without_execute_permissions(
    evidence_ready: dict[str, Any],
) -> None:
    d = evidence_ready
    prepared, _, raw = await observation(d)
    result = ExecutionReceipts(d["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    before = service(d).read(prepared.attempt.business.id)
    assert before.receipts[0].id == result.receipt_id and before.receipts[0].accepted is True
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
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
    # 历史读无需平台当前发送资格；仍核本人/当前snapshot.read/双scope。
    after = service(d).read(prepared.attempt.business.id)
    assert after.model_dump(exclude={"as_of"}) == before.model_dump(exclude={"as_of"})
    safe = json.dumps(after.model_dump(mode="json"))
    for forbidden in (
        "raw_id",
        "payload",
        "headers",
        "dispatch",
        "fence",
        "digest",
        "token",
        "wire_projection",
        "platform_request_key",
        "synthetic-token",
    ):
        assert forbidden not in safe


def test_existing_application_without_attempt_is_actual_empty(
    evidence_ready: dict[str, Any],
) -> None:
    d = evidence_ready
    job = d["applications"].create(d["input"])
    result = service(d).read(UUID(job.job_id))
    assert not any((result.orders, result.attempts, result.receipts, result.recoveries))
    assert result.job_status == job.status and result.job_version == job.version


async def test_unknown_receipt_not_inferred_as_success(evidence_ready: dict[str, Any]) -> None:
    d = evidence_ready
    prepared, _, raw = await observation(d, "timeout")
    ExecutionReceipts(d["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    result = service(d).read(prepared.attempt.business.id)
    assert result.job_status == "EXECUTING"
    assert result.receipts[0].source == "SYSTEM" and result.receipts[0].accepted is None
    assert result.verified_facts == ()


def test_actual_recovery_not_sent_proof_read_has_no_guard_secret(
    evidence_ready: dict[str, Any],
) -> None:
    d = evidence_ready
    _, prepared, _ = prepare(d)
    current = event(d, prepared.attempt.attempt_id)
    expire(d)
    outcome = ExecutionRecovery(d["applications"]).inspect(current)
    result = service(d).read(prepared.attempt.business.id)
    assert outcome.outcome == "NOT_SENT" and result.job_status == "FAILED"
    assert result.recoveries[0].state == "DONE"
    assert result.recovery_evidence[0].kind == "NOT_SENT"
    assert result.recovery_evidence[0].evidence_level == "E1"
    assert result.recovery_evidence[0].network_attempts == 0


async def test_actual_independent_readback_transport_not_e2(
    evidence_ready: dict[str, Any],
    governance: Any,
) -> None:
    from pydantic import SecretStr

    d = evidence_ready
    prepared, _, raw = await observation(d)
    ExecutionReceipts(d["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    port, calls = reader(d, governance)
    observed = await port.inspect(
        prepared.attempt.attempt_id, d["readctx"], SecretStr("synthetic"), uuid4()
    )
    result = service(d).read(prepared.attempt.business.id)
    assert calls == ["GET"] and observed.comparison.status == "MATCHED"
    assert len(result.readbacks) == 1 and result.readbacks[0].external_outcome == "READ_SUCCEEDED"
    assert result.verified_facts == () and result.job_status == "VERIFYING"


@pytest.mark.parametrize("change", ["permission", "scope", "member", "link", "account", "entity"])
def test_current_read_authorization_and_object_scope_rechecked(
    evidence_ready: dict[str, Any],
    change: str,
) -> None:
    d = evidence_ready
    _, prepared, _ = prepare(d)
    assert service(d).read(prepared.attempt.business.id).attempts
    if change == "link":
        # 历史任务要求保留原关联；真实FK先阻断硬删，不为故障注入放宽约束。
        with pytest.raises(DBAPIError) as blocked, d["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(
                text("DELETE FROM entity_links WHERE shop_id=:shop AND account_id=:account"),
                dict(shop=d["shop"], account=d["ctx"].account_id),
            )
        assert getattr(blocked.value.orig, "sqlstate", None) == "23503"
        assert service(d).read(prepared.attempt.business.id).attempts
    commands = {
        "permission": "DELETE FROM role_permissions WHERE tenant_id=:tenant "
        "AND permission_code='snapshot.read'",
        "scope": "UPDATE data_scopes SET permissions_mask=ARRAY['task.create'] "
        "WHERE membership_id=:member",
        "member": "UPDATE memberships SET status='REMOVED' WHERE id=:member",
        "link": "UPDATE shops SET deleted_at=clock_timestamp() WHERE id=:shop",
        "account": "UPDATE ad_accounts SET deleted_at=clock_timestamp() WHERE id=:account",
        "entity": "UPDATE business_entities SET status='DISABLED' WHERE id=:entity",
    }
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(commands[change]),
            dict(
                tenant=d["tenant"],
                member=d["identity"].membership_id,
                shop=d["shop"],
                account=d["ctx"].account_id,
                entity=d["read_entity"],
            ),
        )
    with pytest.raises(ApiError) as error:
        service(d).read(prepared.attempt.business.id)
    assert error.value.code == ErrorCode.FORBIDDEN


def test_same_tenant_other_actor_or_spoofed_member_cannot_read(
    evidence_ready: dict[str, Any],
) -> None:
    d = evidence_ready
    _, prepared, _ = prepare(d)
    for identity in (
        VerifiedIdentity(d["tenant"], d["approver_member"], d["approver"]),
        VerifiedIdentity(d["tenant"], d["approver_member"], d["identity"].user_id),
        VerifiedIdentity(d["tenant"] + 1, d["identity"].membership_id, d["identity"].user_id),
    ):
        with pytest.raises(ApiError) as error:
            service(d, identity).read(prepared.attempt.business.id)
        assert error.value.code in {ErrorCode.NO_DATA, ErrorCode.FORBIDDEN}


async def test_repeatable_snapshot_during_real_receipt_commit(
    evidence_ready: dict[str, Any],
) -> None:
    d = evidence_ready
    prepared, _, raw = await observation(d)
    gate = Barrier(2)

    def record() -> None:
        gate.wait(timeout=10)
        ExecutionReceipts(d["applications"]).record(prepared.attempt.attempt_id, raw, 1)

    engine = d["engine"].execution_options(
        isolation_level="REPEATABLE READ", postgresql_readonly=True
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        with business_transaction(engine, d["identity"]) as session:
            params = dict(
                member=d["identity"].membership_id,
                user=d["identity"].user_id,
                job=prepared.attempt.business.id,
            )
            sql = text("SELECT access.execution_evidence_read(:member,:user,:job)")
            first = session.execute(sql, params).scalar_one()
            future = pool.submit(record)
            gate.wait(timeout=10)
            future.result(timeout=10)
            second = session.execute(sql, params).scalar_one()
            assert first["job_status"] == second["job_status"] == "EXECUTING"
            assert first["receipts"] == second["receipts"] == []
    fresh = service(d).read(prepared.attempt.business.id)
    assert fresh.job_status == "VERIFYING" and len(fresh.receipts) == 1


def test_direct_function_is_bound_to_current_actor_and_readonly(
    evidence_ready: dict[str, Any],
) -> None:
    d = evidence_ready
    _, prepared, _ = prepare(d)
    with (
        pytest.raises(DBAPIError) as error,
        business_transaction(d["engine"], d["identity"]) as session,
    ):
        session.execute(
            text("SELECT access.execution_evidence_read(:member,:user,:job)"),
            dict(member=d["approver_member"], user=d["approver"], job=prepared.attempt.business.id),
        )
    assert getattr(error.value.orig, "sqlstate", None) == "42501"
    before = service(d).read(prepared.attempt.business.id)
    after = service(d).read(prepared.attempt.business.id)
    assert before.model_dump(exclude={"as_of"}) == after.model_dump(exclude={"as_of"})


def test_unknown_recovery_outcome_never_leaks_free_text(evidence_ready: dict[str, Any]) -> None:
    d = evidence_ready
    _, prepared, _ = prepare(d)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE execution_recoveries SET outcome=:value WHERE job_id=:job"),
            dict(value="token=private-sensitive-marker", job=prepared.attempt.business.id),
        )
    result = service(d).read(prepared.attempt.business.id)
    assert result.recoveries[0].outcome == "UNRECOGNIZED"
    assert "UNRECOGNIZED_RECOVERY_OUTCOME_REDACTED" in result.warnings
    assert "private-sensitive-marker" not in result.model_dump_json()


def test_incomplete_historical_shape_is_safe_coverage_error(
    evidence_ready: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yuliang.modules.execution.evidence_read_models import ExecutionEvidence

    d = evidence_ready
    _, prepared, _ = prepare(d)

    def invalid(value: Any) -> Any:
        from pydantic import ValidationError

        raise ValidationError.from_exception_data("private-sensitive-marker", [])

    monkeypatch.setattr(ExecutionEvidence, "model_validate", invalid)
    with pytest.raises(ApiError) as error:
        service(d).read(prepared.attempt.business.id)
    assert error.value.code == ErrorCode.DATA_COVERAGE_INSUFFICIENT
    assert "private-sensitive-marker" not in str(error.value)
