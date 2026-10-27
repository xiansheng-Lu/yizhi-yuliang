"""申请仓储与已确认现值解析；未接入四时点策略前默认阻断广告执行。"""

import json
import re
from contextlib import nullcontext
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import SecretStr, ValidationError
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yuliang.modules.access.service import require_shop_account
from yuliang.modules.audit.service import record
from yuliang.modules.capability.service import maintenance
from yuliang.modules.command.jobs import (
    Action,
    BooleanValue,
    CommandJob,
    CreateJobRequest,
    JobApproval,
    JobHistory,
    JobStatus,
    JobValue,
    MoneyValue,
    RatioValue,
)
from yuliang.modules.connectors.base import GatewayContext, ObjectRef
from yuliang.modules.connectors.freshness import ResourceFreshness
from yuliang.modules.connectors.redaction import canonical, fingerprint
from yuliang.modules.workspace.service import WorkspaceService
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

ACTION_CAPABILITIES = {
    "CHANGE_DAILY_BUDGET": ("budget", "budget.edit"),
    "CHANGE_BID": ("bid", "plan.write"),
    "SET_ROI_TARGET": ("goal", "goal.edit"),
    "PAUSE_AD_PLAN": ("pause", "plan.write"),
    "RESUME_AD_PLAN": ("resume", "plan.write"),
}


def register_value_source(
    connection: Connection,
    campaign_id: UUID,
    source_id: UUID,
    authorization_id: UUID,
    action: Action,
    field: str,
    proof_ref: str,
) -> None:
    maintenance(connection)
    if (
        action not in ACTION_CAPABILITIES
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", field)
        or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,180}", proof_ref)
    ):
        raise ValueError("现值映射需要明确动作、字段和来源依据")
    row = (
        connection.execute(
            text("""SELECT c.*,s.id AS source,a.id AS auth,b.operation_kind,
    b.provider AS source_provider,b.product_line AS source_product FROM campaigns c
    JOIN sync_sources s ON (s.tenant_id,s.account_id)=(c.tenant_id,c.account_id)
    JOIN capability_bindings b ON (b.tenant_id,b.id)=(s.tenant_id,s.binding_id)
    JOIN provider_authorizations a ON (a.tenant_id,a.provider,a.product_line)
      =(c.tenant_id,c.provider,c.product_line)
    WHERE c.id=:campaign AND s.id=:source AND a.id=:auth AND a.status='ACTIVE'
    AND a.expires_at>clock_timestamp() AND b.active AND s.enabled
    AND EXISTS(SELECT 1 FROM authorization_account_links l WHERE
      (l.tenant_id,l.authorization_id,l.account_id)=(c.tenant_id,a.id,c.account_id))"""),
            {"campaign": campaign_id, "source": source_id, "auth": authorization_id},
        )
        .mappings()
        .first()
    )
    if (
        row is None
        or row["operation_kind"] != "READ"
        or (row["provider"], row["product_line"]) != (row["source_provider"], row["source_product"])
    ):
        raise ValueError("现值源与已授权内部计划的账户/产品不匹配")
    connection.execute(
        text("""INSERT INTO command_value_sources(tenant_id,campaign_id,shop_id,
    account_id,source_id,authorization_id,provider,product_line,action_type,value_field,proof_ref)
    VALUES(:tenant,:campaign,:shop,:account,:source,:auth,:provider,:product,:action,:field,:proof)"""),
        {
            "tenant": row["tenant_id"],
            "campaign": campaign_id,
            "shop": row["shop_id"],
            "account": row["account_id"],
            "source": source_id,
            "auth": authorization_id,
            "provider": row["provider"],
            "product": row["product_line"],
            "action": action,
            "field": field,
            "proof": proof_ref,
        },
    )


