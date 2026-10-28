"""真实审批与原申请/资金同事务；HTTP不能选择SERVER_POLICY入口。"""

import hashlib
from contextlib import nullcontext
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from yuliang.modules.audit.service import record
from yuliang.modules.capability.service import maintenance
from yuliang.modules.command.applications import JobApplications
from yuliang.modules.command.jobs import CommandJob
from yuliang.modules.command.state import JobEvent, StateFacts, evolve
from yuliang.modules.connectors.redaction import canonical, fingerprint
from yuliang.modules.execution.approval_models import (
    ReviewJobRequest,
    ServerApprovalPermit,
    permit_matches,
)
from yuliang.modules.execution.guards import controlled
from yuliang.modules.execution.settlement import BudgetSettlement
from yuliang.modules.policy.models import RiskStage
from yuliang.modules.policy.service import RiskFramework
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class ApprovalTicket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    approval_id: UUID
    job_id: UUID
    resource_version: int = Field(strict=True, gt=0)
    version: int = Field(strict=True, gt=0)
    state: Literal["PENDING", "APPROVED", "REJECTED"]


class ApprovalDecisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    job: CommandJob
    approval_id: UUID
    approval_version: int = Field(strict=True, gt=0)
    decision_id: UUID
    method: Literal["MANUAL", "SERVER_POLICY"]


def register_server_permit(connection: Connection, permit: ServerApprovalPermit) -> None:
    """仅维护配置，默认空表；登记不是平台动作/金额授权。"""
    maintenance(connection)
    current = connection.execute(
        text("""SELECT p.id,p.version,v.config->>'mode' FROM risk_policies p JOIN policy_versions v
        ON (v.tenant_id,v.policy_id,v.version)=(p.tenant_id,p.id,p.version)
        WHERE p.tenant_id=:tenant"""),
        {"tenant": permit.tenant_id},
    ).one_or_none()
    if current != (permit.policy_id, permit.policy_version, "AUTOPILOT"):
        raise ValueError("后台白名单只能绑定当前AUTOPILOT策略")
    connection.execute(
        text("""INSERT INTO server_approval_permits(id,tenant_id,policy_id,policy_version,
        policy_actor_membership_id,account_id,campaign_id,action,max_change_minor,max_change_percent,
        important,classification_ref,valid_from,valid_until,proof_ref,active)
        VALUES(:id,:tenant_id,:policy_id,:policy_version,:policy_actor_membership_id,:account_id,
        :campaign_id,:action,:max_change_minor,:max_change_percent,:important,:classification_ref,
        :valid_from,:valid_until,:proof_ref,:active)"""),
        permit.model_dump(),
    )


