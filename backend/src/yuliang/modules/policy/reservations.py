"""额度预占同事务参与原语；不单独提交或接入不完整的确认HTTP。"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.modules.audit.service import record
from yuliang.modules.command.applications import JobApplications
from yuliang.modules.policy.models import RiskStage
from yuliang.modules.policy.repository import policy_lock
from yuliang.modules.policy.service import RiskFramework
from yuliang.platform.errors import ApiError, ErrorCode


class BudgetReservations:
    def __init__(self, applications: JobApplications) -> None:
        self.applications = applications

    def hold(
        self, session: Session, job_id: UUID, expected_version: int, key: str
    ) -> dict[str, str | bool]:
        """调用方须与confirm状态/事件同事务，任何失败回滚R/HELD与审计。"""
        identity = self.applications.identity
        if type(expected_version) is not int or expected_version < 1:
            raise ApiError(ErrorCode.INVALID_INPUT, "需要明确申请版本")
        try:
            policy_lock(session, identity.tenant_id, shared=True)
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760210))"),
                {"key": str(identity.tenant_id)},
            )
            existing = session.execute(
                text("SELECT id FROM job_budget_holds WHERE job_id=:id"), {"id": job_id}
            ).scalar_one_or_none()
            assessment = None
            if existing is None:
                assessment = (
                    RiskFramework(self.applications)
                    .require_job(session, job_id, RiskStage.CONFIRM, expected_version)
                    .assessment_id
                )
            result: dict[str, str | bool] = session.execute(
                text("""SELECT access.budget_hold(
                :member,:user,:job,:version,:key,:assessment,:request)"""),
                {
                    "member": identity.membership_id,
                    "user": identity.user_id,
                    "job": job_id,
                    "version": expected_version,
                    "key": key,
                    "assessment": assessment,
                    "request": self.applications.request_id,
                },
            ).scalar_one()
            record(
                session,
                identity,
                {
                    "action": "budget.reserve",
                    "object_type": "job",
                    "object_id": job_id,
                    "request_id": self.applications.request_id,
                },
            )
            return result
        except DBAPIError as error:
            mapping = {
                "42501": ErrorCode.FORBIDDEN,
                "P0002": ErrorCode.NO_DATA,
                "40001": ErrorCode.VERSION_CONFLICT,
                "P0210": ErrorCode.POLICY_VERSION_CHANGED,
                "23505": ErrorCode.IDEMPOTENCY_MISMATCH,
                "P0211": ErrorCode.TARGET_QUARANTINED,
                "P0212": ErrorCode.RISK_BLOCKED,
                "22023": ErrorCode.DATA_COVERAGE_INSUFFICIENT,
            }
            code = getattr(error.orig, "sqlstate", None)
            if code in mapping:
                raise ApiError(mapping[code], "当前额度、目标或申请条件不允许新增预占") from None
            raise
