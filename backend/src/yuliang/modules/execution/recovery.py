"""恢复只处理已持久事实，撤销发送资格后才可证明未发送；不包含平台HTTP。"""

import hashlib
from contextlib import nullcontext
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.audit.service import record
from yuliang.modules.command.applications import JobApplications
from yuliang.modules.command.jobs import CommandJob, JobError, JobStatus
from yuliang.modules.command.state import JobEvent, StateFacts, evolve
from yuliang.modules.connectors.redaction import canonical
from yuliang.modules.execution.guards import controlled
from yuliang.modules.execution.receipts import ExecutionReceipts
from yuliang.modules.execution.settlement import BudgetSettlement
from yuliang.platform.db import business_transaction


class RecoveryDeferred(Exception):
    def __init__(self, until: datetime) -> None:
        super().__init__("恢复尚未到期，不获得重发资格")
        self.until = until


class RecoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    recovery_id: UUID
    attempt_id: UUID
    state: Literal["DUE", "WAITING", "DONE", "BLOCKED_AUTH", "MANUAL_REQUIRED"]
    outcome: str
    proof_id: UUID | None = None
    evidence_level: Literal["E0", "E1"] = "E0"
    network_attempts: int = Field(default=0, strict=True, ge=0)
    job_status: JobStatus | None = None
    job_version: int | None = Field(default=None, strict=True, gt=0)
    next_event_id: UUID | None = None
    next_due: datetime | None = None


