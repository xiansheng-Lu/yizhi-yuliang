"""资源快照两时间与数据写资格；不替代能力、风控、审批或资金许可。"""

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session

from yuliang.modules.access.service import require_shop_account
from yuliang.modules.capability.service import maintenance
from yuliang.modules.connectors.base import GatewayContext, ObjectRef
from yuliang.modules.connectors.http import OperationSpec
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class ResourcePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    resource_key: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,59}$")
    max_age_seconds: int = Field(strict=True, ge=1, le=2592000)
    refresh_seconds: int = Field(strict=True, ge=1, le=2592000)
    clock_kind: Literal["SOURCE", "FETCHED"] = "SOURCE"
    target_mode: Literal["ACCOUNT", "OBJECT"]
    required_fields: tuple[tuple[str, Literal["INTEGER", "NUMBER", "BOOLEAN", "CODE"]], ...] = ()
    required_metrics: tuple[
        tuple[str, Literal["MINOR", "BEAN", "COUNT", "RATE", "RATIO", "HOURS"]], ...
    ] = ()

    @model_validator(mode="after")
    def verified_contract(self) -> "ResourcePolicy":
        if self.refresh_seconds > self.max_age_seconds or not (
            self.required_fields or self.required_metrics
        ):
            raise ValueError("资源须有明确字段/指标契约及刷新边界")
        for entries in (self.required_fields, self.required_metrics):
            if (
                len(entries) > 32
                or len({name for name, _ in entries}) != len(entries)
                or any(not re.fullmatch(r"[a-z][a-z0-9_.]{0,39}", name) for name, _ in entries)
            ):
                raise ValueError("资源契约字段不合法")
        return self


def _config_lock(
    session: Connection | Session, tenant: int, account: UUID, *, shared: bool = False
) -> None:
    statement = (
        "SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,760116))"
        if shared
        else "SELECT pg_advisory_xact_lock(hashtextextended(:key,760116))"
    )
    session.execute(text(statement), {"key": f"{tenant}|{account}"})


def configure_policy(
    connection: Connection, source_id: UUID, policy: ResourcePolicy, proof_ref: str
) -> None:
    maintenance(connection)
    if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,180}", proof_ref):
        raise ValueError("需要核对证据引用")
    source = (
        connection.execute(text("SELECT * FROM sync_sources WHERE id=:id"), {"id": source_id})
        .mappings()
        .one()
    )
    _config_lock(connection, source["tenant_id"], source["account_id"])
    previous = (
        connection.execute(
            text("SELECT * FROM resource_freshness_policies WHERE source_id=:id"), {"id": source_id}
        )
        .mappings()
        .first()
    )
    if previous:
        if previous["resource_key"] != policy.resource_key:
            raise ValueError("资源键不可改写，须建立新的源契约")
        old = ResourcePolicy.model_validate(
            {name: previous[name] for name in ResourcePolicy.model_fields}
        )
        if old == policy and previous["proof_ref"] == proof_ref:
            return
    values = policy.model_dump()
    connection.execute(
        text("""INSERT INTO resource_freshness_policies(tenant_id,source_id,account_id,
        resource_key,max_age_seconds,refresh_seconds,clock_kind,target_mode,required_fields,required_metrics,
        revision,proof_ref) VALUES(:tenant,:source,:account,:resource_key,
        :max_age_seconds,:refresh_seconds,
        :clock_kind,:target_mode,CAST(:fields AS jsonb),CAST(:metrics AS jsonb),1,:proof)
        ON CONFLICT(tenant_id,source_id) DO UPDATE SET max_age_seconds=EXCLUDED.max_age_seconds,
        refresh_seconds=EXCLUDED.refresh_seconds,clock_kind=EXCLUDED.clock_kind,target_mode=EXCLUDED.target_mode,
        required_fields=EXCLUDED.required_fields,required_metrics=EXCLUDED.required_metrics,
        revision=resource_freshness_policies.revision+1,proof_ref=EXCLUDED.proof_ref"""),
        values
        | {
            "tenant": source["tenant_id"],
            "source": source_id,
            "account": source["account_id"],
            "fields": json.dumps(policy.required_fields),
            "metrics": json.dumps(policy.required_metrics),
            "proof": proof_ref,
        },
    )


