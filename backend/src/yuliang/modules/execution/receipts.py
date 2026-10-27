"""原始请求/响应驱动回执；无响应只落系统未知事实，不伪造PLATFORM。"""

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
from yuliang.modules.command.jobs import CommandJob, JobError, JobReceipt, JobStatus
from yuliang.modules.command.state import JobEvent, StateFacts, evolve
from yuliang.modules.connectors.redaction import canonical
from yuliang.modules.execution.guards import controlled
from yuliang.platform.db import business_transaction

NAMESPACE = UUID("b0af981d-770b-5100-867e-99954094b117")


class ReceiptResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    receipt_id: UUID
    execution_id: UUID
    attempt_id: UUID
    raw_id: int = Field(strict=True, gt=0)
    source: Literal["PLATFORM", "SYSTEM"]
    accepted: bool | None = Field(strict=True)
    evidence_level: Literal["E0", "E1"]
    applied: bool = Field(strict=True)
    disposition: Literal[
        "APPLIED", "STALE_FENCE", "STALE_ATTEMPT", "STALE_VERSION", "STALE_EVIDENCE", "TERMINAL"
    ]
    execution_status: str
    execution_version: int = Field(strict=True, gt=0)
    job_status: JobStatus
    job_version: int = Field(strict=True, gt=0)


class ExecutionReceipts:
    def __init__(self, applications: JobApplications) -> None:
        self.applications = applications

    def record(
        self,
        attempt_id: UUID,
        raw_id: int,
        expected_execution_version: int,
        *,
        unit_of_work: Session | None = None,
    ) -> ReceiptResult:
        if (
            type(raw_id) is not int
            or raw_id < 1
            or type(expected_execution_version) is not int
            or expected_execution_version < 1
        ):
            raise ValueError("需要持久原始记录与明确执行版本")
        transaction = (
            nullcontext(unit_of_work)
            if unit_of_work is not None
            else business_transaction(self.applications.engine, self.applications.identity)
        )
        with transaction as session:
            return controlled(
                lambda: self._record(session, attempt_id, raw_id, expected_execution_version)
            )

    def _record(
        self, session: Session, attempt_id: UUID, raw_id: int, version: int
    ) -> ReceiptResult:
        actor = self.applications.identity
        arguments: dict[str, Any] = {
            "member": actor.membership_id,
            "user": actor.user_id,
            "attempt": attempt_id,
            "raw": raw_id,
            "version": version,
        }
        context = session.execute(
            text("SELECT access.execution_result_context(:member,:user,:attempt,:raw,:version)"),
            arguments,
        ).scalar_one()
        if "existing" in context:
            return ReceiptResult.model_validate(context["existing"])
        receipt_id = uuid5(NAMESPACE, f"{actor.tenant_id}|{attempt_id}|{raw_id}")
        document: CommandJob | None = None
        if context["disposition"] == "APPLIED":
            before = CommandJob.model_validate(context["job"]["document"])
            received = datetime.fromisoformat(context["raw"]["created_at"])
            receipt = None
            if context["source"] == "PLATFORM":
                receipt = JobReceipt(
                    receipt_id=str(receipt_id),
                    platform_receipt_id=None,
                    accepted=context["accepted"],
                    received_at=received,
                    verification="PENDING" if context["accepted"] is True else "UNKNOWN",
                    observed_value=None,
                    verified_at=None,
                )
            at = session.execute(text("SELECT clock_timestamp()")).scalar_one()
            error = (
                JobError(
                    code="PLATFORM_REJECTED", message="平台明确拒绝原请求", retryable=False, at=at
                )
                if context["event"] == "REJECT_SEND"
                else None
            )
            document = evolve(
                before,
                JobEvent(context["event"]),
                StateFacts(
                    receipt=receipt,
                    error=error,
                    evidence_level=context["evidenceLevel"],
                    proof_ref=f"execution-raw/{attempt_id}/{raw_id}",
                    transport_unknown=context["source"] == "SYSTEM",
                ),
                at=at,
                actor_id=actor.user_id,
                requester_id=UUID(context["job"]["created_by"]),
            )
        arguments.update(
            receipt=receipt_id,
            document=document.model_dump_json() if document else None,
            request=self.applications.request_id,
        )
        value = session.execute(
            text("""SELECT access.execution_record_result(:member,:user,:attempt,:raw,
            :version,:receipt,CAST(:document AS jsonb),:request)"""),
            arguments,
        ).scalar_one()
        result = ReceiptResult.model_validate(value)
        record(
            session,
            actor,
            {
                "action": "execution.result",
                "object_type": "execution_receipt",
                "object_id": receipt_id,
                "request_id": self.applications.request_id,
                "after_hash": hashlib.sha256(canonical(result.model_dump(mode="json"))).hexdigest(),
                "detail": {"result": "APPLIED" if result.applied else "DENIED"},
            },
        )
        return result

    def lookup(self, receipt_id: UUID) -> ReceiptResult:
        with business_transaction(self.applications.engine, self.applications.identity) as session:
            item = (
                session.execute(
                    text("SELECT attempt_id,raw_id,result FROM execution_receipts WHERE id=:id"),
                    {"id": receipt_id},
                )
                .mappings()
                .first()
            )
            if item is None:
                from yuliang.platform.errors import ApiError, ErrorCode

                raise ApiError(ErrorCode.NO_DATA, "未找到当前调用者的执行回执")
            # 回执是原结果快照，但读取仍核对原任务当前权限，不授予旧结果访问权。
            actor = self.applications.identity
            controlled(
                lambda: session.execute(
                    text("SELECT access.execution_result_context(:member,:user,:attempt,:raw,1)"),
                    {
                        "member": actor.membership_id,
                        "user": actor.user_id,
                        "attempt": item["attempt_id"],
                        "raw": item["raw_id"],
                    },
                ).scalar_one()
            )
            return ReceiptResult.model_validate(item["result"])
