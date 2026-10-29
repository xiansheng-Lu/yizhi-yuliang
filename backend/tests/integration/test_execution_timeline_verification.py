"""独立SQL核对真实生产者时间线；不是前端合同或人工/平台验收。"""

import json
from collections import Counter
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.modules.execution.attempt_models import BusinessKind, BusinessRef
from yuliang.modules.execution.attempts import ExecutionOrders, PreparedExecution
from yuliang.modules.execution.manual_evidence_models import ManualEvidenceInput
from yuliang.modules.execution.manual_evidence_service import ExecutionManualEvidence
from yuliang.modules.execution.recovery import ExecutionRecovery
from yuliang.modules.execution.settlement import BudgetSettlement
from yuliang.modules.execution.timeline_reads import ExecutionTimelineReads
from yuliang.platform.db import business_transaction

from .test_approved_execution_dispatch import event, runtime, service
from .test_budget_settlement import independent_match
from .test_execution_attempts import SyntheticBudgetSerializer
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
from .test_execution_recovery import expire
from .test_independent_readback import reader

pytestmark = pytest.mark.integration


def oracle(data: dict[str, Any], job: UUID) -> list[tuple[str, str, Any]]:
    """直接查源表，不调用时间线/证据读取SQL函数或复制其JSON投影。"""
    sources = {
        "JOB_EVENT": "SELECT id::text,created_at FROM job_events WHERE job_id=:job",
        "APPROVAL": "SELECT id::text,created_at FROM approvals WHERE command_job_id=:job",
        "APPROVAL_DECISION": """SELECT id::text,decided_at FROM approval_decisions
          WHERE command_job_id=:job""",
        "ATTEMPT": """SELECT a.id::text,a.sent_at FROM execution_attempts a
          JOIN execution_orders o ON (o.tenant_id,o.id)=(a.tenant_id,a.order_id)
          WHERE o.command_job_id=:job""",
        "RECEIPT": """SELECT r.id::text,r.created_at FROM execution_receipts r
          JOIN execution_orders o ON (o.tenant_id,o.id)=(r.tenant_id,r.order_id)
          WHERE o.command_job_id=:job""",
        "RECOVERY_EVIDENCE": """SELECT id::text,created_at FROM execution_recovery_evidence
          WHERE job_id=:job""",
        "READ_TRANSPORT": """SELECT r.id::text,r.created_at FROM platform_raw_payloads r
          WHERE r.phase IN ('RESPONSE','ERROR') AND r.outbound_id IN (
          SELECT p.id FROM platform_requests p JOIN execution_orders o
           ON (o.tenant_id,o.id)=(p.tenant_id,p.execution_id)
          WHERE o.command_job_id=:job AND p.effect='READ' AND p.method='GET')""",
        "VERIFIED_FACT": "SELECT id::text,verified_at FROM verified_action_facts WHERE job_id=:job",
        "APPROVED_ORIGIN": """SELECT event_id::text,created_at FROM approval_execution_events
          WHERE job_id=:job""",
        "DISPATCH": """SELECT event_id::text,created_at FROM approved_execution_dispatches
          WHERE job_id=:job""",
        "HUMAN_NOTE": "SELECT id::text,created_at FROM execution_manual_notes WHERE job_id=:job",
    }
    rows = []
    with data["maintenance"].begin() as session:
        session.execute(text("SET LOCAL ROLE app_owner"))
        for kind, sql in sources.items():
            for identifier, at in session.execute(text(sql), dict(job=job)):
                rows.append((kind, identifier, at))
    return sorted(rows, key=lambda item: (item[2], item[0], item[1]))


def unchanged_state(data: dict[str, Any]) -> list[Any]:
    queries = [
        "SELECT id,status,version,document FROM command_jobs ORDER BY id",
        "SELECT id,status,version,current_attempt FROM execution_orders ORDER BY id",
        "SELECT id,state,version,dispatch_consumed_at FROM execution_attempts ORDER BY id",
        "SELECT id,confirmed_total_minor,reserved_increase_minor,data_state "
        "FROM budget_ledgers ORDER BY id",
        "SELECT job_id,state FROM job_budget_holds ORDER BY job_id",
    ]
    with business_transaction(data["engine"], data["identity"]) as session:
        return [list(session.execute(text(sql)).tuples()) for sql in queries]


def assert_oracle(data: dict[str, Any], job: UUID) -> Any:
    before = unchanged_state(data)
    service = ExecutionTimelineReads(data["engine"], data["identity"])
    first, second = service.read(job), service.read(job)
    actual = [(row.source_kind, row.source_id, row.at) for row in first.events]
    assert actual == oracle(data, job)
    assert first.model_dump(exclude={"as_of"}) == second.model_dump(exclude={"as_of"})
    assert unchanged_state(data) == before
    serialized = json.dumps(first.model_dump(mode="json"))
    for forbidden in (
        "note-private-marker",
        "support-private-marker",
        "wire_projection",
        "dispatch_secret",
        "payload_digest",
        "access_token",
        "Bearer ",
    ):
        assert forbidden not in serialized
    with data["maintenance"].begin() as session:
        session.execute(text("SET LOCAL ROLE app_owner"))
        versions = dict(
            session.execute(
                text("SELECT id::text,job_version FROM job_events WHERE job_id=:job"), dict(job=job)
            ).tuples().all()
        )
    for row in first.events:
        if row.source_kind == "JOB_EVENT":
            assert row.job_version == versions[row.source_id]
            assert row.source_version == versions[row.source_id]
        if row.source_kind in {"APPROVAL", "DISPATCH"}:
            assert row.job_version is None and "HISTORICAL_VERSION_UNKNOWN" in row.warnings
            assert row.source_version is None
        if row.source_kind in {"ATTEMPT", "READ_TRANSPORT"}:
            assert row.evidence_level == "E0"
    return first


