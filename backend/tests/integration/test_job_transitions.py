"""真实PG状态/事件/审计/并发；策略与执行参与者为显式合成测试，未写平台。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from state_cases import facts_for, state_job

from yuliang.infrastructure.outbox import emit
from yuliang.modules.command.jobs import CommandJob, JobStatus
from yuliang.modules.command.state import RULES, JobEvent, StateFacts
from yuliang.modules.command.transitions import JobTransitions
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode
from yuliang.platform.messages import WorkMessage

from .test_capability_evidence import capability as capability
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import health_ready as health_ready
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration
EDGES = [(event, source) for event, rule in RULES.items() for source in sorted(rule.sources)]


def seed(data: dict[str, Any], status: JobStatus, *, expired: bool = False) -> CommandJob:
    base = data["applications"].create(data["input"])
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        now = owner.execute(text("SELECT clock_timestamp()")).scalar_one()
        job = state_job(status, now, base=base)
        if expired:
            job = CommandJob.model_validate(
                job.model_dump()
                | {
                    "created_at": now - timedelta(minutes=30),
                    "updated_at": now - timedelta(minutes=1),
                    "expires_at": now - timedelta(seconds=1),
                    "history": [
                        {
                            "status": status,
                            "at": now - timedelta(minutes=1),
                            "detail": "合成初始状态",
                        }
                    ],
                }
            )
        owner.execute(
            text("""UPDATE command_jobs SET status=:status,document=CAST(:document AS jsonb),
        created_at=:created,updated_at=:updated,expires_at=:expires,
        claimed_at=CASE WHEN :claimed THEN :updated ELSE NULL END WHERE id=:id"""),
            {
                "status": status.value,
                "document": job.model_dump_json(),
                "created": job.created_at,
                "updated": job.updated_at,
                "expires": job.expires_at,
                "id": UUID(job.job_id),
                "claimed": status
                in {
                    JobStatus.EXECUTING,
                    JobStatus.VERIFYING,
                    JobStatus.FAILED,
                    JobStatus.SUCCEEDED,
                },
            },
        )
        owner.execute(
            text(
                "UPDATE job_events SET to_status=:status,detail='合成初始状态',"
                "created_at=:at WHERE job_id=:id"
            ),
            {"status": status.value, "at": job.updated_at, "id": UUID(job.job_id)},
        )
    return job


class SyntheticParticipants:
    """只验证状态事务协议，绝不宣告实际审批、额度或执行模块已经完成。"""

    def __init__(
        self, data: dict[str, Any], *, barrier: Barrier | None = None, failure: bool = False
    ) -> None:
        self.data, self.barrier, self.failure = data, barrier, failure

    def check(
        self, session: Session, row: dict[str, Any], event: JobEvent, at: datetime
    ) -> StateFacts:
        if self.barrier:
            self.barrier.wait(timeout=5)
        return facts_for(event, CommandJob.model_validate(row["document"]), at)

    def apply(
        self,
        session: Session,
        row: dict[str, Any],
        after: CommandJob,
        event: JobEvent,
        facts: StateFacts,
    ) -> None:
        # 实际PG Outbox探针用于证明同事务回滚，不是执行投递或供应商调用。
        emit(
            session,
            WorkMessage(
                event_id=uuid4(),
                tenant_id=self.data["tenant"],
                job_id=UUID(after.job_id),
                membership_id=self.data["member"],
                user_id=self.data["user"],
            ),
            "yuliang.system.identity_probe",
        )
        if self.failure:
            raise RuntimeError("synthetic participant failure")


def service(data: dict[str, Any], event: JobEvent, **options: Any) -> JobTransitions:
    identity = (
        VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"])
        if event in {JobEvent.APPROVE, JobEvent.REJECT}
        else data["identity"]
    )
    return JobTransitions(
        data["engine"],
        identity,
        uuid4(),
        SecretStr("synthetic-state-key-more-than-32-characters"),
        SyntheticParticipants(data, **options),
    )


def counts(data: dict[str, Any], job: CommandJob) -> tuple[Any, ...]:
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        return (
            owner.execute(
                text("SELECT status,version FROM command_jobs WHERE id=:id"), {"id": job.job_id}
            ).one(),
            owner.execute(
                text("SELECT count(*) FROM job_events WHERE job_id=:id"), {"id": job.job_id}
            ).scalar_one(),
            owner.execute(
                text("SELECT count(*) FROM outbox_events WHERE aggregate_id=:id"),
                {"id": job.job_id},
            ).scalar_one(),
            owner.execute(
                text(
                    "SELECT count(*) FROM audit_logs WHERE object_id=:id "
                    "AND action='job.transition'"
                ),
                {"id": job.job_id},
            ).scalar_one(),
        )


@pytest.mark.parametrize("event,status", EDGES)
def test_each_legal_edge_with_real_condition_update(
    applications: dict[str, Any], event: JobEvent, status: JobStatus
) -> None:
    data = applications
    job = seed(data, status, expired=event == JobEvent.EXPIRE)
    if event in {JobEvent.APPROVE, JobEvent.REJECT}:
        # M2-14上线后纯合成参与者不能绕过真实审批账，合法纯归约边仍由单元矩阵覆盖。
        with pytest.raises(ApiError) as denied:
            service(data, event).apply(UUID(job.job_id), job.version, event)
        assert denied.value.code == ErrorCode.FORBIDDEN
        assert counts(data, job) == ((status.value, 1), 1, 0, 0)
        return
    after = service(data, event).apply(UUID(job.job_id), job.version, event)
    assert after.status == RULES[event].destination and after.version == 2
    assert counts(data, job) == ((after.status.value, 2), 2, 1, 1)


def test_database_rules_match_domain_and_default_boundary_is_closed(
    applications: dict[str, Any],
) -> None:
    data = applications
    job = seed(data, JobStatus.DRAFT)
    with business_transaction(data["engine"], data["identity"]) as session:
        actual = {
            (r.event, r.from_status, r.to_status, r.detail)
            for r in session.execute(text("SELECT * FROM job_transition_rules"))
        }
    expected = {
        (event.value, source.value, rule.destination.value, rule.detail)
        for event, rule in RULES.items()
        for source in rule.sources
    }
    assert actual == expected
    closed = JobTransitions(data["engine"], data["identity"], uuid4(), data["applications"].key)
    with pytest.raises(ApiError) as denied:
        closed.apply(UUID(job.job_id), 1, JobEvent.CONFIRM)
    assert denied.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
    assert counts(data, job) == (("DRAFT", 1), 1, 0, 0)


def test_illegal_or_old_version_never_writes_effects(applications: dict[str, Any]) -> None:
    data = applications
    job = seed(data, JobStatus.DRAFT)
    with pytest.raises(ApiError) as invalid:
        service(data, JobEvent.CLAIM).apply(UUID(job.job_id), 1, JobEvent.CLAIM)
    assert invalid.value.code == ErrorCode.ILLEGAL_TRANSITION
    after = service(data, JobEvent.CONFIRM).apply(UUID(job.job_id), 1, JobEvent.CONFIRM)
    with pytest.raises(ApiError) as old:
        service(data, JobEvent.CANCEL).apply(UUID(job.job_id), 1, JobEvent.CANCEL)
    assert old.value.code == ErrorCode.VERSION_CONFLICT
    assert counts(data, job) == ((after.status.value, 2), 2, 1, 1)


@pytest.mark.parametrize("expired", [False, True])
def test_cancel_expire_claim_competition_only_one_wins(
    applications: dict[str, Any], expired: bool
) -> None:
    data = applications
    job = seed(data, JobStatus.APPROVED, expired=expired)
    barrier = Barrier(3)

    def compete(event: JobEvent) -> JobStatus | ErrorCode:
        try:
            return service(data, event, barrier=barrier).apply(UUID(job.job_id), 1, event).status
        except ApiError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(compete, (JobEvent.CANCEL, JobEvent.EXPIRE, JobEvent.CLAIM)))
    assert sum(isinstance(r, JobStatus) for r in results) == 1
    assert counts(data, job)[1:] == (2, 1, 1)


@pytest.mark.parametrize("failure", ["participant", "audit"])
def test_participant_and_audit_failure_rollback_everything(
    applications: dict[str, Any], failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = applications
    job = seed(data, JobStatus.DRAFT)
    if failure == "audit":

        def fail(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("synthetic audit failure")

        monkeypatch.setattr("yuliang.modules.command.transitions.record", fail)
    with pytest.raises(RuntimeError):
        service(data, JobEvent.CONFIRM, failure=failure == "participant").apply(
            UUID(job.job_id), 1, JobEvent.CONFIRM
        )
    assert counts(data, job) == (("DRAFT", 1), 1, 0, 0)


@pytest.mark.parametrize("marker", ["claimed_at", "dispatch_started_at"])
def test_claim_or_send_marker_blocks_cancel_and_expiry(
    applications: dict[str, Any], marker: str
) -> None:
    data = applications
    job = seed(data, JobStatus.APPROVED, expired=True)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        query = (
            "UPDATE command_jobs SET claimed_at=clock_timestamp() WHERE id=:id"
            if marker == "claimed_at"
            else "UPDATE command_jobs SET dispatch_started_at=clock_timestamp() WHERE id=:id"
        )
        owner.execute(text(query), {"id": job.job_id})
    for event in (JobEvent.CANCEL, JobEvent.EXPIRE):
        with pytest.raises(ApiError) as denied:
            service(data, event).apply(UUID(job.job_id), 1, event)
        assert denied.value.code == ErrorCode.VERSION_CONFLICT
    assert counts(data, job) == (("APPROVED", 1), 1, 0, 0)


def test_runtime_no_direct_status_update_or_terminal_insert(applications: dict[str, Any]) -> None:
    data = applications
    job = seed(data, JobStatus.DRAFT)
    for query in (
        "UPDATE command_jobs SET status='SUCCEEDED' WHERE id=:id",
        "UPDATE job_transition_rules SET to_status='SUCCEEDED'",
        "UPDATE job_events SET to_status='SUCCEEDED' WHERE job_id=:id",
    ):
        with (
            pytest.raises(DBAPIError),
            business_transaction(data["engine"], data["identity"]) as session,
        ):
            session.execute(text(query), {"id": job.job_id})
    with (
        pytest.raises(DBAPIError),
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(
            text(
                "INSERT INTO command_jobs(tenant_id,created_by,shop_id,status,document) "
                "VALUES(:tenant,:actor,:shop,'SUCCEEDED','{}'::jsonb)"
            ),
            {"tenant": data["tenant"], "actor": data["user"], "shop": data["shop"]},
        )


def test_safe_retry_retains_job_and_history_and_unknown_cannot_retry(
    applications: dict[str, Any],
) -> None:
    data = applications
    job = seed(data, JobStatus.FAILED)
    retry_service = service(data, JobEvent.SAFE_RETRY)
    after = retry_service.apply(UUID(job.job_id), 1, JobEvent.SAFE_RETRY)
    assert after.job_id == job.job_id and after.status == JobStatus.EXECUTING
    assert after.history[0].status == JobStatus.FAILED and after.error is None
    unknown_service = service(data, JobEvent.SUBMIT_UNKNOWN)
    unknown = unknown_service.apply(UUID(job.job_id), 2, JobEvent.SUBMIT_UNKNOWN)
    with pytest.raises(ApiError):
        service(data, JobEvent.SAFE_RETRY).apply(
            UUID(job.job_id), unknown.version, JobEvent.SAFE_RETRY
        )
    assert counts(data, job) == (("EXECUTING", 3), 3, 2, 2)
    if evidence := os.getenv("YULIANG_STATE_EVIDENCE"):
        Path(evidence).write_text(
            json.dumps(
                {
                    "jobId": job.job_id,
                    "retryRequestId": str(retry_service.request_id),
                    "unknownRequestId": str(unknown_service.request_id),
                    "history": unknown.model_dump(mode="json")["history"],
                    "receiptId": unknown.execution_receipt.receipt_id
                    if unknown.execution_receipt
                    else None,
                    "stateVersion": unknown.version,
                    "factsSource": "SYNTHETIC_TRANSITION_PARTICIPANTS",
                    "realPgStateEventsAuditOutbox": True,
                    "realExecutionAttemptCreated": False,
                    "platformCalls": 0,
                    "paidCalls": 0,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
