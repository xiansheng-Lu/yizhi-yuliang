"""与V1共用授权查询，当前只投影实际ADQ广告申请。"""

from uuid import UUID

from sqlalchemy import text

from yuliang.modules.command.applications import JobApplications
from yuliang.modules.delivery.projections import ExecutionTask, execution_view
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class DeliveryJobQueries:
    def __init__(self, applications: JobApplications) -> None:
        self.applications = applications

    def get(self, job_id: UUID) -> ExecutionTask | None:
        with business_transaction(self.applications.engine, self.applications.identity) as session:
            projection = self.applications.read_projection(session, job_id)
            if projection.job.target_type != "AD_PLAN":
                return None
            pair = session.execute(
                text("SELECT provider,product_line FROM ad_accounts WHERE id=:id"),
                {"id": projection.job.account_id},
            ).one_or_none()
            if pair != ("tencent", "adq"):
                raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "该平台任务投影尚未接入")
            return execution_view(projection, platform="adq")
