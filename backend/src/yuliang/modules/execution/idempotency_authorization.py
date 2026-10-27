"""重放仍按当前动作/目标权限授权，不能靠旧成功快照绕过撤权。"""

from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.access.service import require_shop_account
from yuliang.modules.command.applications import ACTION_CAPABILITIES, JobApplications
from yuliang.modules.command.jobs import CreateJobRequest
from yuliang.modules.execution.guards import controlled
from yuliang.modules.execution.idempotency_models import ApiOperation, MutationInput
from yuliang.platform.errors import ApiError, ErrorCode


class IdempotencyAuthorization(Protocol):
    def reference(
        self, session: Session, operation: ApiOperation, payload: dict[str, Any]
    ) -> dict[str, Any]: ...
    def check(
        self, session: Session, operation: ApiOperation, reference: dict[str, Any]
    ) -> None: ...


class CommandAuthorization:
    def __init__(self, applications: JobApplications) -> None:
        self.applications = applications

    def reference(
        self, session: Session, operation: ApiOperation, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if operation == ApiOperation.CREATE:
            value = CreateJobRequest.model_validate(payload)
            target = self.applications.target(session, value)
            return {
                "version": "command-v1",
                "kind": "TARGET",
                "shop": str(value.shop_id),
                "target": str(value.target_id),
                "targetType": value.target_type,
                "account": str(target["account_id"]) if value.target_type == "AD_PLAN" else None,
                "permission": ACTION_CAPABILITIES[value.action_type][1]
                if value.target_type == "AD_PLAN"
                else "task.create",
            }
        mutation = MutationInput.model_validate(payload)
        reference = {"version": "command-v1", "kind": "JOB", "job": str(mutation.job_id)}
        self.check(session, operation, reference)
        return reference

    def check(self, session: Session, operation: ApiOperation, reference: dict[str, Any]) -> None:
        actor = self.applications.identity
        if reference.get("version") != "command-v1":
            raise ApiError(ErrorCode.FORBIDDEN, "幂等请求的授权合同不可用")
        if operation != ApiOperation.CREATE:
            event = {
                ApiOperation.CONFIRM: "CONFIRM",
                ApiOperation.APPROVE: "APPROVE",
                ApiOperation.REJECT: "REJECT",
                ApiOperation.CANCEL: "CANCEL",
                ApiOperation.SAFE_RETRY: "SAFE_RETRY",
            }[operation]
            if reference.get("kind") != "JOB":
                raise ApiError(ErrorCode.FORBIDDEN, "幂等请求授权不匹配")
            controlled(
                lambda: session.execute(
                    text("SELECT access.job_state_context(:member,:user,:job,:event)"),
                    {
                        "member": actor.membership_id,
                        "user": actor.user_id,
                        "job": UUID(reference["job"]),
                        "event": event,
                    },
                ).scalar_one()
            )
            return
        if (
            reference.get("kind") != "TARGET"
            or not session.execute(
                text("SELECT access.workspace_allowed(:member,:user)"),
                {"member": actor.membership_id, "user": actor.user_id},
            ).scalar_one()
        ):
            raise ApiError(ErrorCode.FORBIDDEN, "当前没有原请求的动作权限")
        shop = UUID(reference["shop"])
        self.applications.workspace.require_shop(session, shop)
        query = (
            "SELECT * FROM campaigns WHERE id=:target AND shop_id=:shop"
            if reference["targetType"] == "AD_PLAN"
            else "SELECT * FROM live_sessions WHERE id=:target AND shop_id=:shop"
        )
        target = (
            session.execute(text(query), {"target": UUID(reference["target"]), "shop": shop})
            .mappings()
            .first()
        )
        if target is None:
            raise ApiError(ErrorCode.NO_DATA, "原请求目标已不在当前范围")
        if reference["targetType"] == "AD_PLAN":
            if str(target["account_id"]) != reference["account"]:
                raise ApiError(ErrorCode.FORBIDDEN, "原请求目标归属已变化")
            for permission in ("task.create", reference["permission"]):
                require_shop_account(session, actor, shop, target["account_id"], permission)