def register_requirement(
    connection: Connection,
    write_binding_id: UUID,
    source_id: UUID,
    target_native_id: str | None,
    proof_ref: str,
) -> None:
    maintenance(connection)
    policy = (
        connection.execute(
            text("SELECT * FROM resource_freshness_policies WHERE source_id=:id"), {"id": source_id}
        )
        .mappings()
        .one()
    )
    _config_lock(connection, policy["tenant_id"], policy["account_id"])
    binding = (
        connection.execute(
            text("SELECT * FROM capability_bindings WHERE id=:id"), {"id": write_binding_id}
        )
        .mappings()
        .one()
    )
    if (
        (binding["tenant_id"], binding["account_id"], binding["operation_kind"])
        != (policy["tenant_id"], policy["account_id"], "WRITE")
        or (policy["target_mode"] == "OBJECT") != (target_native_id is not None)
        or target_native_id is not None
        and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", target_native_id)
        or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,180}", proof_ref)
    ):
        raise ValueError("写资源范围或核对证据不匹配")
    connection.execute(
        text("""INSERT INTO write_resource_requirements(tenant_id,account_id,
        write_binding_id,source_id,resource_key,target_native_id,policy_revision,proof_ref)
        VALUES(:tenant,:account,:binding,:source,:key,:target,:version,:proof)
        ON CONFLICT(tenant_id,write_binding_id,resource_key,target_native_id) DO UPDATE
        SET source_id=EXCLUDED.source_id,policy_revision=EXCLUDED.policy_revision,
        proof_ref=EXCLUDED.proof_ref"""),
        {
            "tenant": policy["tenant_id"],
            "account": policy["account_id"],
            "binding": write_binding_id,
            "source": source_id,
            "key": policy["resource_key"],
            "target": target_native_id,
            "version": policy["revision"],
            "proof": proof_ref,
        },
    )


