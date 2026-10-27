"""受控预算治理摘要与凭终结证明解冻；不接受客户端修改B/R。"""

import hashlib
from contextlib import nullcontext
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from yuliang.modules.audit.service import record
from yuliang.modules.command.applications import JobApplications
from yuliang.modules.command.jobs import CommandJob, JobError
from yuliang.modules.command.state import JobEvent, StateFacts, evolve
from yuliang.modules.connectors.redaction import canonical
from yuliang.modules.execution.guards import controlled
from yuliang.modules.execution.settlement import BudgetSettlement, FinalizationResult
from yuliang.modules.policy.models import RiskStage
from yuliang.modules.policy.repository import policy_lock
from yuliang.modules.policy.service import RiskFramework
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class BudgetGovernance:
    def __init__(self, engine: Engine, identity: VerifiedIdentity, request_id: UUID) -> None:
        self.engine, self.identity, self.request_id = engine, identity, request_id

    def summary(self, ledger_id: UUID) -> dict[str, Any]:
        with business_transaction(self.engine, self.identity) as session:
            value: dict[str, Any] = controlled(
                lambda: session.execute(
                    text("SELECT access.budget_governance_summary(:member,:user,:ledger)"),
                    {
                        "member": self.identity.membership_id,
                        "user": self.identity.user_id,
                        "ledger": ledger_id,
                    },
                ).scalar_one()
            )
            return value

    def resolve(self, discrepancy_id: UUID, finalization_id: UUID) -> dict[str, Any]:
        with business_transaction(self.engine, self.identity) as session:
            value: dict[str, Any] = controlled(
                lambda: session.execute(
                    text(
                        "SELECT access.budget_resolve_discrepancy"
                        "(:member,:user,:id,:proof,:request)"
                    ),
                    {
                        "member": self.identity.membership_id,
                        "user": self.identity.user_id,
                        "id": discrepancy_id,
                        "proof": finalization_id,
                        "request": self.request_id,
                    },
                ).scalar_one()
            )
            if not value["replayed"]:
                record(
                    session,
                    self.identity,
                    {
                        "action": "budget.resolve",
                        "object_type": "budget_discrepancy",
                        "object_id": discrepancy_id,
                        "request_id": self.request_id,
                        "after_hash": hashlib.sha256(canonical(value)).hexdigest(),
                    },
                )
            return value


class UnsentBudgetRejection:
    """仅额度复检确已拒绝且未领取发送时关闭原批准单，同事务E1释放。"""

    def __init__(self, applications: JobApplications) -> None:
        self.applications = applications

    def reject(
        self,
        job_id: UUID,
        expected_version: int,
        *,
        unit_of_work: Session | None = None,
    ) -> FinalizationResult:
        if type(expected_version) is not int or expected_version < 1:
            raise ApiError(ErrorCode.INVALID_INPUT, "需要明确原批准版本")
        transaction = (
            nullcontext(unit_of_work)
            if unit_of_work is not None
            else business_transaction(self.applications.engine, self.applications.identity)
        )
        with transaction as session:
            return controlled(lambda: self._reject(session, job_id, expected_version))

    def _reject(self, session: Session, job_id: UUID, version: int) -> FinalizationResult:
        actor = self.applications.identity
        policy_lock(session, actor.tenant_id, shared=True)
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760210))"),
            {"key": str(actor.tenant_id)},
        )
        row = session.execute(
            text("SELECT access.job_state_context(:member,:user,:job,'CLAIM')"),
            {"member": actor.membership_id, "user": actor.user_id, "job": job_id},
        ).scalar_one()
        before = CommandJob.model_validate(row["document"])
        existing = session.execute(
            text("""SELECT f.result FROM budget_job_finalizations f
        JOIN job_events e ON (e.tenant_id,e.job_id,e.job_version)=(f.tenant_id,f.job_id,:version)
        WHERE f.job_id=:job AND e.control_event='UNSENT_REJECT'
         AND f.kind='RELEASE' AND f.proof_kind='UNCLAIMED_TERMINAL'"""),
            {"job": job_id, "version": before.version},
        ).scalar_one_or_none()
        if existing and before.version == version + 1:
            return FinalizationResult.model_validate(existing)
        if before.version != version or before.status.value != "APPROVED":
            raise ApiError(ErrorCode.VERSION_CONFLICT, "批准单已变化或已有执行尝试")
        decision = RiskFramework(self.applications).assess_job(
            session,
            job_id,
            RiskStage.EXECUTE,
            expected_version=version,
        )
        if decision.summary.result != "BLOCKED" or not any(
            c.code == "AMOUNT_LIMIT" and not c.passed for c in decision.summary.checks
        ):
            raise ApiError(ErrorCode.ILLEGAL_TRANSITION, "本次核对没有额度拒绝结论")
        at = decision.summary.checked_at
        after = evolve(
            before,
            JobEvent.UNSENT_REJECT,
            StateFacts(
                risk=decision.summary,
                evidence_level="E1",
                proof_ref=f"unclaimed-risk/{decision.assessment_id}",
                error=JobError(
                    code="RISK_BLOCKED",
                    message="发送前额度复核未通过，已确认未发送",
                    retryable=False,
                    at=at,
                ),
            ),
            at=at,
            actor_id=actor.user_id,
            requester_id=UUID(row["created_by"]),
        )
        session.execute(
            text("""SELECT access.budget_reject_unsent(:member,:user,:job,
        :version,:assessment,CAST(:document AS jsonb),:request)"""),
            {
                "member": actor.membership_id,
                "user": actor.user_id,
                "job": job_id,
                "version": version,
                "assessment": decision.assessment_id,
                "document": after.model_dump_json(),
                "request": self.applications.request_id,
            },
        )
        result = BudgetSettlement(self.applications).release(job_id, unit_of_work=session)
        record(
            session,
            actor,
            {
                "action": "job.transition",
                "object_type": "job",
                "object_id": job_id,
                "request_id": self.applications.request_id,
                "after_hash": hashlib.sha256(canonical(after.model_dump(mode="json"))).hexdigest(),
            },
        )
        return result
