"""发送前短事务登记；适配器显式装配，重复投递不恢复一次性发送凭证。"""

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID, uuid4, uuid5

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.audit.service import record
from yuliang.modules.capability.service import evaluate_binding
from yuliang.modules.command.applications import ACTION_CAPABILITIES, JobApplications
from yuliang.modules.command.jobs import CommandJob
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.http import OperationSpec, _check_fields
from yuliang.modules.connectors.redaction import ArchivePolicy, canonical, fingerprint, project
from yuliang.modules.execution.attempt_models import BusinessKind, BusinessRef, ExecutionAttempt
from yuliang.modules.execution.guards import GuardLease, TargetGuards, controlled
from yuliang.modules.policy.repository import policy_lock
from yuliang.modules.policy.service import RiskFramework
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

KEY_NAMESPACE = UUID("7e8fac75-e4f3-50c3-b96d-1e6b1172fc64")


class CommandSerializer(Protocol):
    """仅后端已审核适配器可装配；不得把客户端params或AI输出当已验证serializer。"""

    def render(
        self, ctx: GatewayContext, job: CommandJob, platform_key: UUID
    ) -> tuple[OperationSpec, dict[str, Any]]: ...


@dataclass(frozen=True)
class PreparedExecution:
    attempt: ExecutionAttempt
    spec: OperationSpec | None = field(default=None, repr=False)
    parameters: dict[str, Any] | None = field(default=None, repr=False)


