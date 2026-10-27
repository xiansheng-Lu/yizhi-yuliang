"""精确目标PG守卫与状态原子领取；租约到期不能直接再写。"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.modules.audit.service import record
from yuliang.modules.command.applications import JobApplications
from yuliang.modules.command.jobs import CommandJob
from yuliang.modules.command.state import JobEvent, StateFacts, evolve
from yuliang.modules.connectors.redaction import canonical, fingerprint
from yuliang.modules.policy.models import RiskStage
from yuliang.modules.policy.repository import policy_lock
from yuliang.modules.policy.service import RiskFramework
from yuliang.platform.errors import ApiError, ErrorCode


def controlled[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except DBAPIError as error:
        mapping = {
            "42501": ErrorCode.FORBIDDEN,
            "P0002": ErrorCode.NO_DATA,
            "40001": ErrorCode.VERSION_CONFLICT,
            "P0211": ErrorCode.TARGET_QUARANTINED,
            "55P03": ErrorCode.LOCKED,
            "22023": ErrorCode.INVALID_INPUT,
        }
        code = getattr(error.orig, "sqlstate", None)
        if code in mapping:
            raise ApiError(mapping[code], "当前目标、申请或执行租约需要重新核验") from None
        raise


class GuardLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    guardId: UUID
    jobId: UUID
    fence: int = Field(strict=True, gt=0)
    owner: UUID
    leaseUntil: AwareDatetime
    jobVersion: int = Field(strict=True, gt=1)


class TargetGuards:
    def __init__(self, applications: JobApplications) -> None:
        self.applications = applications

    def describe(self, session: Session, job_id: UUID) -> dict[str, Any]:
        actor = self.applications.identity
        return controlled(
            lambda: session.execute(
                text("SELECT access.target_guard_state(:member,:user,:job)"),
                {"member": actor.membership_id, "user": actor.user_id, "job": job_id},
            ).scalar_one()
        )

    def claim(
        self,
        session: Session,
        job_id: UUID,
        expected_version: int,
        expected_fence: int,
        owner: UUID,
        lease_seconds: int = 30,
    ) -> GuardLease:
        if (
            type(expected_fence) is not int
            or expected_fence < 0
            or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= 300
        ):
            raise ApiError(ErrorCode.INVALID_INPUT, "需要明确守卫版本与有效租约窗口")
        return controlled(
            lambda: self._claim(
                session, job_id, expected_version, expected_fence, owner, lease_seconds
            )
        )

    def _claim(
        self, session: Session, job_id: UUID, version: int, fence: int, owner: UUID, seconds: int
    ) -> GuardLease:
        actor = self.applications.identity
        policy_lock(session, actor.tenant_id, shared=True)
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760210))"),
            {"key": str(actor.tenant_id)},
        )
        decision = RiskFramework(self.applications).require_job(
            session, job_id, RiskStage.EXECUTE, version
        )
        row = session.execute(
            text("SELECT access.job_state_context(:member,:user,:job,'CLAIM')"),
            {"member": actor.membership_id, "user": actor.user_id, "job": job_id},
        ).scalar_one()
        before = CommandJob.model_validate(row["document"])
        after = evolve(
            before,
            JobEvent.CLAIM,
            StateFacts(risk=decision.summary),
            at=decision.summary.checked_at,
            actor_id=actor.user_id,
            requester_id=UUID(row["created_by"]),
        )
        proof = fingerprint(
            self.applications.key,
            "target-claim-v1",
            canonical(
                {
                    "job": str(job_id),
                    "version": version,
                    "fence": fence,
                    "owner": str(owner),
                    "assessment": str(decision.assessment_id),
                }
            ),
        )
        value = session.execute(
            text("""SELECT access.target_claim(:member,:user,:job,:version,
            :fence,:owner,:seconds,:assessment,CAST(:document AS jsonb),:request,:proof)"""),
            {
                "member": actor.membership_id,
                "user": actor.user_id,
                "job": job_id,
                "version": version,
                "fence": fence,
                "owner": owner,
                "seconds": seconds,
                "assessment": decision.assessment_id,
                "document": after.model_dump_json(),
                "request": self.applications.request_id,
                "proof": proof,
            },
        ).scalar_one()
        record(
            session,
            actor,
            {
                "action": "job.transition",
                "object_type": "job",
                "object_id": job_id,
                "request_id": self.applications.request_id,
                "after_hash": proof,
            },
        )
        return GuardLease.model_validate(value)

    def assert_current(self, session: Session, lease: GuardLease) -> None:
        actor = self.applications.identity
        controlled(
            lambda: session.execute(
                text("SELECT access.target_lease_current(:member,:user,:job,:guard,:owner,:fence)"),
                {
                    "member": actor.membership_id,
                    "user": actor.user_id,
                    "job": lease.jobId,
                    "guard": lease.guardId,
                    "owner": lease.owner,
                    "fence": lease.fence,
                },
            ).scalar_one()
        )
