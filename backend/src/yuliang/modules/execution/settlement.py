"""有归档E1/E2的预算终结；可复用生命周期事务，不接外部IO。"""

import hashlib
from contextlib import nullcontext
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.audit.service import record
from yuliang.modules.command.applications import JobApplications
from yuliang.modules.command.jobs import CommandJob, MoneyValue
from yuliang.modules.command.state import JobEvent, StateFacts, evolve
from yuliang.modules.connectors.redaction import canonical
from yuliang.modules.execution.guards import controlled
from yuliang.platform.db import business_transaction

NAMESPACE = UUID("5c3a7634-9547-572e-9dc4-c860d65b8988")


class FinalizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    finalization_id: UUID
    job_id: UUID
    kind: Literal["MATCH", "RELEASE"]
    proof_kind: Literal[
        "VERIFIED_MATCH", "UNCLAIMED_TERMINAL", "PLATFORM_REJECTION", "RECOVERY_NOT_SENT"
    ]
    job_version: int = Field(strict=True, gt=0)
    buckets: int = Field(strict=True, ge=0)
    verified_fact_id: UUID | None


class BudgetSettlement:
    def __init__(self, applications: JobApplications) -> None:
        self.applications = applications

    def _transaction(self, session: Session | None) -> Any:
        return (
            nullcontext(session)
            if session is not None
            else business_transaction(self.applications.engine, self.applications.identity)
        )

    def _audit(self, session: Session, result: FinalizationResult) -> None:
        record(
            session,
            self.applications.identity,
            {
                "action": "budget.finalize",
                "object_type": "budget_finalization",
                "object_id": result.finalization_id,
                "request_id": self.applications.request_id,
                "after_hash": hashlib.sha256(canonical(result.model_dump(mode="json"))).hexdigest(),
            },
        )

    def match(
        self,
        verified_fact_id: UUID,
        expected_job_version: int,
        *,
        unit_of_work: Session | None = None,
    ) -> FinalizationResult:
        if type(expected_job_version) is not int or expected_job_version < 1:
            raise ValueError("需要明确任务版本")
        with self._transaction(unit_of_work) as session:
            return controlled(lambda: self._match(session, verified_fact_id, expected_job_version))

    def _match(self, session: Session, fact: UUID, version: int) -> FinalizationResult:
        actor = self.applications.identity
        arguments: dict[str, Any] = {
            "member": actor.membership_id,
            "user": actor.user_id,
            "fact": fact,
            "version": version,
        }
        context = session.execute(
            text("SELECT access.budget_match_context(:member,:user,:fact,:version)"),
            arguments,
        ).scalar_one()
        if "existing" in context:
            return FinalizationResult.model_validate(context["existing"])
        before = CommandJob.model_validate(context["job"]["document"])
        if before.execution_receipt is None:
            raise ValueError("缺少已落库平台受理回执")
        receipt = before.execution_receipt.model_copy(
            update={
                "verification": "MATCHED",
                "observed_value": MoneyValue.model_validate(context["fact"]["observed_value"]),
                "verified_at": datetime.fromisoformat(context["fact"]["verified_at"]),
            }
        )
        at = session.execute(text("SELECT clock_timestamp()")).scalar_one()
        after = evolve(
            before,
            JobEvent.MATCH,
            StateFacts(
                receipt=receipt,
                evidence_level="E2",
                proof_ref=f"verified-action/{fact}",
            ),
            at=at,
            actor_id=actor.user_id,
            requester_id=UUID(context["job"]["created_by"]),
        )
        arguments.update(
            id=uuid5(NAMESPACE, f"{actor.tenant_id}|{before.job_id}"),
            document=after.model_dump_json(),
            request=self.applications.request_id,
        )
        value = session.execute(
            text("""SELECT access.budget_settle_match(:member,:user,
        :fact,:version,:id,CAST(:document AS jsonb),:request)"""),
            arguments,
        ).scalar_one()
        result = FinalizationResult.model_validate(value)
        self._audit(session, result)
        return result

    def release(
        self,
        job_id: UUID,
        receipt_id: UUID | None = None,
        *,
        unit_of_work: Session | None = None,
    ) -> FinalizationResult:
        actor = self.applications.identity
        with self._transaction(unit_of_work) as session:
            value = controlled(
                lambda: session.execute(
                    text("SELECT access.budget_release(:member,:user,:job,:receipt,:id,:request)"),
                    {
                        "member": actor.membership_id,
                        "user": actor.user_id,
                        "job": job_id,
                        "receipt": receipt_id,
                        "id": uuid5(NAMESPACE, f"{actor.tenant_id}|{job_id}"),
                        "request": self.applications.request_id,
                    },
                ).scalar_one()
            )
            result = FinalizationResult.model_validate(value["result"])
            if value["created"]:
                self._audit(session, result)
            return result

    def release_recovered(
        self,
        job_id: UUID,
        proof_id: UUID,
        *,
        unit_of_work: Session | None = None,
    ) -> FinalizationResult:
        """仅持久恢复E1可用；保留原claimed/dispatch字段，不能伪装成未领取。"""
        actor = self.applications.identity
        with self._transaction(unit_of_work) as session:
            value = controlled(
                lambda: session.execute(
                    text(
                        "SELECT access.budget_release_recovery"
                        "(:member,:user,:job,:proof,:id,:request)"
                    ),
                    {
                        "member": actor.membership_id,
                        "user": actor.user_id,
                        "job": job_id,
                        "proof": proof_id,
                        "id": uuid5(NAMESPACE, f"{actor.tenant_id}|{job_id}"),
                        "request": self.applications.request_id,
                    },
                ).scalar_one()
            )
            result = FinalizationResult.model_validate(value["result"])
            if value["created"]:
                self._audit(session, result)
            return result
