"""独立GET只读端口：真实留档+READBACK配额，不推进任务、不结算、不重发写。"""

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.command.applications import JobApplications
from yuliang.modules.command.jobs import Action, CommandJob
from yuliang.modules.connectors.archive import ArchiveUnavailable
from yuliang.modules.connectors.base import GatewayContext, ObjectRef
from yuliang.modules.connectors.governance import CallDeferred, GovernanceUnavailable
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import OperationSpec
from yuliang.modules.connectors.redaction import canonical
from yuliang.modules.execution.guards import controlled
from yuliang.modules.execution.readback_values import (
    ParameterComparison,
    ParameterReadContract,
    compare_parameter,
)
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

NAMESPACE = UUID("39d539dc-407a-5cee-a647-b74c04889ecd")


@dataclass(frozen=True)
class ParameterReadPort:
    spec: OperationSpec
    contract: ParameterReadContract
    parameters: Callable[[GatewayContext, ObjectRef], dict[str, Any]]

    def __post_init__(self) -> None:
        if (
            self.spec.effect != "READ"
            or self.spec.method != "GET"
            or self.spec.max_read_attempts != 1
            or not self.spec.idempotent_read
        ):
            raise ValueError("参数核验必须是单次独立GET，重调度交给持久任务")
        if self.spec.account_parameter is None or self.spec.target_parameter is None:
            raise ValueError("参数回读必须绑定实际账户和目标")

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            canonical(
                {
                    "contract": self.contract.model_dump(mode="json"),
                    "operation": self.spec.key,
                    "catalog": self.spec.catalog_sha256,
                    "schema": self.spec.parameters_model.model_json_schema(),
                }
            )
        ).hexdigest()


class ReadbackObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    check_id: UUID
    execution_id: UUID
    attempt_id: UUID
    write_outbound_id: UUID
    read_outbound_id: UUID | None = None
    raw_id: int | None = None
    request_id: UUID | None = None
    contract_digest: str
    checked_at: AwareDatetime
    comparison: ParameterComparison
    unavailable_cause: str | None = None
    retry_at: AwareDatetime | None = None