class ResourceFreshness:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def view(self, ctx: GatewayContext, source_id: UUID) -> dict[str, Any]:
        with business_transaction(self.engine, ctx.identity) as session:
            return self.inspect(session, ctx, source_id)

    def inspect(self, session: Session, ctx: GatewayContext, source_id: UUID) -> dict[str, Any]:
        require_shop_account(session, ctx.identity, ctx.shop_id, ctx.account_id, "overview.read")
        source = (
            session.execute(
                text("""SELECT s.*,b.provider,b.product_line,b.active,
            EXISTS(SELECT 1 FROM catalog_head h WHERE h.catalog_sha256=b.catalog_sha256)
            AS catalog_current FROM sync_sources s JOIN capability_bindings b
            ON (b.tenant_id,b.id)=(s.tenant_id,s.binding_id)
            WHERE s.id=:id AND s.account_id=:account"""),
                {"id": source_id, "account": ctx.account_id},
            )
            .mappings()
            .first()
        )
        if source is None or (source["provider"], source["product_line"]) != (
            ctx.provider,
            ctx.product_line,
        ):
            raise ApiError(ErrorCode.NO_DATA, "资源不在当前范围")
        now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
        policy_row = (
            session.execute(
                text("SELECT * FROM resource_freshness_policies WHERE source_id=:id"),
                {"id": source_id},
            )
            .mappings()
            .first()
        )
        policy = None
        if policy_row:
            try:
                policy = ResourcePolicy.model_validate(
                    {name: policy_row[name] for name in ResourcePolicy.model_fields}
                )
            except ValueError:
                policy = None
        success = (
            session.execute(
                text("""SELECT * FROM sync_runs WHERE source_id=:id AND state='SUCCESS'
            ORDER BY source_version DESC LIMIT 1"""),
                {"id": source_id},
            )
            .mappings()
            .first()
        )
        version = success["source_version"] if success else 0
        clocks = None
        if success:
            clocks = (
                session.execute(
                    text("""SELECT count(*) AS pages,
                CASE WHEN count(source_updated_at)=count(*) THEN min(source_updated_at)
                END AS source_at,
                CASE WHEN count(fetched_at)=count(*) THEN min(fetched_at) END AS fetched_at,
                coalesce(bool_or(source_updated_at>fetched_at),false) AS clock_skew
                FROM sync_pages WHERE run_id=:id"""),
                    {"id": success["id"]},
                )
                .mappings()
                .one()
            )
        failure = session.execute(
            text("""SELECT coalesce(i.last_category,'RESULT_UNKNOWN') FROM sync_page_intents i
            JOIN sync_runs r ON (r.tenant_id,r.id)=(i.tenant_id,i.run_id)
            WHERE r.source_id=:source AND r.source_version-r.page_no+i.page_no+1>:version
            AND (i.last_phase='ERROR' OR (i.last_phase='RESPONSE'
              AND i.last_category<>'READ_SUCCEEDED')
              OR (i.last_phase='ATTEMPT' AND r.lease_until<clock_timestamp()))
            ORDER BY i.last_observed_at DESC LIMIT 1"""),
            {"source": source_id, "version": version},
        ).scalar_one_or_none()
        incomplete = session.execute(
            text("""SELECT state FROM sync_runs WHERE source_id=:id
            AND source_version>=:version AND state IN ('PARTIAL','FAILED')
            ORDER BY source_version DESC,started_at DESC LIMIT 1"""),
            {"id": source_id, "version": version},
        ).scalar_one_or_none()
        source_at = clocks["source_at"] if clocks else None
        fetched_at = clocks["fetched_at"] if clocks else None
        status = "UNKNOWN"
        reason: str | None = "NO_CONFIRMED_DATA"
        age = None
        available = bool(
            success and clocks and success["page_no"] > 0 and clocks["pages"] == success["page_no"]
        )
        if available and success is not None and clocks is not None:
            status, reason = "FRESH", None
            if not source["enabled"]:
                status, reason = "DEGRADED", "SYNC_PAUSED"
            elif not source["active"] or not source["catalog_current"]:
                status, reason = "DEGRADED", "CAPABILITY_UNAVAILABLE"
            elif failure or incomplete:
                status, reason = "DEGRADED", failure or "PARTIAL_SNAPSHOT"
            elif policy is None:
                status, reason = "UNKNOWN", "POLICY_UNCONFIGURED"
            elif fetched_at is None:
                status, reason = "UNKNOWN", "FETCH_TIME_UNKNOWN"
            elif clocks["clock_skew"] or fetched_at is not None and fetched_at > now:
                status, reason = "UNKNOWN", "CLOCK_SKEW"
            else:
                anchor = source_at if policy.clock_kind == "SOURCE" else fetched_at
                if anchor is None:
                    status, reason = (
                        "UNKNOWN",
                        "SOURCE_TIME_UNKNOWN"
                        if policy.clock_kind == "SOURCE"
                        else "FETCH_TIME_UNKNOWN",
                    )
                elif anchor > now:
                    status, reason = "UNKNOWN", "CLOCK_SKEW"
                else:
                    delta = now - anchor
                    age = math.ceil(delta.total_seconds())
                    if delta > timedelta(seconds=policy.max_age_seconds):
                        status, reason = "STALE", "MAX_AGE_EXCEEDED"
        auth = (
            session.execute(
                text("""SELECT status,expires_at FROM provider_authorizations WHERE id=:id
            AND provider=:provider AND product_line=:product
            AND (account_id IS NULL OR account_id=:account)"""),
                {
                    "id": ctx.authorization_id,
                    "provider": ctx.provider,
                    "product": ctx.product_line,
                    "account": ctx.account_id,
                },
            )
            .mappings()
            .first()
        )
        auth_state = (
            "UNKNOWN"
            if auth is None
            else "EXPIRED"
            if auth["status"] == "ACTIVE" and auth["expires_at"] <= now
            else auth["status"]
        )
        if status == "FRESH" and auth_state != "ACTIVE":
            status, reason = (
                "DEGRADED",
                "AUTH_EXPIRED" if auth_state == "EXPIRED" else "AUTH_REQUIRED",
            )
        return {
            "sourceId": str(source_id),
            "resourceKey": policy.resource_key if policy else source["resource_kind"],
            "status": status,
            "reason": reason,
            "dataAvailable": available,
            "sourceUpdatedAt": source_at,
            "fetchedAt": fetched_at,
            "queriedAt": now,
            "maxAgeSeconds": policy.max_age_seconds if policy else None,
            "refreshSeconds": policy.refresh_seconds if policy else None,
            "clock": policy.clock_kind if policy else None,
            "ageSeconds": age,
            "successRunId": success["id"] if success else None,
            "policy": policy,
            "policyRevision": policy_row["revision"] if policy_row else None,
            "authorizationState": auth_state,
            "authorizationExpiresAt": auth["expires_at"] if auth else None,
        }

    @staticmethod
    def required_data(
        session: Session, ctx: GatewayContext, health: dict[str, Any], target: ObjectRef | None
    ) -> bool:
        policy: ResourcePolicy = health["policy"]
        native = target.native_id if target else None
        if policy.target_mode == "OBJECT" and target is None:
            return False
        if policy.required_fields:
            projection = session.execute(
                text("""SELECT m.projection FROM sync_resource_mirrors m
                JOIN sync_pages p ON (p.tenant_id,p.outbound_id)=(m.tenant_id,m.outbound_id)
                WHERE m.source_id=:source AND m.native_id=:native AND p.run_id=:run"""),
                {
                    "source": health["sourceId"],
                    "native": native if policy.target_mode == "OBJECT" else ctx.native_account_id,
                    "run": health["successRunId"],
                },
            ).scalar_one_or_none()
            data = projection.get("data") if isinstance(projection, dict) else None
            if not isinstance(data, dict):
                return False
            for name, kind in policy.required_fields:
                value = data.get(name)
                valid = (
                    type(value) is int
                    if kind == "INTEGER"
                    else type(value) in (int, float)
                    if kind == "NUMBER"
                    else type(value) is bool
                    if kind == "BOOLEAN"
                    else isinstance(value, str) and bool(value)
                )
                if not valid:
                    return False
        for metric, unit in policy.required_metrics:
            rows = session.execute(
                text("""SELECT key_json FROM metric_fact_revisions WHERE
                source_id=:source AND sync_run_id=:run AND decision IN ('APPLIED','DUPLICATE')
                AND value_num IS NOT NULL AND key_json->>'metric_name'=:metric
                AND key_json->>'unit'=:unit
                AND ((:mode='OBJECT' AND key_json->>'native_entity_id'=:native) OR
                     (:mode='ACCOUNT' AND key_json->>'entity_kind'='ACCOUNT'))"""),
                {
                    "source": health["sourceId"],
                    "run": health["successRunId"],
                    "metric": metric,
                    "unit": unit,
                    "mode": policy.target_mode,
                    "native": native,
                },
            ).all()
            if len(rows) != 1:
                return False
        return True


