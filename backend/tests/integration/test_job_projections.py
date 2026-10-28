"""真实PG同一查询事实；网络显式Mock，回读UNKNOWN输入不冒充M2-17生产者。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from yuliang.modules.command.applications import JobApplications
from yuliang.modules.command.jobs import CreateJobRequest
from yuliang.modules.delivery.queries import DeliveryJobQueries
from yuliang.modules.execution.receipts import ExecutionReceipts
from yuliang.modules.execution.recovery import ExecutionRecovery
from yuliang.modules.execution.settlement import BudgetSettlement
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_budget_initialization import budget_ready as budget_ready
from .test_budget_settlement import matched_input
from .test_capability_evidence import capability as capability
from .test_execution_receipts import observation
from .test_execution_recovery import event, expire
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import risk_ready as risk_ready
from .test_sync_checkpoints import sync_ready as sync_ready
from .test_target_guards import approved

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("mode", ["accepted", "rejected", "timeout"])
async def test_same_real_attempt_projects_both_contracts_without_fake_receipt(
    budget_ready: dict[str, Any],
    mode: str,
) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data, mode)
    ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    if mode == "accepted":
        # 本卡只测已有内部UNKNOWN的读契约；完整回读失败生产者仍归M2-17。
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(text("UPDATE execution_orders SET status='UNKNOWN',version=version+1"))
    common = data["applications"].projection(prepared.attempt.business.id)
    view = DeliveryJobQueries(data["applications"]).get(prepared.attempt.business.id)
    assert view is not None and view.id == prepared.attempt.execution_id
    assert common.job == data["applications"].get(prepared.attempt.business.id)
    assert view.status == ("FAILED" if mode == "rejected" else "UNKNOWN")
    assert (
        common.job.status.value
        == {"accepted": "VERIFYING", "rejected": "FAILED", "timeout": "EXECUTING"}[mode]
    )
    assert common.confirmed_change is None and not view.safe_retry
    if mode == "timeout":
        assert view.response is None and common.job.execution_receipt is None
    elif mode == "accepted":
        assert view.response is not None and view.response.accepted is True
        assert view.response.verification == "UNKNOWN" and common.job.error is None
        with business_transaction(data["engine"], data["identity"]) as session:
            # 只读解释不能回写原回执、伪造已核验时间或修改任务版本。
            original = session.execute(
                text("SELECT document FROM command_jobs WHERE id=:id"),
                {"id": prepared.attempt.business.id},
            ).scalar_one()
            assert original["execution_receipt"]["verification"] == "PENDING"
            assert original["execution_receipt"]["verified_at"] is None
    if path := os.getenv("YULIANG_PROJECTION_EVIDENCE"):
        if mode == "accepted":
            assert view.response is not None
            Path(path).write_text(
                json.dumps(
                    {
                        "jobId": str(prepared.attempt.business.id),
                        "attemptId": str(prepared.attempt.attempt_id),
                        "executionId": str(prepared.attempt.execution_id),
                        "requestId": str(data["applications"].request_id),
                        "v1Status": common.job.status,
                        "v8Status": view.status,
                        "receipt": view.response.model_dump(mode="json"),
                        "realPlatformCalls": 0,
                        "paidCalls": 0,
                        "input": "REAL_PG_AND_MOCK_ACK_WITH_EXPLICIT_INTERNAL_UNKNOWN_FIXTURE",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )


async def test_approval_read_keeps_plan_truth_and_only_e2_is_effective(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    job = approved(data)
    view = DeliveryJobQueries(data["applications"]).get(UUID(job.job_id))
    assert view is not None and view.status == "PENDING" and view.native_id is None
    assert data["applications"].projection(UUID(job.job_id)).confirmed_change is None
    with business_transaction(data["engine"], data["identity"]) as session:
        mirror = session.execute(
            text("SELECT projection FROM sync_resource_mirrors WHERE source_id=:id"),
            {"id": data["good_lease"].source_id},
        ).scalar_one()
        assert mirror["data"]["budget_minor"] == 100
    # 另一用例使用实际独立留档+维护校验E2，不用上述审批状态冒充生效。


async def test_actual_e2_finalization_is_the_only_effective_projection(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    prepared, fact = await matched_input(data)
    BudgetSettlement(data["applications"]).match(fact, 5)
    common = data["applications"].projection(prepared.attempt.business.id)
    view = DeliveryJobQueries(data["applications"]).get(prepared.attempt.business.id)
    assert view is not None and view.status == "EFFECTIVE"
    assert common.confirmed_change == common.job.new_value and view.response is not None
    assert view.response.source == "PLATFORM" and view.response.accepted is True
    assert view.response.verification == "MATCHED"


async def test_reader_and_receipt_commit_return_coherent_versions(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data)
    service = data["applications"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        reads = [pool.submit(service.projection, prepared.attempt.business.id) for _ in range(12)]
        receipt = pool.submit(
            ExecutionReceipts(service).record, prepared.attempt.attempt_id, raw, 1
        )
        values = [future.result(timeout=10) for future in reads]
        receipt.result(timeout=10)
    for value in values:
        assert value.job.version in {4, 5}
        assert value.job.history[-1].status == value.job.status
        assert len(value.job.history) == value.job.version


async def test_projection_keeps_private_scope_and_rechecks_current_authorization(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    prepared, _, _ = await observation(data)
    other = JobApplications(
        data["engine"],
        VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"]),
        uuid4(),
        data["applications"].key,
    )
    with pytest.raises(ApiError) as denied:
        DeliveryJobQueries(other).get(prepared.attempt.business.id)
    assert denied.value.code == ErrorCode.NO_DATA
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE data_scopes SET permissions_mask=ARRAY['overview.read'] "
                "WHERE membership_id=:id"
            ),
            {"id": data["identity"].membership_id},
        )
    with pytest.raises(ApiError) as revoked:
        DeliveryJobQueries(data["applications"]).get(prepared.attempt.business.id)
    assert revoked.value.code == ErrorCode.FORBIDDEN


async def test_recovery_unknown_has_last_check_but_no_platform_receipt(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    prepared, _, _ = await observation(data, "timeout")
    expire(data)
    ExecutionRecovery(data["applications"]).inspect(event(data, prepared.attempt.attempt_id))
    result = data["applications"].projection(prepared.attempt.business.id)
    assert result.unknown and result.last_checked_at is not None
    assert result.job.execution_receipt is None
    assert result.job.error is not None and not result.job.error.retryable
    view = DeliveryJobQueries(data["applications"]).get(prepared.attempt.business.id)
    assert view is not None and view.history[-1].at == result.last_checked_at
    assert view.response is None and view.history[-1].status == "UNKNOWN"


def test_actual_manual_handoff_has_no_advertising_execution(
    applications: dict[str, Any],
) -> None:
    data = applications
    value = CreateJobRequest(
        source="PC_MANUAL",
        shop_id=data["shop"],
        target_type="LIVE_SESSION",
        target_id=data["live"],
        action_type="REQUEST_LIVE_START",
        new_value={"kind": "HANDOFF", "value": "START"},
        intent="仅生成开播人工交接申请",
        idempotency_key=uuid4().hex,
    )
    job = data["applications"].create(value)
    assert job.status.value == "MANUAL_REQUIRED" and job.execution_receipt is None
    assert data["applications"].get(UUID(job.job_id)) == job
    assert DeliveryJobQueries(data["applications"]).get(UUID(job.job_id)) is None