def test_independent_sql_real_approval_dispatch_receipt_recovery_and_human_note(
    evidence_ready: dict[str, Any],
) -> None:
    data = evidence_ready
    item = event(data)
    calls: list[str] = []
    result = service(data, runtime(data, calls)).handle(item)
    assert result["state"] == "RECEIPT_RECORDED" and calls == ["POST"]
    with business_transaction(data["engine"], data["identity"]) as session:
        receipt = session.execute(text("SELECT id FROM execution_receipts")).scalar_one()
        attempt = session.execute(text("SELECT id FROM execution_attempts")).scalar_one()
    expire(data)
    recovery = ExecutionRecovery(data["applications"])
    observed = recovery.inspect(recovery.for_job(item.job_id).next_event_id)
    before = unchanged_state(data)
    note = ExecutionManualEvidence(data["engine"], data["identity"]).append(
        item.job_id,
        ManualEvidenceInput(
            request_key=uuid4(),
            attempt_id=attempt,
            receipt_id=receipt,
            recovery_id=observed.recovery_id,
            note_kind="FOLLOW_UP",
            reference_code="support-private-marker",
            note_text="note-private-marker",
        ),
    )
    assert unchanged_state(data) == before
    timeline = assert_oracle(data, item.job_id)
    kinds = Counter(row.source_kind for row in timeline.events)
    assert all(
        kinds[k]
        for k in (
            "JOB_EVENT",
            "APPROVAL",
            "APPROVAL_DECISION",
            "APPROVED_ORIGIN",
            "ATTEMPT",
            "DISPATCH",
            "RECEIPT",
            "RECOVERY_EVIDENCE",
            "HUMAN_NOTE",
        )
    )
    human = next(row for row in timeline.events if row.source_id == str(note.id))
    assert human.source_kind == "HUMAN_NOTE" and human.evidence_level == "UNVERIFIED_NOTE"
    assert not any(row.evidence_level == "E2" for row in timeline.events)


async def test_independent_sql_maintenance_verified_fact_then_actual_settlement_e2(
    evidence_ready: dict[str, Any],
) -> None:
    """维护验证入口是真实技术输入；不是尚未实现的正式M2-17b Writer。"""
    import asyncio

    data = evidence_ready
    item = event(data)
    await asyncio.to_thread(service(data, runtime(data, [])).handle, item)
    ticket = ExecutionOrders(data["applications"]).lookup(
        BusinessRef(kind=BusinessKind.COMMAND_JOB, id=item.job_id)
    )
    fact = await independent_match(data, PreparedExecution(attempt=ticket), 110)
    before = data["applications"].get(item.job_id)
    BudgetSettlement(data["applications"]).match(fact, before.version)
    timeline = assert_oracle(data, item.job_id)
    verified = [row for row in timeline.events if row.source_kind == "VERIFIED_FACT"]
    assert len(verified) == 1 and verified[0].source_id == str(fact)
    assert verified[0].evidence_level == "E2"
    assert all(row.evidence_level != "E2" for row in timeline.events if row not in verified)


def test_independent_sql_sent_registration_is_not_wire_or_acceptance(
    evidence_ready: dict[str, Any],
) -> None:
    data = evidence_ready
    item = event(data)
    prepared = ExecutionOrders(
        data["applications"], SyntheticBudgetSerializer(data["spec"])
    ).prepare(
        BusinessRef(kind=BusinessKind.COMMAND_JOB, id=item.job_id), data["ctx"], 3, 0, uuid4()
    )
    assert prepared.attempt.attempt_state == "SENT"
    timeline = assert_oracle(data, item.job_id)
    assert any(row.source_kind == "ATTEMPT" for row in timeline.events)
    assert not any(
        row.source_kind in {"RECEIPT", "READ_TRANSPORT", "VERIFIED_FACT"} for row in timeline.events
    )
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM platform_requests WHERE effect='WRITE'")
            ).scalar_one()
            == 0
        )


async def test_independent_sql_read_transport_match_does_not_become_e2(
    evidence_ready: dict[str, Any],
    governance: Any,
) -> None:
    data = evidence_ready
    item = event(data)
    # 同步dispatch使用asyncio.run；仅此准备阶段在独立线程运行，不改生产发送边界。
    import asyncio

    await asyncio.to_thread(service(data, runtime(data, [])).handle, item)
    with business_transaction(data["engine"], data["identity"]) as session:
        attempt = session.execute(text("SELECT id FROM execution_attempts")).scalar_one()
    port, calls = reader(data, governance)
    result = await port.inspect(attempt, data["readctx"], SecretStr("synthetic"), uuid4())
    assert result.comparison.status == "MATCHED" and calls == ["GET"]
    timeline = assert_oracle(data, item.job_id)
    assert any(row.source_kind == "READ_TRANSPORT" for row in timeline.events)
    assert not any(row.evidence_level == "E2" for row in timeline.events)