class ExecutionRecovery:
    def __init__(self, applications: JobApplications) -> None:
        self.applications = applications

    def inspect(self, event_id: UUID, *, unit_of_work: Session | None = None) -> RecoveryResult:
        transaction = (
            nullcontext(unit_of_work)
            if unit_of_work is not None
            else business_transaction(self.applications.engine, self.applications.identity)
        )
        with transaction as session:
            return controlled(lambda: self._inspect(session, event_id))

    def _inspect(self, session: Session, event_id: UUID) -> RecoveryResult:
        actor = self.applications.identity
        arguments = {"member": actor.membership_id, "user": actor.user_id, "event": event_id}
        context = session.execute(
            text("SELECT access.execution_recovery_context(:member,:user,:event)"),
            arguments,
        ).scalar_one()
        if "existing" in context:
            return RecoveryResult.model_validate(context["existing"])
        if "deferred" in context:
            raise RecoveryDeferred(datetime.fromisoformat(context["deferred"]))
        # 同fence的迟到响应仍可成为事实；隔离期间始终没有再次发送资格。
        if context["kind"] == "UNKNOWN" and context["raw"] is not None:
            before = CommandJob.model_validate(context["job"]["document"])
            if before.status.value == "EXECUTING":
                receipt = ExecutionReceipts(self.applications).record(
                    UUID(context["attempt"]["id"]),
                    context["raw"]["id"],
                    context["order"]["version"],
                    unit_of_work=session,
                )
                if receipt.applied and receipt.accepted is False:
                    BudgetSettlement(self.applications).release(
                        UUID(before.job_id),
                        receipt.receipt_id,
                        unit_of_work=session,
                    )
            elif (
                before.status.value == "FAILED"
                and before.execution_receipt is not None
                and before.execution_receipt.accepted is False
            ):
                BudgetSettlement(self.applications).release(
                    UUID(before.job_id),
                    UUID(before.execution_receipt.receipt_id),
                    unit_of_work=session,
                )
        observed = session.execute(
            text("SELECT access.execution_recovery_observe(:member,:user,:event,:id)"),
            arguments | {"id": uuid4()},
        ).scalar_one()
        if "existing" in observed:
            return RecoveryResult.model_validate(observed["existing"])
        if "deferred" in observed:
            raise RecoveryDeferred(datetime.fromisoformat(observed["deferred"]))
        context, proof = observed["context"], observed["proof"]
        if proof["kind"] == "NOT_SENT":
            before = CommandJob.model_validate(context["job"]["document"])
            at = session.execute(text("SELECT clock_timestamp()")).scalar_one()
            after = evolve(
                before,
                JobEvent.CONFIRMED_NOT_EXECUTED,
                StateFacts(
                    transport_not_sent=True,
                    evidence_level="E1",
                    proof_ref=f"execution-recovery/{proof['id']}",
                    error=JobError(
                        code="NOT_SENT",
                        message="已撤销原发送资格并确认没有网络发送",
                        retryable=False,
                        at=at,
                    ),
                ),
                at=at,
                actor_id=actor.user_id,
                requester_id=UUID(context["job"]["created_by"]),
            )
            session.execute(
                text("""SELECT access.execution_recovery_not_sent(:member,:user,
            :proof,CAST(:document AS jsonb),:request)"""),
                {
                    "member": actor.membership_id,
                    "user": actor.user_id,
                    "proof": UUID(proof["id"]),
                    "document": after.model_dump_json(),
                    "request": self.applications.request_id,
                },
            )
            BudgetSettlement(self.applications).release_recovered(
                UUID(before.job_id),
                UUID(proof["id"]),
                unit_of_work=session,
            )
        value = session.execute(
            text("SELECT access.execution_recovery_finish(:member,:user,:event,:proof)"),
            arguments | {"proof": UUID(proof["id"])},
        ).scalar_one()
        result = RecoveryResult.model_validate(value)
        record(
            session,
            actor,
            {
                "action": "execution.recover",
                "object_type": "execution_recovery",
                "object_id": result.recovery_id,
                "request_id": self.applications.request_id,
                "after_hash": hashlib.sha256(canonical(result.model_dump(mode="json"))).hexdigest(),
            },
        )
        return result

    def blocked(
        self,
        event_id: UUID,
        reason: Literal["AUTH_REVOKED", "INTEGRITY_REQUIRED"],
        *,
        unit_of_work: Session | None = None,
    ) -> RecoveryResult:
        """仅原持久事件关系可记SYSTEM/E0阻塞；不调用资金或假造有效用户审计。"""
        actor = self.applications.identity
        transaction = (
            nullcontext(unit_of_work)
            if unit_of_work is not None
            else business_transaction(self.applications.engine, actor)
        )
        with transaction as session:
            value = controlled(
                lambda: session.execute(
                    text("SELECT access.execution_recovery_blocked(:member,:user,:event,:reason)"),
                    {
                        "member": actor.membership_id,
                        "user": actor.user_id,
                        "event": event_id,
                        "reason": reason,
                    },
                ).scalar_one()
            )
            return RecoveryResult.model_validate(value)

    def resume(self, event_id: UUID) -> RecoveryResult:
        actor = self.applications.identity
        with business_transaction(self.applications.engine, actor) as session:
            value = controlled(
                lambda: session.execute(
                    text("SELECT access.execution_recovery_resume(:member,:user,:event)"),
                    {"member": actor.membership_id, "user": actor.user_id, "event": event_id},
                ).scalar_one()
            )
            result = RecoveryResult.model_validate(value)
            record(
                session,
                actor,
                {
                    "action": "execution.recover",
                    "object_type": "execution_recovery",
                    "object_id": result.recovery_id,
                    "request_id": self.applications.request_id,
                    "after_hash": hashlib.sha256(
                        canonical(result.model_dump(mode="json"))
                    ).hexdigest(),
                },
            )
            return result

    def for_job(self, job_id: UUID) -> RecoveryResult:
        actor = self.applications.identity
        with business_transaction(self.applications.engine, actor) as session:
            controlled(
                lambda: session.execute(
                    text("SELECT access.job_state_context(:member,:user,:job,'CLAIM')"),
                    {"member": actor.membership_id, "user": actor.user_id, "job": job_id},
                ).scalar_one()
            )
            row = (
                session.execute(
                    text("""SELECT r.* FROM execution_recoveries r
            JOIN execution_attempts a ON (a.tenant_id,a.id)=(r.tenant_id,r.attempt_id)
            JOIN execution_orders o ON (o.tenant_id,o.id,o.current_attempt)
             =(a.tenant_id,a.order_id,a.attempt_no)
            WHERE r.job_id=:id"""),
                    {"id": job_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                from yuliang.platform.errors import ApiError, ErrorCode

                raise ApiError(ErrorCode.NO_DATA, "当前申请没有恢复任务")
            return RecoveryResult.model_validate(
                row["result"]
                or {
                    "recovery_id": row["id"],
                    "attempt_id": row["attempt_id"],
                    "state": row["state"],
                    "outcome": row["outcome"],
                    "next_event_id": row["current_event_id"],
                    "next_due": row["next_due"],
                }
            )

    def manager_summary(self) -> dict[str, Any]:
        actor = self.applications.identity
        with business_transaction(self.applications.engine, actor) as session:
            value: dict[str, Any] = controlled(
                lambda: session.execute(
                    text("SELECT access.execution_recovery_manager(:member,:user)"),
                    {"member": actor.membership_id, "user": actor.user_id},
                ).scalar_one()
            )
            return value