class IndependentReadback:
    def __init__(
        self,
        applications: JobApplications,
        client: GovernedPlatformClient | None = None,
        ports: Mapping[Action, ParameterReadPort] | None = None,
    ) -> None:
        self.applications, self.client, self.ports = applications, client, dict(ports or {})
        if any(action != port.contract.action for action, port in self.ports.items()):
            raise ValueError("端口动作与参数合同不一致")

    def _source(self, session: Session, attempt_id: UUID, ctx: GatewayContext) -> dict[str, Any]:
        actor = self.applications.identity
        if ctx.identity != actor:
            raise ApiError(ErrorCode.FORBIDDEN, "回读身份与原申请人不一致")
        row = (
            session.execute(
                text("""SELECT a.*,o.command_job_id,o.current_attempt,o.status AS order_status,
            j.document,j.target_proof,c.native_id,m.source_id,m.value_field,
            m.authorization_id AS read_authorization,
            s.binding_id AS read_binding,p.revision AS field_revision,
            (SELECT max(raw.created_at) FROM execution_receipts r JOIN platform_raw_payloads raw
             ON (raw.tenant_id,raw.id)=(r.tenant_id,r.raw_id)
             WHERE r.attempt_id=a.id AND r.applied AND r.accepted IS TRUE) AS accepted_at
            FROM execution_attempts a JOIN execution_orders o
             ON (o.tenant_id,o.id)=(a.tenant_id,a.order_id)
            JOIN command_jobs j ON (j.tenant_id,j.id)=(o.tenant_id,o.command_job_id)
            JOIN campaigns c ON (c.tenant_id,c.id)=(j.tenant_id,j.campaign_id)
            JOIN command_value_sources m ON m.tenant_id=j.tenant_id AND m.campaign_id=c.id
             AND m.action_type=j.document->>'action_type'
            JOIN sync_sources s ON (s.tenant_id,s.id)=(m.tenant_id,m.source_id)
            JOIN resource_freshness_policies p
             ON (p.tenant_id,p.source_id)=(m.tenant_id,m.source_id)
            WHERE a.id=:id AND o.biz_type='COMMAND_JOB' AND o.created_by=:user"""),
                {"id": attempt_id, "user": actor.user_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError(ErrorCode.NO_DATA, "没有当前原申请的参数回读依据")
        data = dict(row)
        controlled(
            lambda: session.execute(
                text("SELECT access.job_state_context(:member,:user,:job,'CLAIM')"),
                {
                    "member": actor.membership_id,
                    "user": actor.user_id,
                    "job": data["command_job_id"],
                },
            ).scalar_one()
        )
        job = CommandJob.model_validate(data["document"])
        if (
            data["current_attempt"] != data["attempt_no"]
            or data["order_status"] not in {"EXECUTING", "VERIFYING", "UNKNOWN"}
            or job.status.value not in {"EXECUTING", "VERIFYING"}
        ):
            raise ApiError(
                ErrorCode.ILLEGAL_TRANSITION, "旧尝试或终态只查询历史，不新增参数核验调用"
            )
        if (
            str(data["source_id"]) != data["target_proof"].get("sourceId")
            or data["field_revision"] != data["target_proof"].get("policyRevision")
            or data["value_field"] != data["target_proof"].get("field")
            or data["read_binding"] != ctx.binding_id
            or data["read_authorization"] != ctx.authorization_id
            or str(ctx.account_id) != job.account_id
            or str(ctx.shop_id) != job.shop_id
        ):
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "原申请参数来源合同或回读范围已改变")
        data["job"] = job
        return data

    async def inspect(
        self,
        attempt_id: UUID,
        ctx: GatewayContext,
        credential: SecretStr,
        check_id: UUID,
    ) -> ReadbackObservation:
        if self.client is None:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "参数回读端口尚未装配")
        with business_transaction(self.applications.engine, self.applications.identity) as session:
            source = self._source(session, attempt_id, ctx)
        job = source["job"]
        port = self.ports.get(job.action_type)
        if port is None or port.contract.canonical_field != source["value_field"]:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "该参数的回读字段或精度尚未核验")
        if not port.contract.archive_fields <= self.client.client.policy.fields:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "回读字段尚未进入脱敏档案白名单")
        target = ObjectRef(
            tenant_id=ctx.identity.tenant_id,
            account_id=ctx.account_id,
            connector_id=ctx.connector_id,
            provider=ctx.provider,
            product_line=ctx.product_line,
            native_id=source["native_id"],
        )
        outbound = uuid5(
            NAMESPACE, f"{ctx.identity.tenant_id}|{attempt_id}|{check_id}|{port.digest}"
        )
        cause, retry_at = None, None

        def guard(session: Session) -> None:
            self._source(session, attempt_id, ctx)

        try:
            await self.client.send(
                ctx,
                port.spec,
                outbound,
                port.parameters(ctx, target),
                credential,
                lane="READBACK",
                target=target,
                execution_id=source["order_id"],
                attempt_id=attempt_id,
                before_persist=guard,
            )
        except CallDeferred as deferred:
            cause, retry_at = deferred.decision.reason, deferred.decision.retry_at
        except GovernanceUnavailable:
            cause = "GOVERNANCE_UNAVAILABLE"
        except ArchiveUnavailable:
            cause = "ARCHIVE_UNAVAILABLE"
        with business_transaction(self.applications.engine, self.applications.identity) as session:
            checked = session.execute(text("SELECT clock_timestamp()")).scalar_one()
            # 归档本身会重放只读旧结果；从真实保存的投影取值，首次与重放采用同一路径。
            raw = (
                session.execute(
                    text("""SELECT raw.*,root.request_id FROM platform_requests root
                LEFT JOIN LATERAL(SELECT * FROM platform_raw_payloads p WHERE p.outbound_id=root.id
                  ORDER BY p.id DESC LIMIT 1) raw ON true
                WHERE root.id=:id AND root.effect='READ' AND root.method='GET'
                 AND root.execution_id=:execution AND root.execution_attempt_id=:attempt"""),
                    {"id": outbound, "execution": source["order_id"], "attempt": attempt_id},
                )
                .mappings()
                .first()
            )
            comparison = ParameterComparison(status="UNAVAILABLE", reason="READ_UNAVAILABLE")
            if (
                raw is not None
                and raw["phase"] == "RESPONSE"
                and raw["external_outcome"] == "READ_SUCCEEDED"
            ):
                comparison = compare_parameter(
                    raw["payload"]["data"],
                    port.contract,
                    job.new_value,
                    source["native_id"],
                    fetched_at=raw["created_at"],
                    checked_at=checked,
                    accepted_at=source["accepted_at"],
                )
            return ReadbackObservation(
                check_id=check_id,
                execution_id=source["order_id"],
                attempt_id=attempt_id,
                write_outbound_id=source["outbound_id"],
                read_outbound_id=outbound if raw else None,
                raw_id=raw["id"] if raw else None,
                request_id=raw["request_id"] if raw else None,
                contract_digest=port.digest,
                checked_at=checked,
                comparison=comparison,
                unavailable_cause=cause
                or (raw["category"] if raw and comparison.status == "UNAVAILABLE" else None),
                retry_at=retry_at,
            )
