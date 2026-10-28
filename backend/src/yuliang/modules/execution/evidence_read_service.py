"""独立snapshot.read，仅本人当前可见任务；无CLAIM/写/平台调用。"""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.execution.evidence_read_models import ExecutionEvidence
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class ExecutionEvidenceReads:
    def __init__(self, engine: Engine, identity: VerifiedIdentity) -> None:
        self.engine, self.identity = engine, identity

    def read(self, job_id: UUID) -> ExecutionEvidence:
        engine = self.engine.execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=True
        )
        try:
            with business_transaction(engine, self.identity) as session:
                value = session.execute(
                    text("SELECT access.execution_evidence_read(:member,:user,:job)"),
                    dict(
                        member=self.identity.membership_id, user=self.identity.user_id, job=job_id
                    ),
                ).scalar_one()
                return ExecutionEvidence.model_validate(value)
        except ValidationError:
            raise ApiError(
                ErrorCode.DATA_COVERAGE_INSUFFICIENT, "持久执行证据结构或关联不完整"
            ) from None
        except DBAPIError as error:
            code = getattr(error.orig, "sqlstate", None)
            mappings = {
                "42501": ErrorCode.FORBIDDEN,
                "P0002": ErrorCode.NO_DATA,
                "22023": ErrorCode.CAPABILITY_UNAVAILABLE,
                "54000": ErrorCode.DATA_COVERAGE_INSUFFICIENT,
            }
            if code in mappings:
                raise ApiError(mappings[code], "当前执行证据读取范围、权限或完整性不满足") from None
            raise