class JobApplications:
    def __init__(
        self, engine: Engine, identity: VerifiedIdentity, request_id: UUID, audit_key: SecretStr
    ) -> None:
        self.engine, self.identity, self.request_id, self.key = (
            engine,
            identity,
            request_id,
            audit_key,
        )
        self.workspace = WorkspaceService(engine, identity, request_id)

    def target(self, session: Session, value: CreateJobRequest) -> dict[str, Any]:
        if not session.execute(
            text("SELECT access.workspace_allowed(:member,:user)"),
            {"member": self.identity.membership_id, "user": self.identity.user_id},
        ).scalar_one():
            raise ApiError(ErrorCode.FORBIDDEN, "没有任务申请权限")
        self.workspace.require_shop(session, value.shop_id)
        query = (
            "SELECT * FROM campaigns WHERE id=:id AND shop_id=:shop"
            if value.target_type == "AD_PLAN"
            else "SELECT * FROM live_sessions WHERE id=:id AND shop_id=:shop"
        )
        row = (
            session.execute(
                text(query),
                {"id": value.target_id, "shop": value.shop_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError(ErrorCode.NO_DATA, "没有可申请的内部目标")
        if value.target_type == "AD_PLAN":
            for permission in ("task.create", ACTION_CAPABILITIES[value.action_type][1]):
                require_shop_account(
                    session, self.identity, value.shop_id, row["account_id"], permission
                )
        return dict(row)

    def current_value(
        self, session: Session, value: CreateJobRequest, target: dict[str, Any]
    ) -> tuple[JobValue | None, dict[str, Any]]:
        if value.target_type == "LIVE_SESSION":
            return None, {
                "kind": "MANUAL_HANDOFF",
                "targetVersion": target["updated_at"].isoformat(),
            }
        session.execute(
            text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,760117))"),
            {"key": f"{self.identity.tenant_id}|{target['account_id']}"},
        )
        mapping = (
            session.execute(
                text("""SELECT m.*,b.id AS binding_id,b.connector_id,b.revision,
        b.catalog_id,b.catalog_sha256,a.native_id AS native_account FROM command_value_sources m
        JOIN sync_sources s ON (s.tenant_id,s.id)=(m.tenant_id,m.source_id)
        JOIN capability_bindings b ON (b.tenant_id,b.id)=(s.tenant_id,s.binding_id)
        JOIN ad_accounts a ON (a.tenant_id,a.id)=(m.tenant_id,m.account_id)
        WHERE m.campaign_id=:campaign AND m.action_type=:action"""),
                {"campaign": value.target_id, "action": value.action_type},
            )
            .mappings()
            .first()
        )
        if mapping is None:
            raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "目标现值源尚未核验")
        session.execute(
            text("SELECT id FROM sync_sources WHERE id=:id FOR SHARE"), {"id": mapping["source_id"]}
        )
        ctx = GatewayContext(
            self.identity,
            value.shop_id,
            target["account_id"],
            mapping["native_account"],
            mapping["connector_id"],
            mapping["provider"],
            mapping["product_line"],
            mapping["authorization_id"],
            self.request_id,
            mapping["binding_id"],
            mapping["revision"],
            mapping["catalog_id"],
            mapping["catalog_sha256"],
        )
        health = ResourceFreshness(self.engine).inspect(session, ctx, mapping["source_id"])
        policy = health["policy"]
        expected_kind = (
            "INTEGER"
            if value.action_type in {"CHANGE_DAILY_BUDGET", "CHANGE_BID"}
            else "NUMBER"
            if value.action_type == "SET_ROI_TARGET"
            else "BOOLEAN"
        )
        if (
            health["status"] != "FRESH"
            or policy is None
            or policy.target_mode != "OBJECT"
            or (mapping["value_field"], expected_kind) not in policy.required_fields
        ):
            raise ApiError(
                ErrorCode.DATA_COVERAGE_INSUFFICIENT, "当前计划值未知、过期或字段契约未核验"
            )
        obj = ObjectRef(
            tenant_id=self.identity.tenant_id,
            account_id=target["account_id"],
            connector_id=ctx.connector_id,
            provider=ctx.provider,
            product_line=ctx.product_line,
            native_id=target["native_id"],
        )
        if not ResourceFreshness.required_data(session, ctx, health, obj):
            raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "最新完整批次没有当前目标字段")
        mirror = (
            session.execute(
                text("""SELECT m.projection,m.outbound_id FROM sync_resource_mirrors m
        JOIN sync_pages p ON (p.tenant_id,p.outbound_id)=(m.tenant_id,m.outbound_id)
        WHERE m.source_id=:source AND m.native_id=:native AND p.run_id=:run"""),
                {
                    "source": mapping["source_id"],
                    "native": target["native_id"],
                    "run": health["successRunId"],
                },
            )
            .mappings()
            .one()
        )
        actual = mirror["projection"]["data"][mapping["value_field"]]
        try:
            old: JobValue = (
                MoneyValue(amount_minor=actual)
                if expected_kind == "INTEGER"
                else RatioValue(value=actual)
                if expected_kind == "NUMBER"
                else BooleanValue(value=actual)
            )
        except ValidationError:
            raise ApiError(
                ErrorCode.DATA_COVERAGE_INSUFFICIENT, "现值类型、单位或范围未通过"
            ) from None
        return old, {
            "sourceId": str(mapping["source_id"]),
            "outboundId": str(mirror["outbound_id"]),
            "field": mapping["value_field"],
            "policyRevision": health["policyRevision"],
            "sourceUpdatedAt": health["sourceUpdatedAt"].isoformat()
            if health["sourceUpdatedAt"]
            else None,
            "fetchedAt": health["fetchedAt"].isoformat(),
        }

    def create(self, value: CreateJobRequest, *, unit_of_work: Session | None = None) -> CommandJob:
        payload = value.model_dump(mode="json") | {
            "creator": str(self.identity.user_id),
            "tenant": self.identity.tenant_id,
        }
        digest = fingerprint(self.key, "job-request-v1", canonical(payload))
        try:
            transaction = (
                nullcontext(unit_of_work)
                if unit_of_work is not None
                else business_transaction(self.engine, self.identity)
            )
            with transaction as session:
                target = self.target(session, value)
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760205))"),
                    {"key": f"{self.identity.tenant_id}|{value.idempotency_key}"},
                )
                previous = (
                    session.execute(
                        text("SELECT * FROM command_jobs WHERE idempotency_key=:key"),
                        {"key": value.idempotency_key},
                    )
                    .mappings()
                    .first()
                )
                if previous:
                    if previous["request_digest"] != digest:
                        raise ApiError(ErrorCode.IDEMPOTENCY_MISMATCH, "同一申请键已用于不同内容")
                    return self._project(session, dict(previous))
                from yuliang.modules.policy.repository import policy_lock
                from yuliang.modules.policy.service import RiskFramework

                policy_lock(session, self.identity.tenant_id, shared=True)
                old, proof = self.current_value(session, value, target)
                now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
                manual = value.target_type == "LIVE_SESSION"
                job_id = uuid4()
                risk = (
                    RiskFramework(self)
                    .assess_create(session, value, target, old, proof, job_id, now)
                    .summary
                )
                status = (
                    JobStatus.MANUAL_REQUIRED
                    if manual
                    else (
                        JobStatus.DRAFT if risk.result == "REVIEW_REQUIRED" else JobStatus.BLOCKED
                    )
                )
                job = CommandJob(
                    job_id=str(job_id),
                    source=value.source,
                    tenant_id=str(self.identity.tenant_id),
                    shop_id=str(value.shop_id),
                    account_id=str(target["account_id"]) if not manual else None,
                    live_session_id=str(value.target_id) if manual else None,
                    target_type=value.target_type,
                    target_id=str(value.target_id),
                    action_type=value.action_type,
                    old_value=old,
                    new_value=value.new_value,
                    intent=value.intent,
                    status=status,
                    created_at=now,
                    updated_at=now,
                    expires_at=now + timedelta(minutes=15),
                    risk_summary=risk,
                    approval=JobApproval(),
                    execution_receipt=None,
                    error=None,
                    version=1,
                    idempotency_key=value.idempotency_key,
                    history=(
                        JobHistory(
                            status=status,
                            at=now,
                            detail="已记录人工交接申请"
                            if manual
                            else "申请已保存，执行策略尚未就绪",
                        ),
                    ),
                )
                from yuliang.modules.policy.intent import seal_intent

                intent_hash = seal_intent(self.key, job, self.identity.user_id, proof)
                session.execute(
                    text("""INSERT INTO command_jobs(id,tenant_id,created_by,shop_id,account_id,
                live_session_id,status,created_at,updated_at,document,target_type,target_id,campaign_id,
                request_digest,intent_hash,idempotency_key,version,expires_at,target_proof)
                VALUES(:id,:tenant,:user,:shop,:account,:live,:status,:at,:at,
                CAST(:document AS jsonb),
                :type,:target,:campaign,:digest,:intent,:key,1,:expires,CAST(:proof AS jsonb))"""),
                    {
                        "id": UUID(job.job_id),
                        "tenant": self.identity.tenant_id,
                        "user": self.identity.user_id,
                        "shop": value.shop_id,
                        "account": target.get("account_id"),
                        "live": value.target_id if manual else None,
                        "status": status.value,
                        "at": now,
                        "document": job.model_dump_json(),
                        "type": value.target_type,
                        "target": value.target_id,
                        "campaign": None if manual else value.target_id,
                        "digest": digest,
                        "intent": intent_hash,
                        "key": value.idempotency_key,
                        "expires": job.expires_at,
                        "proof": json.dumps(proof),
                    },
                )
                session.execute(
                    text("""INSERT INTO job_events(tenant_id,job_id,to_status,actor_id,
                request_id,reason_code,detail,created_at)
                VALUES(:tenant,:job,:status,:actor,:request,
                :reason,:detail,:at)"""),
                    {
                        "tenant": self.identity.tenant_id,
                        "job": UUID(job.job_id),
                        "status": status.value,
                        "actor": self.identity.user_id,
                        "request": self.request_id,
                        "reason": "MANUAL_HANDOFF" if manual else "POLICY_NOT_READY",
                        "detail": job.history[0].detail,
                        "at": now,
                    },
                )
                record(
                    session,
                    self.identity,
                    {
                        "action": "job.create",
                        "object_type": "job",
                        "object_id": UUID(job.job_id),
                        "request_id": self.request_id,
                        "after_hash": intent_hash,
                    },
                )
                return job
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) == "23505":
                raise ApiError(
                    ErrorCode.IDEMPOTENCY_MISMATCH, "申请键已被使用，请核对原申请"
                ) from None
            raise

    @staticmethod
    def _project(session: Session, row: dict[str, Any]) -> CommandJob:
        if row["document"] is None:
            raise ApiError(ErrorCode.NO_DATA, "该标识尚无完整申请，不生成虚假任务")
        history = [
            dict(status=e.to_status, at=e.created_at, detail=e.detail)
            for e in session.execute(
                text(
                    "SELECT to_status,created_at,detail FROM job_events "
                    "WHERE job_id=:id ORDER BY id"
                ),
                {"id": row["id"]},
            )
        ]
        return CommandJob.model_validate(row["document"] | {"history": history})

    def get(self, job_id: UUID) -> CommandJob:
        with business_transaction(self.engine, self.identity) as session:
            row = (
                session.execute(text("SELECT * FROM command_jobs WHERE id=:id"), {"id": job_id})
                .mappings()
                .first()
            )
            if row is None:
                raise ApiError(ErrorCode.NO_DATA, "没有当前范围内的申请")
            document = row["document"]
            if document is None:
                raise ApiError(ErrorCode.NO_DATA, "尚无完整申请")
            self.target(
                session,
                CreateJobRequest.model_validate(
                    {k: document[k] for k in CreateJobRequest.model_fields}
                ),
            )
            return self._project(session, dict(row))
