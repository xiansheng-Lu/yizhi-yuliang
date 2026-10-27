"""版本条件状态仓储；每条转移必须装配真实同事务参与者，缺省关闭。"""

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.modules.audit.service import record
from yuliang.modules.command.jobs import CommandJob
from yuliang.modules.command.state import JobEvent, StateFacts, evolve
from yuliang.modules.connectors.redaction import canonical, fingerprint
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class TransitionParticipants(Protocol):
    """策略/额度/审批/执行模块的原子参与者；不得在事务里做外部IO。"""

    def check(
        self, session: Session, row: dict[str, Any], event: JobEvent, at: datetime
    ) -> StateFacts: ...

    def apply(
        self,
        session: Session,
        row: dict[str, Any],
        after: CommandJob,
        event: JobEvent,
        facts: StateFacts,
    ) -> None: ...


class JobTransitions:
    def __init__(
        self,
        engine: Engine,
        identity: VerifiedIdentity,
        request_id: UUID,
        audit_key: SecretStr,
        participants: TransitionParticipants | None = None,
    ) -> None:
        self.engine, self.identity, self.request_id, self.key = (
            engine,
            identity,
            request_id,
            audit_key,
        )
        self.participants = participants

    def apply(self, job_id: UUID, expected_version: int, event: JobEvent) -> CommandJob:
        if (
            type(expected_version) is not int
            or expected_version < 1
            or not isinstance(event, JobEvent)
        ):
            raise ApiError(ErrorCode.INVALID_INPUT, "需要有效版本与服务端生命周期枚举")
        if self.participants is None:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "状态参与者尚未装配，转移关闭")
        try:
            with business_transaction(self.engine, self.identity) as session:
                identity = {
                    "member": self.identity.membership_id,
                    "user": self.identity.user_id,
                    "job": job_id,
                    "event": event.value,
                }
                row = dict(
                    session.execute(
                        text("SELECT access.job_state_context(:member,:user,:job,:event)"), identity
                    ).scalar_one()
                )
                if row["version"] != expected_version:
                    raise ApiError(ErrorCode.VERSION_CONFLICT, "申请版本已变化")
                before = CommandJob.model_validate(row["document"])
                at = session.execute(text("SELECT clock_timestamp()")).scalar_one()
                facts = self.participants.check(session, row, event, at)
                after = evolve(
                    before,
                    event,
                    facts,
                    at=at,
                    actor_id=self.identity.user_id,
                    requester_id=UUID(row["created_by"]),
                )
                proof = fingerprint(
                    self.key,
                    "job-transition-proof-v1",
                    canonical(
                        {
                            "job": str(job_id),
                            "version": expected_version,
                            "event": event.value,
                            "level": facts.evidence_level,
                            "reference": facts.proof_ref,
                        }
                    ),
                )
                session.execute(
                    text("""SELECT access.job_apply_transition(:member,:user,:job,
                :version,:event,CAST(:document AS jsonb),:request,:proof)"""),
                    identity
                    | {
                        "version": expected_version,
                        "document": after.model_dump_json(),
                        "request": self.request_id,
                        "proof": proof,
                    },
                ).scalar_one()
                self.participants.apply(session, row, after, event, facts)
                record(
                    session,
                    self.identity,
                    {
                        "action": "job.transition",
                        "object_type": "job",
                        "object_id": job_id,
                        "request_id": self.request_id,
                        "before_hash": proof,
                        "after_hash": fingerprint(
                            self.key, "job-state-v1", canonical(after.model_dump(mode="json"))
                        ),
                        "detail": {"changed_fields": ["status"]},
                    },
                )
                return after
        except DBAPIError as error:
            code = getattr(error.orig, "sqlstate", None)
            mapping = {
                "42501": ErrorCode.FORBIDDEN,
                "P0002": ErrorCode.NO_DATA,
                "40001": ErrorCode.VERSION_CONFLICT,
                "22023": ErrorCode.INVALID_INPUT,
            }
            if code in mapping:
                raise ApiError(mapping[code], "任务权限、状态或版本需要重新核对") from None
            raise