class ExecutionOrders:
    def __init__(
        self, applications: JobApplications, serializer: CommandSerializer | None = None
    ) -> None:
        self.applications = applications
        self.serializer = serializer

    def _locks(self, session: Session) -> None:
        policy_lock(session, self.applications.identity.tenant_id, shared=True)
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760210))"),
            {"key": str(self.applications.identity.tenant_id)},
        )

    def _scope(self, session: Session, business: BusinessRef) -> dict[str, Any]:
        if business.kind != BusinessKind.COMMAND_JOB:
            raise ApiError(
                ErrorCode.CAPABILITY_UNAVAILABLE, "当前业务源尚未接入执行内核，不创建替代命令申请"
            )
        actor = self.applications.identity
        return controlled(
            lambda: session.execute(
                text("SELECT access.job_state_context(:member,:user,:job,'CLAIM')"),
                {"member": actor.membership_id, "user": actor.user_id, "job": business.id},
            ).scalar_one()
        )

    def _load(self, session: Session, business: BusinessRef) -> dict[str, Any] | None:
        value = (
            session.execute(
                text("""SELECT o.*,a.id AS attempt_id,a.attempt_no,a.outbound_id,
            a.platform_request_key,a.state AS attempt_state,a.dispatch_digest,
            a.dispatch_consumed_at,
            a.business_version,a.binding_id AS attempt_binding_id,
            a.capability_version AS attempt_capability_version,
            a.authorization_id AS attempt_authorization_id,
            a.authorization_version AS attempt_authorization_version,
            a.context_digest AS attempt_context_digest,a.operation_key AS attempt_operation_key,
            a.schema_digest AS attempt_schema_digest,a.wire_digest AS attempt_wire_digest,
            c.fence,c.lease_owner,c.lease_until FROM execution_orders o
            JOIN execution_attempts a ON (a.tenant_id,a.order_id,a.attempt_no)
             =(o.tenant_id,o.id,o.current_attempt)
            JOIN target_guard_claims c ON (c.tenant_id,c.id)=(a.tenant_id,a.guard_claim_id)
            WHERE o.biz_type=:kind AND o.biz_id=:id"""),
                {"kind": business.kind.value, "id": business.id},
            )
            .mappings()
            .first()
        )
        if value is None:
            return None
        row = dict(value)
        for name in (
            "binding_id",
            "capability_version",
            "authorization_id",
            "authorization_version",
            "context_digest",
            "operation_key",
            "schema_digest",
            "wire_digest",
        ):
            row[name] = row[f"attempt_{name}"]
        return row

    @staticmethod
    def _snapshot(row: dict[str, Any], secret: SecretStr | None = None) -> ExecutionAttempt:
        return ExecutionAttempt(
            execution_id=row["id"],
            attempt_id=row["attempt_id"],
            business=BusinessRef(kind=row["biz_type"], id=row["biz_id"]),
            attempt_no=row["attempt_no"],
            outbound_id=row["outbound_id"],
            platform_request_key=row["platform_request_key"],
            execution_status=row["status"],
            attempt_state=row["attempt_state"],
            execution_version=row["version"],
            guard=GuardLease(
                guardId=row["guard_id"],
                jobId=row["command_job_id"],
                fence=row["fence"],
                owner=row["lease_owner"],
                leaseUntil=row["lease_until"],
                jobVersion=row["business_version"],
            ),
            send_allowed=secret is not None,
            dispatch_secret=secret,
        )

    def _context_digest(self, ctx: GatewayContext, auth_version: int) -> str:
        return fingerprint(
            self.applications.key,
            "execution-context-v1",
            canonical(
                [
                    ctx.identity.tenant_id,
                    str(ctx.identity.user_id),
                    str(ctx.identity.membership_id),
                    str(ctx.shop_id),
                    str(ctx.account_id),
                    ctx.native_account_id,
                    ctx.connector_id,
                    ctx.provider,
                    ctx.product_line,
                    str(ctx.binding_id),
                    ctx.capability_version,
                    str(ctx.authorization_id),
                    auth_version,
                    ctx.catalog_id,
                    ctx.catalog_sha256,
                ]
            ),
        )

    def lookup(self, business: BusinessRef) -> ExecutionAttempt:
        with business_transaction(self.applications.engine, self.applications.identity) as session:
            self._scope(session, business)
            row = self._load(session, business)
            if row is None:
                raise ApiError(ErrorCode.NO_DATA, "当前申请尚无执行尝试")
            return self._snapshot(row)

    def prepare(
        self,
        business: BusinessRef,
        ctx: GatewayContext,
        expected_version: int,
        expected_fence: int,
        owner: UUID,
        *,
        unit_of_work: Session | None = None,
    ) -> PreparedExecution:
        if ctx.identity != self.applications.identity:
            raise ApiError(ErrorCode.FORBIDDEN, "执行上下文与实际调用者不匹配")
        if type(expected_version) is not int or expected_version < 1:
            raise ApiError(ErrorCode.INVALID_INPUT, "需要明确申请版本")
        transaction = (
            nullcontext(unit_of_work)
            if unit_of_work is not None
            else business_transaction(self.applications.engine, self.applications.identity)
        )
        with transaction as session:
            return controlled(
                lambda: self._prepare(
                    session, business, ctx, expected_version, expected_fence, owner
                )
            )

    def _prepare(
        self,
        session: Session,
        business: BusinessRef,
        ctx: GatewayContext,
        version: int,
        fence: int,
        owner: UUID,
    ) -> PreparedExecution:
        self._locks(session)
        source = self._scope(session, business)
        previous = self._load(session, business)
        if previous:
            if previous["requested_version"] != version or previous[
                "context_digest"
            ] != self._context_digest(ctx, previous["authorization_version"]):
                raise ApiError(
                    ErrorCode.IDEMPOTENCY_MISMATCH, "执行单已绑定其他输入版本或出站上下文"
                )
            return PreparedExecution(self._snapshot(previous))
        if self.serializer is None:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "当前动作尚未装配已核验的出站序列化器")
        job = CommandJob.model_validate(source["document"])
        target = (
            session.execute(
                text("""SELECT c.*,a.native_id AS native_account
            FROM campaigns c JOIN ad_accounts a ON (a.tenant_id,a.id)=(c.tenant_id,c.account_id)
            WHERE c.id=:id"""),
                {"id": UUID(job.target_id)},
            )
            .mappings()
            .one()
        )
        if (
            str(ctx.shop_id),
            str(ctx.account_id),
            ctx.native_account_id,
            ctx.provider,
            ctx.product_line,
        ) != (
            job.shop_id,
            job.account_id,
            target["native_account"],
            target["provider"],
            target["product_line"],
        ):
            raise ApiError(ErrorCode.FORBIDDEN, "完整执行目标与出站账户不匹配")
        binding = (
            session.execute(
                text("SELECT * FROM capability_bindings WHERE id=:id AND active"),
                {"id": ctx.binding_id},
            )
            .mappings()
            .first()
        )
        if binding is None or (
            binding["account_id"],
            binding["connector_id"],
            binding["provider"],
            binding["product_line"],
            binding["catalog_id"],
            binding["catalog_sha256"],
            binding["revision"],
            binding["operation_kind"],
            binding["action"],
        ) != (
            ctx.account_id,
            ctx.connector_id,
            ctx.provider,
            ctx.product_line,
            ctx.catalog_id,
            ctx.catalog_sha256,
            ctx.capability_version,
            "WRITE",
            ACTION_CAPABILITIES[job.action_type][0],
        ):
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "出站动作绑定尚未核验")
        capability = evaluate_binding(session, ctx.identity, ctx.shop_id, ctx.binding_id)
        if capability["status"] != "AUTHORIZED":
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "动作六阶段证据未就绪")
        auth = session.execute(
            text("SELECT version FROM provider_authorizations WHERE id=:id"),
            {"id": ctx.authorization_id},
        ).scalar_one_or_none()
        if auth is None:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "出站授权不存在")
        order, attempt = uuid4(), uuid4()
        key = uuid5(KEY_NAMESPACE, f"{ctx.identity.tenant_id}|{order}|1|platform")
        outbound = uuid5(KEY_NAMESPACE, f"{ctx.identity.tenant_id}|{order}|1|write-root")
        spec, parameters = self.serializer.render(ctx, job, key)
        if (spec.effect, spec.method, spec.path, spec.catalog_id, spec.catalog_sha256) != (
            "WRITE",
            binding["http_method"],
            binding["normalized_path"],
            ctx.catalog_id,
            ctx.catalog_sha256,
        ):
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "序列化器与已核验接口不匹配")
        normalized = spec.parameters_model.model_validate(parameters, strict=True).model_dump(
            mode="json", by_alias=True
        )
        _check_fields(normalized)
        schema = hashlib.sha256(canonical(spec.parameters_model.model_json_schema())).hexdigest()
        wire = hashlib.sha256(canonical(normalized)).hexdigest()
        lease = TargetGuards(self.applications).claim(session, business.id, version, fence, owner)
        secret = SecretStr(secrets.token_urlsafe(32))
        actor = self.applications.identity
        session.execute(
            text("""SELECT access.execution_register(:member,:user,:job,:version,
            :order,:attempt,:outbound,:key,:guard,:fence,:owner,:binding,:auth,:context,:digest,
            :operation,:schema,:wire_digest,CAST(:wire AS jsonb))"""),
            {
                "member": actor.membership_id,
                "user": actor.user_id,
                "job": business.id,
                "version": version,
                "order": order,
                "attempt": attempt,
                "outbound": outbound,
                "key": key,
                "guard": lease.guardId,
                "fence": lease.fence,
                "owner": lease.owner,
                "binding": ctx.binding_id,
                "auth": ctx.authorization_id,
                "context": self._context_digest(ctx, auth),
                "digest": hashlib.sha256(secret.get_secret_value().encode()).hexdigest(),
                "operation": spec.key,
                "schema": schema,
                "wire_digest": wire,
                "wire": json.dumps(project(normalized, ArchivePolicy())),
            },
        )
        record(
            session,
            actor,
            {
                "action": "execution.prepare",
                "object_type": "execution_order",
                "object_id": order,
                "request_id": self.applications.request_id,
                "after_hash": wire,
            },
        )
        current = self._load(session, business)
        if current is None:
            raise RuntimeError("发送前执行尝试未落库")
        return PreparedExecution(self._snapshot(current, secret), spec, normalized)

    def before_verify(self, ticket: ExecutionAttempt) -> Callable[[Session], None]:
        def lock(session: Session) -> None:
            session.execute(text("SET LOCAL lock_timeout='1500ms'"))
            self._locks(session)
            self._scope(session, ticket.business)

        return lock

    def after_admission(
        self,
        ticket: ExecutionAttempt,
        ctx: GatewayContext,
        spec: OperationSpec,
        parameters: dict[str, Any],
    ) -> Callable[[Session], None]:
        def authorize(session: Session) -> None:
            secret = ticket.dispatch_secret
            if not ticket.send_allowed or secret is None:
                raise ApiError(ErrorCode.RESULT_UNKNOWN, "原执行尝试只允许查询，不恢复发送凭证")
            self._scope(session, ticket.business)
            row = self._load(session, ticket.business)
            if (
                row is None
                or row["id"] != ticket.execution_id
                or row["attempt_id"] != ticket.attempt_id
            ):
                raise ApiError(ErrorCode.VERSION_CONFLICT, "执行尝试已变化")
            actual = self._snapshot(row)
            normalized = spec.parameters_model.model_validate(parameters, strict=True).model_dump(
                mode="json", by_alias=True
            )
            if (
                ctx.identity != self.applications.identity
                or row["context_digest"] != self._context_digest(ctx, row["authorization_version"])
                or row["operation_key"] != spec.key
                or row["schema_digest"]
                != hashlib.sha256(canonical(spec.parameters_model.model_json_schema())).hexdigest()
                or not hmac.compare_digest(
                    row["wire_digest"], hashlib.sha256(canonical(normalized)).hexdigest()
                )
            ):
                raise ApiError(ErrorCode.IDEMPOTENCY_MISMATCH, "发送参数或账户与冻结执行载荷不一致")
            decision = RiskFramework(self.applications).assess_dispatch(session, actual.guard)
            decision.require_allowed()
            actor = self.applications.identity
            controlled(
                lambda: session.execute(
                    text("""SELECT access.execution_authorize_dispatch(
                :member,:user,:attempt,:secret,:assessment)"""),
                    {
                        "member": actor.membership_id,
                        "user": actor.user_id,
                        "attempt": ticket.attempt_id,
                        "secret": secret.get_secret_value(),
                        "assessment": decision.assessment_id,
                    },
                ).scalar_one()
            )

        return authorize