@dataclass(frozen=True)
class ResourceValidity:
    authorization_expires_at: datetime
    data_deadline: datetime

    def check(self, session: Session) -> None:
        now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
        if self.authorization_expires_at <= now:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "平台授权已过期")
        if self.data_deadline < now:
            raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "写前置数据已过期，请重新核对")


class ResourceWriteGuard:
    def __init__(self, service: ResourceFreshness, application_id: UUID) -> None:
        self.service, self.application_id = service, application_id

    def __call__(
        self, session: Session, ctx: GatewayContext, spec: OperationSpec, target: ObjectRef | None
    ) -> ResourceValidity:
        if spec.effect != "WRITE" or not spec.preflight_resources:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "写资源契约尚未配置")
        if target:
            ctx.assert_ref(target)
        _config_lock(session, ctx.identity.tenant_id, ctx.account_id, shared=True)
        auth = (
            session.execute(
                text("""SELECT status,expires_at FROM provider_authorizations WHERE id=:id
            AND application_id=:app AND provider=:provider AND product_line=:product
            AND (account_id IS NULL OR account_id=:account) FOR SHARE"""),
                {
                    "id": ctx.authorization_id,
                    "app": self.application_id,
                    "provider": ctx.provider,
                    "product": ctx.product_line,
                    "account": ctx.account_id,
                },
            )
            .mappings()
            .first()
        )
        now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
        if auth is None or auth["status"] != "ACTIVE" or auth["expires_at"] <= now:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "平台授权缺失或已过期")
        requirements = (
            session.execute(
                text("""SELECT * FROM write_resource_requirements
            WHERE write_binding_id=:binding AND account_id=:account
            AND (target_native_id IS NULL OR target_native_id=:target)"""),
                {
                    "binding": ctx.binding_id,
                    "account": ctx.account_id,
                    "target": target.native_id if target else None,
                },
            )
            .mappings()
            .all()
        )
        if {row["resource_key"] for row in requirements} != set(spec.preflight_resources):
            raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "写资源契约与登记范围不一致")
        deadlines = []
        for key in spec.preflight_resources:
            matches = [row for row in requirements if row["resource_key"] == key]
            if len(matches) != 1:
                raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "写资源范围缺失或存在歧义")
            requirement = matches[0]
            # 和暂停/新分页提交串行到出站意图提交；只读不授予配置写权限。
            session.execute(
                text("SELECT id FROM sync_sources WHERE id=:id FOR SHARE"),
                {"id": requirement["source_id"]},
            )
            health = self.service.inspect(session, ctx, requirement["source_id"])
            if (
                health["status"] != "FRESH"
                or health["policyRevision"] != requirement["policy_revision"]
                or not self.service.required_data(session, ctx, health, target)
            ):
                raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "写入需要重新核对当前资源数据")
            policy: ResourcePolicy = health["policy"]
            anchor = (
                health["sourceUpdatedAt"] if policy.clock_kind == "SOURCE" else health["fetchedAt"]
            )
            deadlines.append(anchor + timedelta(seconds=policy.max_age_seconds))
        validity = ResourceValidity(auth["expires_at"], min(deadlines))
        validity.check(session)
        return validity