class Approvals:
    def __init__(self, applications: JobApplications) -> None:
        self.applications = applications

    def _transaction(self, session: Session | None) -> Any:
        return (
            nullcontext(session)
            if session is not None
            else business_transaction(self.applications.engine, self.applications.identity)
        )

    def open(
        self, job_id: UUID, expected_version: int, *, unit_of_work: Session | None = None
    ) -> ApprovalTicket:
        actor = self.applications.identity
        if type(expected_version) is not int or expected_version < 1:
            raise ApiError(ErrorCode.INVALID_INPUT, "需要有效申请版本")
        with self._transaction(unit_of_work) as session:
            value = controlled(
                lambda: session.execute(
                    text("SELECT access.approval_open(:member,:user,:job,:version,:id)"),
                    {
                        "member": actor.membership_id,
                        "user": actor.user_id,
                        "job": job_id,
                        "version": expected_version,
                        "id": uuid4(),
                    },
                ).scalar_one()
            )
            item = value["approval"]
            ticket = ApprovalTicket(
                approval_id=item["id"],
                job_id=item["command_job_id"],
                resource_version=item["resource_version"],
                version=item["version"],
                state=item["state"],
            )
            if value["created"]:
                record(
                    session,
                    actor,
                    {
                        "action": "approval.request",
                        "object_type": "approval",
                        "object_id": ticket.approval_id,
                        "request_id": self.applications.request_id,
                        "after_hash": item["payload_digest"],
                    },
                )
            return ticket

    def review(
        self, request: ReviewJobRequest, *, unit_of_work: Session | None = None
    ) -> ApprovalDecisionResult:
        return self._decide(request, "MANUAL", None, unit_of_work)

    def approve_by_policy(
        self,
        job_id: UUID,
        expected_version: int,
        permit_id: UUID,
        *,
        unit_of_work: Session | None = None,
    ) -> ApprovalDecisionResult:
        """仅后台装配调用；原成员、当前策略、精确白名单仍由DB核对。"""
        return self._decide(
            ReviewJobRequest(job_id=job_id, expected_version=expected_version, decision="APPROVE"),
            "SERVER_POLICY",
            permit_id,
            unit_of_work,
        )

    def _decide(
        self,
        request: ReviewJobRequest,
        method: Literal["MANUAL", "SERVER_POLICY"],
        permit_id: UUID | None,
        unit_of_work: Session | None,
    ) -> ApprovalDecisionResult:
        actor = self.applications.identity
        with self._transaction(unit_of_work) as session:
            arguments: dict[str, Any] = {
                "member": actor.membership_id,
                "user": actor.user_id,
                "job": request.job_id,
                "version": request.expected_version,
            }
            query = "SELECT access.approval_context(:member,:user,:job,:version)"
            if method == "SERVER_POLICY":
                query = "SELECT access.approval_policy_context(:member,:user,:job,:version,:permit)"
                arguments["permit"] = permit_id
            context = controlled(lambda: session.execute(text(query), arguments).scalar_one())
            before = CommandJob.model_validate(context["job"]["document"])
            at = session.execute(text("SELECT clock_timestamp()")).scalar_one()
            if method == "SERVER_POLICY":
                permit = ServerApprovalPermit.model_validate(
                    {key: context["permit"][key] for key in ServerApprovalPermit.model_fields}
                )
                if not permit_matches(before, permit, at=at, actor_membership=actor.membership_id):
                    raise ApiError(ErrorCode.FORBIDDEN, "该申请需要人工审批或策略依据已失效")
            decision = None
            if request.decision == "APPROVE":
                decision = RiskFramework(self.applications).require_job(
                    session,
                    request.job_id,
                    RiskStage.APPROVE,
                    request.expected_version,
                    at=at,
                )
            after = evolve(
                before,
                JobEvent(request.decision),
                StateFacts(
                    risk=decision.summary if decision else None,
                    approval_method=method,
                    approval_policy_ref=permit_id,
                    proof_ref=f"approval-policy/{permit_id}" if permit_id else None,
                ),
                at=at,
                actor_id=actor.user_id,
                requester_id=UUID(context["job"]["created_by"]),
            )
            value = controlled(
                lambda: session.execute(
                    text("""SELECT access.approval_decide(
                :member,:user,:job,:version,:approval_version,
                :decision,:method,:risk,:permit,CAST(:document AS jsonb),:id,:request,:comment)"""),
                    {
                        "member": actor.membership_id,
                        "user": actor.user_id,
                        "job": request.job_id,
                        "version": request.expected_version,
                        "approval_version": context["approval"]["version"],
                        "decision": request.decision,
                        "method": method,
                        "risk": decision.assessment_id if decision else None,
                        "permit": permit_id,
                        "document": after.model_dump_json(),
                        "id": uuid4(),
                        "request": self.applications.request_id,
                        "comment": request.comment,
                    },
                ).scalar_one()
            )
            result = ApprovalDecisionResult(
                job=CommandJob.model_validate(value["job"]["document"]),
                approval_id=value["approval"]["id"],
                approval_version=value["approval"]["version"],
                decision_id=value["decision"]["id"],
                method=method,
            )
            if request.decision == "REJECT":
                BudgetSettlement(self.applications).release(request.job_id, unit_of_work=session)
            record(
                session,
                actor,
                {
                    "action": "approval.manual" if method == "MANUAL" else "approval.policy",
                    "object_type": "approval",
                    "object_id": result.approval_id,
                    "request_id": self.applications.request_id,
                    "after_hash": hashlib.sha256(
                        canonical({"decision": str(result.decision_id), "method": method})
                    ).hexdigest(),
                },
            )
            record(
                session,
                actor,
                {
                    "action": "job.transition",
                    "object_type": "job",
                    "object_id": request.job_id,
                    "request_id": self.applications.request_id,
                    "before_hash": context["approval"]["intent_hash"],
                    "after_hash": fingerprint(
                        self.applications.key,
                        "approval-result-v1",
                        canonical(result.job.model_dump(mode="json")),
                    ),
                    "detail": {"changed_fields": ["status"]},
                },
            )
            return result
