"""原actor snapshot.read+当前双scope下的结构化只读内部入口。"""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.execution.timeline_models import ExecutionTimeline
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class ExecutionTimelineReads:
    def __init__(self, engine: Engine, identity: VerifiedIdentity) -> None:
        self.engine, self.identity = engine, identity

    def read(self, job_id: UUID) -> ExecutionTimeline:
        engine = self.engine.execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=True
        )
        try:
            with business_transaction(engine, self.identity) as session:
                result = session.execute(
                    text("SELECT access.execution_timeline_read(:member,:user,:job)"),
                    dict(
                        member=self.identity.membership_id, user=self.identity.user_id, job=job_id
                    ),
                ).scalar_one()
                return ExecutionTimeline.model_validate(result)
        except ValidationError:
            raise ApiError(
                ErrorCode.DATA_COVERAGE_INSUFFICIENT, "结构化执行时间线证据不完整"
            ) from None
        except DBAPIError as error:
            mapping = {
                "42501": ErrorCode.FORBIDDEN,
                "P0002": ErrorCode.NO_DATA,
                "22023": ErrorCode.CAPABILITY_UNAVAILABLE,
                "54000": ErrorCode.DATA_COVERAGE_INSUFFICIENT,
            }
            state = getattr(error.orig, "sqlstate", None)
            if state in mapping:
                raise ApiError(mapping[state], "当前时间线读取范围、权限或完整性不满足") from None
            raise
