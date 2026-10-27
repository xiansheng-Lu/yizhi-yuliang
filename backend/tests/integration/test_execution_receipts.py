"""真实PG回执/顺序/fence/短事务；供应商响应明确为MockTransport。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.command.applications import JobApplications
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.outcomes import BusinessVerdict
from yuliang.modules.execution.attempts import PreparedExecution
from yuliang.modules.execution.receipts import ExecutionReceipts
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_budget_initialization import budget_ready as budget_ready
from .test_capability_evidence import capability as capability
from .test_execution_attempts import prepare
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_platform_payloads import decoder, signer
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import risk_ready as risk_ready
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


async def observation(
    data: dict[str, Any], mode: str = "accepted"
) -> tuple[PreparedExecution, int, int]:
    orders, prepared, _ = prepare(data)
    ticket = prepared.attempt
    assert prepared.spec is not None and prepared.parameters is not None

    def upstream(request: httpx.Request) -> httpx.Response:
        if mode == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if mode == "late_fence":
            with data["maintenance"].begin() as owner:
                owner.execute(text("SET LOCAL ROLE app_owner"))
                owner.execute(text("UPDATE target_execution_guards SET fence=fence+1"))
        status = 500 if mode == "http500" else 200
        return httpx.Response(status, json={"code": 1 if mode == "rejected" else 0})

    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(upstream)),
        data["archive"],
        decoder=(lambda response: BusinessVerdict()) if mode == "unconfirmed" else decoder,
    )
    await client.send(
        data["ctx"],
        prepared.spec,
        ticket.outbound_id,
        prepared.parameters,
        SecretStr("synthetic"),
        target=data["target"],
        execution_id=ticket.execution_id,
        attempt_id=ticket.attempt_id,
        before_verify=orders.before_verify(ticket),
        after_admission=orders.after_admission(
            ticket, data["ctx"], prepared.spec, prepared.parameters
        ),
    )
    with business_transaction(data["engine"], data["identity"]) as session:
        rows = (
            session.execute(
                text("SELECT id FROM platform_raw_payloads WHERE outbound_id=:id ORDER BY id"),
                {"id": ticket.outbound_id},
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
    return prepared, rows[0], rows[1]


def job_state(data: dict[str, Any], prepared: PreparedExecution) -> Any:
    return data["applications"].get(prepared.attempt.business.id)


@pytest.mark.parametrize("mode", ["accepted", "rejected", "unconfirmed", "http500", "timeout"])
async def test_response_mapping_and_unknown_null(budget_ready: dict[str, Any], mode: str) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data, mode)
    service = ExecutionReceipts(data["applications"])
    result = service.record(prepared.attempt.attempt_id, raw, 1)
    assert result.applied and result.job_version == 5 and result.execution_version == 2
    assert result.source == ("SYSTEM" if mode == "timeout" else "PLATFORM")
    assert result.accepted is (
        True if mode == "accepted" else False if mode == "rejected" else None
    )
    assert result.evidence_level == ("E1" if mode == "rejected" else "E0")
    job = job_state(data, prepared)
    assert job.status.value == {"accepted": "VERIFYING", "rejected": "FAILED"}.get(
        mode, "EXECUTING"
    )
    if mode == "timeout":
        assert job.execution_receipt is None and job.error.code == "RESULT_UNKNOWN"
    else:
        assert job.execution_receipt.source == "PLATFORM"
        assert job.execution_receipt.accepted is result.accepted
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT state FROM job_budget_holds")).scalar_one() == "HELD"
        if result.accepted is None:
            assert (
                session.execute(text("SELECT state FROM target_execution_guards")).scalar_one()
                == "QUARANTINED"
            )
    assert service.lookup(result.receipt_id) == result
    if mode == "accepted" and (evidence := os.getenv("YULIANG_RECEIPT_EVIDENCE")):
        Path(evidence).write_text(
            json.dumps(
                {
                    **result.model_dump(mode="json"),
                    "platformCalls": 0,
                    "paidCalls": 0,
                    "input": "EXPLICIT_MOCK_RESPONSE_REAL_PG_AND_KERNEL",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


async def test_four_concurrent_results_share_first_receipt_and_transition(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data)
    service = ExecutionReceipts(data["applications"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: service.record(prepared.attempt.attempt_id, raw, 1), range(4))
        )
    assert all(result == results[0] for result in results)
    assert job_state(data, prepared).version == 5
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM execution_receipts")).scalar_one() == 1
        assert (
            session.execute(
                text("SELECT count(*) FROM audit_logs WHERE action='execution.result'")
            ).scalar_one()
            == 1
        )


async def test_system_observation_then_late_real_response_can_resolve_same_fence(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    prepared, started, raw = await observation(data)
    service = ExecutionReceipts(data["applications"])
    unknown = service.record(prepared.attempt.attempt_id, started, 1)
    assert unknown.source == "SYSTEM" and unknown.accepted is None
    assert job_state(data, prepared).execution_receipt is None
    accepted = service.record(prepared.attempt.attempt_id, raw, 2)
    assert accepted.applied and accepted.accepted is True and accepted.job_version == 6
    assert service.record(prepared.attempt.attempt_id, started, 999) == unknown
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(text("SELECT state FROM target_execution_guards")).scalar_one()
            == "QUARANTINED"
        )


@pytest.mark.parametrize("fault", ["fence", "attempt", "version", "terminal", "older"])
async def test_old_results_are_retained_without_overwriting_current_state(
    budget_ready: dict[str, Any], fault: str
) -> None:
    data = budget_ready
    prepared, started, raw = await observation(
        data, "unconfirmed" if fault == "older" else "accepted"
    )
    service = ExecutionReceipts(data["applications"])
    expected = 1
    if fault == "older":
        service.record(prepared.attempt.attempt_id, raw, 1)
        raw, expected = started, 2
    elif fault == "version":
        expected = 999
    else:
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            if fault == "fence":
                owner.execute(text("UPDATE target_execution_guards SET fence=fence+1"))
            elif fault == "attempt":
                owner.execute(text("UPDATE execution_orders SET current_attempt=2"))
            elif fault == "terminal":
                # 故障注入内部终态，未声称M2-17核验生产者已完成。
                owner.execute(text("UPDATE execution_orders SET status='EFFECTIVE',version=9"))
    before = job_state(data, prepared)
    result = service.record(prepared.attempt.attempt_id, raw, expected)
    assert not result.applied
    assert (
        result.disposition
        == {
            "fence": "STALE_FENCE",
            "attempt": "STALE_ATTEMPT",
            "version": "STALE_VERSION",
            "terminal": "TERMINAL",
            "older": "STALE_EVIDENCE",
        }[fault]
    )
    assert job_state(data, prepared) == before
    assert service.record(prepared.attempt.attempt_id, raw, 1) == result


async def test_result_audit_failure_rolls_back_receipt_state_event_and_keeps_raw(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data)
    before = job_state(data, prepared)

    def failed(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic result audit failure")

    monkeypatch.setattr("yuliang.modules.execution.receipts.record", failed)
    with pytest.raises(RuntimeError, match="synthetic result audit failure"):
        ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    assert job_state(data, prepared) == before
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM execution_receipts")).scalar_one() == 0
        assert (
            session.execute(
                text("SELECT count(*) FROM platform_raw_payloads WHERE id=:id"), {"id": raw}
            ).scalar_one()
            == 1
        )


async def test_unrelated_raw_and_missing_attempt_do_not_create_receipts(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    prepared, _, _ = await observation(data)
    with business_transaction(data["engine"], data["identity"]) as session:
        unrelated = session.execute(text("SELECT min(id) FROM platform_raw_payloads")).scalar_one()
    service = ExecutionReceipts(data["applications"])
    for attempt, raw in ((prepared.attempt.attempt_id, unrelated), (uuid4(), unrelated)):
        with pytest.raises(ApiError) as raised:
            service.record(attempt, raw, 1)
        assert raised.value.code == ErrorCode.NO_DATA


async def test_receipts_private_immutable_to_runtime_and_read_rechecks_permission(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data)
    service = ExecutionReceipts(data["applications"])
    result = service.record(prepared.attempt.attempt_id, raw, 1)
    for statement in (
        "UPDATE execution_receipts SET accepted=false",
        "DELETE FROM execution_receipts",
    ):
        with (
            pytest.raises(DBAPIError),
            business_transaction(data["engine"], data["identity"]) as session,
        ):
            session.execute(text(statement))
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:id"),
            {"id": data["identity"].membership_id},
        )
    with pytest.raises(ApiError) as raised:
        service.lookup(result.receipt_id)
    assert raised.value.code == ErrorCode.FORBIDDEN


async def test_late_response_archived_after_fence_change_is_isolated(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data, "late_fence")
    result = ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    assert result.source == "PLATFORM" and result.accepted is True
    assert not result.applied and result.disposition == "STALE_FENCE"
    assert job_state(data, prepared).status.value == "EXECUTING"


async def test_other_actor_cannot_lookup_private_receipt(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data)
    result = ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    other = ExecutionReceipts(
        JobApplications(
            data["engine"],
            VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"]),
            uuid4(),
            data["applications"].key,
        )
    )
    with pytest.raises(ApiError) as raised:
        other.lookup(result.receipt_id)
    assert raised.value.code == ErrorCode.NO_DATA
    with pytest.raises(ApiError) as raised:
        other.record(prepared.attempt.attempt_id, raw, 1)
    assert raised.value.code == ErrorCode.NO_DATA
