"""请求先持久化、逐次网络观察追加留痕；存档不是平台执行授权。"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from pydantic import SecretStr, ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from yuliang.modules.access.service import require_shop_account
from yuliang.modules.audit.service import record
from yuliang.modules.capability.models import Binding
from yuliang.modules.capability.route_sources import current_source
from yuliang.modules.connectors.base import GatewayContext, ObjectRef
from yuliang.modules.connectors.http import (
    ConnectorTransportError,
    HttpExchange,
    OperationSpec,
    PlatformHttpClient,
)
from yuliang.modules.connectors.outcomes import (
    HEALTH_CATEGORIES,
    BusinessVerdict,
    Outcome,
    classify,
)
from yuliang.modules.connectors.redaction import ArchivePolicy, canonical, fingerprint, project
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

DEFAULT_POLICY = ArchivePolicy()


class ArchiveUnavailable(Exception):
    def __init__(self, dispatch: Literal["NOT_SENT", "RESPONDED", "UNKNOWN"]) -> None:
        super().__init__("平台核对档案暂不可用")
        self.dispatch = dispatch


def _verify(session: Session, ctx: GatewayContext, spec: OperationSpec) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,760117))"),
        {"key": f"{ctx.identity.tenant_id}|{ctx.account_id}"},
    )
    authorization = (
        session.execute(
            text("""SELECT connector_id,provider,product_line,status,version,
        expires_at>clock_timestamp() AS current,account_id FROM provider_authorizations
        WHERE id=:id"""),
            {"id": ctx.authorization_id},
        )
        .mappings()
        .first()
    )
    if authorization is not None and (
        (authorization["connector_id"], authorization["provider"], authorization["product_line"])
        != (ctx.connector_id, ctx.provider, ctx.product_line)
        or authorization["status"] != "ACTIVE"
        or not authorization["current"]
        or authorization["account_id"] not in (None, ctx.account_id)
        or ctx.authorization_version is not None
        and authorization["version"] != ctx.authorization_version
    ):
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "平台连接已失效或范围不匹配")
    if ctx.authorization_version is not None and authorization is None:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "缺少已核对版本的实际授权")
    row = (
        session.execute(
            text("""
      SELECT b.*,a.native_id FROM capability_bindings b JOIN ad_accounts a
       ON (a.tenant_id,a.id)=(b.tenant_id,b.account_id)
      WHERE b.id=:id AND b.active AND a.deleted_at IS NULL
       AND access.catalog_is_current(b.catalog_sha256,b.operation_kind)
    """),
            {"id": ctx.binding_id},
        )
        .mappings()
        .first()
    )
    if row is None or (
        row["account_id"],
        row["connector_id"],
        row["provider"],
        row["product_line"],
        row["revision"],
        row["catalog_id"],
        row["catalog_sha256"],
        row["native_id"],
    ) != (
        ctx.account_id,
        ctx.connector_id,
        ctx.provider,
        ctx.product_line,
        ctx.capability_version,
        ctx.catalog_id,
        ctx.catalog_sha256,
        ctx.native_account_id,
    ):
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "出站范围或版本已变化")
    if (
        spec.connector_id,
        spec.provider,
        spec.product_line,
        spec.catalog_id,
        spec.catalog_sha256,
    ) != (ctx.connector_id, ctx.provider, ctx.product_line, ctx.catalog_id, ctx.catalog_sha256):
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "操作与范围不匹配")
    if (row["operation_kind"], row["http_method"], row["normalized_path"]) != (
        spec.effect,
        spec.method,
        spec.path,
    ):
        raise ApiError(
            ErrorCode.CAPABILITY_UNAVAILABLE, "操作读写类型、方法或路径与已核对绑定不一致"
        )
    require_shop_account(session, ctx.identity, ctx.shop_id, ctx.account_id, row["permission_code"])
    if row["provider"] == "tencent" and row["normalized_path"].startswith("/v1.3/"):
        binding = Binding.model_validate({key: row[key] for key in Binding.model_fields})
        now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
        if current_source(session, binding, now) is None:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "旧版只读路径缺少有效独立SDK依据")


class PlatformArchive:
    def __init__(self, engine: Engine, audit_key: SecretStr) -> None:
        fingerprint(audit_key, "key-check", b"")
        self.engine, self.key = engine, audit_key

    def begin(
        self,
        ctx: GatewayContext,
        spec: OperationSpec,
        outbound_id: UUID,
        parameters: dict[str, Any],
        policy: ArchivePolicy,
        target: ObjectRef | None,
        execution_id: UUID | None,
        attempt_id: UUID | None,
        before_create: Callable[[], None] | None = None,
        before_persist: Callable[[Session], None] | None = None,
        before_verify: Callable[[Session], None] | None = None,
        after_admission: Callable[[Session], None] | None = None,
    ) -> bool:
        if target is not None:
            ctx.assert_ref(target)
        if (execution_id is None) != (attempt_id is None):
            raise ValueError("执行单与尝试引用必须同时提供")
        intent = {
            "tenant": ctx.identity.tenant_id,
            "user": str(ctx.identity.user_id),
            "account": str(ctx.account_id),
            "authorization": str(ctx.authorization_id),
            "authorization_version": ctx.authorization_version,
            "binding": str(ctx.binding_id),
            "version": ctx.capability_version,
            "catalog": ctx.catalog_sha256,
            "operation": spec.key,
            "method": spec.method,
            "path": spec.path,
            "effect": spec.effect,
            "parameters": parameters,
            "target": target.native_id if target else None,
            "execution": str(execution_id) if execution_id else None,
            "attempt": str(attempt_id) if attempt_id else None,
        }
        digest = fingerprint(self.key, "platform-request-v1", canonical(intent))
        request_policy = ArchivePolicy(
            policy.fields - frozenset({"code", "errcode"}), policy.max_items
        )
        safe = project(parameters, request_policy)
        with business_transaction(self.engine, ctx.identity) as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:id,760103))"),
                {"id": str(outbound_id)},
            )
            existing = session.execute(
                text("SELECT request_digest FROM platform_requests WHERE id=:id"),
                {"id": outbound_id},
            ).scalar_one_or_none()
            if existing is not None:
                if existing != digest:
                    raise ApiError(ErrorCode.IDEMPOTENCY_MISMATCH, "原出站请求与本次内容不一致")
                return False
            if execution_id is not None and spec.effect == "WRITE":
                session.execute(
                    text("""SELECT set_config('app.execution_wire_digest',:wire,true),
                    set_config('app.execution_schema_digest',:schema,true)"""),
                    {
                        "wire": hashlib.sha256(canonical(parameters)).hexdigest(),
                        "schema": hashlib.sha256(
                            canonical(spec.parameters_model.model_json_schema())
                        ).hexdigest(),
                    },
                )
            if before_verify is not None:
                before_verify(session)
            _verify(session, ctx, spec)
            if (
                target is not None
                and spec.effect == "WRITE"
                and session.execute(
                    text("""SELECT EXISTS(SELECT 1 FROM campaigns WHERE account_id=:account
                 AND native_id=:native AND kind='CAMPAIGN' AND protocol_version='1.3')"""),
                    {"account": ctx.account_id, "native": target.native_id},
                ).scalar_one()
            ):
                raise ApiError(
                    ErrorCode.CAPABILITY_UNAVAILABLE, "旧版计划未核验写协议，不能作为营销单元执行"
                )
            if before_persist is not None:
                before_persist(session)
            if before_create is not None:
                before_create()
            if after_admission is not None:
                after_admission(session)
            audit_id = record(
                session,
                ctx.identity,
                {
                    "action": "platform.request",
                    "object_type": "platform_request",
                    "object_id": outbound_id,
                    "request_id": ctx.request_id,
                    "after_hash": digest,
                },
            )
            session.execute(
                text("""
              INSERT INTO platform_requests(id,tenant_id,user_id,membership_id,request_id,
               authorization_id,
               account_id,binding_id,capability_version,connector_id,provider,product_line,catalog_sha256,
               catalog_id,operation_key,effect,method,normalized_path,target_native_id,execution_id,
               execution_attempt_id,request_digest,request_json,audit_id)
              VALUES(:id,:tenant,:user,:member,:request,:auth,:account,:binding,:version,:connector,:provider,
               :product,:sha,:catalog,:operation,:effect,:method,:path,:target,:execution,:attempt,:digest,
               CAST(:payload AS jsonb),:audit)
            """),
                {
                    "id": outbound_id,
                    "tenant": ctx.identity.tenant_id,
                    "user": ctx.identity.user_id,
                    "member": ctx.identity.membership_id,
                    "request": ctx.request_id,
                    "auth": ctx.authorization_id,
                    "account": ctx.account_id,
                    "binding": ctx.binding_id,
                    "version": ctx.capability_version,
                    "connector": ctx.connector_id,
                    "provider": ctx.provider,
                    "product": ctx.product_line,
                    "sha": ctx.catalog_sha256,
                    "catalog": ctx.catalog_id,
                    "operation": spec.key,
                    "effect": spec.effect,
                    "method": spec.method,
                    "path": spec.path,
                    "target": target.native_id if target else None,
                    "execution": execution_id,
                    "attempt": attempt_id,
                    "digest": digest,
                    "payload": json.dumps(safe, ensure_ascii=False),
                    "audit": audit_id,
                },
            )
        return True

    def append(
        self,
        ctx: GatewayContext,
        spec: OperationSpec,
        outbound_id: UUID,
        attempt: int,
        phase: str,
        dispatch: str,
        *,
        outcome: Outcome | None = None,
        response: HttpExchange | None = None,
        verdict: BusinessVerdict | None = None,
        policy: ArchivePolicy = DEFAULT_POLICY,
        http_status: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {}
        response_digest = trace_digest = None
        if response is not None:
            response_digest = fingerprint(self.key, "platform-response-v1", response.body)
            try:
                payload = project(json.loads(response.body), policy)
            except (ValueError, UnicodeError, RecursionError):
                payload = {"data": None, "unparseable": True}
        if verdict and isinstance(verdict.trace_id, str) and len(verdict.trace_id) <= 512:
            trace_digest = fingerprint(self.key, "platform-trace-v1", verdict.trace_id.encode())
        with business_transaction(self.engine, ctx.identity) as session:
            if phase == "ATTEMPT":
                _verify(session, ctx, spec)
            session.execute(
                text("""
              INSERT INTO platform_raw_payloads(tenant_id,user_id,outbound_id,network_attempt,phase,
               dispatch,http_status,category,external_outcome,retry_after_seconds,retry_after_unconfirmed,
               response_digest,trace_digest,payload)
              VALUES(:tenant,:user,:outbound,:attempt,:phase,:dispatch,:status,:category,:external,
               :retry,:unconfirmed,:digest,:trace,CAST(:payload AS jsonb))
            """),
                {
                    "tenant": ctx.identity.tenant_id,
                    "user": ctx.identity.user_id,
                    "outbound": outbound_id,
                    "attempt": attempt,
                    "phase": phase,
                    "dispatch": dispatch,
                    "status": response.http_status if response else http_status,
                    "category": outcome.category if outcome else None,
                    "external": outcome.external if outcome else None,
                    "retry": outcome.retry_after_seconds if outcome else None,
                    "unconfirmed": outcome.retry_after_unconfirmed if outcome else False,
                    "digest": response_digest,
                    "trace": trace_digest,
                    "payload": json.dumps(payload, ensure_ascii=False),
                },
            )
            # 同步健康只共享受控观察码，不共享用户私有请求、原文或PII。
            session.execute(
                text("""UPDATE sync_page_intents SET last_phase=:phase,last_category=:category,
                last_dispatch=:dispatch,last_observed_at=clock_timestamp()
                WHERE outbound_id=:id"""),
                {
                    "id": outbound_id,
                    "phase": phase,
                    "category": (
                        outcome.category if outcome.category in HEALTH_CATEGORIES else "UNCONFIRMED"
                    )
                    if outcome
                    else None,
                    "dispatch": dispatch,
                },
            )

    def summary(self, ctx: GatewayContext, outbound_id: UUID) -> dict[str, Any]:
        with business_transaction(self.engine, ctx.identity) as session:
            root = (
                session.execute(
                    text("SELECT request_id,effect FROM platform_requests WHERE id=:id"),
                    {"id": outbound_id},
                )
                .mappings()
                .first()
            )
            if root is None:
                raise ApiError(ErrorCode.NO_DATA, "未找到本人的出站请求")
            event = (
                session.execute(
                    text(
                        "SELECT * FROM platform_raw_payloads WHERE outbound_id=:id "
                        "ORDER BY id DESC LIMIT 1"
                    ),
                    {"id": outbound_id},
                )
                .mappings()
                .first()
            )
            attempts = session.execute(
                text(
                    "SELECT coalesce(max(network_attempt),0) FROM platform_raw_payloads "
                    "WHERE outbound_id=:id AND phase='ATTEMPT'"
                ),
                {"id": outbound_id},
            ).scalar_one()
        if event is None:
            category, external, dispatch = "NOT_SENT", "NOT_SENT", "NOT_SENT"
        elif event["phase"] == "ATTEMPT":
            category, external, dispatch = "RESULT_UNKNOWN", "UNKNOWN", "UNKNOWN"
        else:
            category, external, dispatch = (
                event["category"],
                event["external_outcome"],
                event["dispatch"],
            )
        return {
            "archiveId": str(outbound_id),
            "requestId": str(root["request_id"]),
            "category": category,
            "externalOutcome": external,
            "dispatch": dispatch,
            "networkAttempts": attempts,
            "httpStatus": event["http_status"] if event else None,
            "retryAfterSeconds": event["retry_after_seconds"] if event else None,
            "retryAfterUnconfirmed": event["retry_after_unconfirmed"] if event else False,
        }


class _Observer:
    def __init__(
        self,
        archive: PlatformArchive,
        ctx: GatewayContext,
        spec: OperationSpec,
        outbound: UUID,
        policy: ArchivePolicy,
        decoder: Callable[[HttpExchange], BusinessVerdict],
    ) -> None:
        self.archive, self.ctx, self.spec, self.outbound, self.policy, self.decoder = (
            archive,
            ctx,
            spec,
            outbound,
            policy,
            decoder,
        )
        self.attempt = 0
        self.completed: set[int] = set()

    def started(self, attempt: int) -> None:
        try:
            self.archive.append(self.ctx, self.spec, self.outbound, attempt, "ATTEMPT", "SENDING")
        except Exception:
            raise ArchiveUnavailable("NOT_SENT") from None
        self.attempt = attempt

    def responded(self, response: HttpExchange) -> None:
        try:
            verdict = self.decoder(response)
            if not isinstance(verdict, BusinessVerdict):
                verdict = BusinessVerdict()
        except Exception:
            verdict = BusinessVerdict()
        outcome = classify(self.spec.effect, response=response, verdict=verdict)
        try:
            self.archive.append(
                self.ctx,
                self.spec,
                self.outbound,
                self.attempt,
                "RESPONSE",
                "RESPONDED",
                outcome=outcome,
                response=response,
                verdict=verdict,
                policy=self.policy,
            )
        except Exception:
            raise ArchiveUnavailable("RESPONDED") from None
        self.completed.add(self.attempt)

    def failed(self, error: ConnectorTransportError) -> None:
        if error.attempts in self.completed:
            return
        try:
            self.archive.append(
                self.ctx,
                self.spec,
                self.outbound,
                error.attempts,
                "ERROR",
                error.dispatch,
                outcome=classify(self.spec.effect, error=error),
                http_status=error.http_status,
            )
        except Exception:
            raise ArchiveUnavailable(error.dispatch) from None
        self.completed.add(error.attempts)


@dataclass(frozen=True)
class RecordedResult:
    metadata: dict[str, Any]
    exchange: HttpExchange | None = field(default=None, repr=False)
    error: ConnectorTransportError | None = field(default=None, repr=False)


class RecordedPlatformClient:
    def __init__(
        self,
        client: PlatformHttpClient,
        archive: PlatformArchive,
        *,
        policy: ArchivePolicy = DEFAULT_POLICY,
        decoder: Callable[[HttpExchange], BusinessVerdict] = lambda response: BusinessVerdict(),
    ) -> None:
        self.client, self.archive, self.policy, self.decoder = client, archive, policy, decoder

    async def send(
        self,
        ctx: GatewayContext,
        spec: OperationSpec,
        outbound_id: UUID,
        parameters: dict[str, Any],
        credential: SecretStr,
        *,
        target: ObjectRef | None = None,
        execution_id: UUID | None = None,
        attempt_id: UUID | None = None,
        before_create: Callable[[], None] | None = None,
        before_persist: Callable[[Session], None] | None = None,
        before_verify: Callable[[Session], None] | None = None,
        after_admission: Callable[[Session], None] | None = None,
    ) -> RecordedResult:
        try:
            prepared = spec.parameters_model.model_validate(parameters, strict=True).model_dump(
                mode="json", by_alias=True
            )
        except (ValidationError, ValueError, TypeError):
            raise ValueError("平台参数不符合该操作Schema") from None
        try:
            created = self.archive.begin(
                ctx,
                spec,
                outbound_id,
                prepared,
                self.policy,
                target,
                execution_id,
                attempt_id,
                before_create,
                before_persist,
                before_verify,
                after_admission,
            )
        except SQLAlchemyError:
            raise ArchiveUnavailable("NOT_SENT") from None
        if not created:
            return RecordedResult(self.archive.summary(ctx, outbound_id))
        observer = _Observer(self.archive, ctx, spec, outbound_id, self.policy, self.decoder)
        try:
            response = await self.client.send(
                ctx, spec, prepared, credential, target=target, observer=observer
            )
        except ConnectorTransportError as transport_error:
            observer.failed(transport_error)
            return RecordedResult(self.archive.summary(ctx, outbound_id), error=transport_error)
        except ValueError:
            preflight_error = ConnectorTransportError(
                "INVALID_RESPONSE" if observer.attempt else "PREFLIGHT_ERROR",
                ctx.request_id,
                observer.attempt,
                dispatch="UNKNOWN" if observer.attempt else "NOT_SENT",
            )
            observer.failed(preflight_error)
            return RecordedResult(self.archive.summary(ctx, outbound_id), error=preflight_error)
        return RecordedResult(self.archive.summary(ctx, outbound_id), exchange=response)
