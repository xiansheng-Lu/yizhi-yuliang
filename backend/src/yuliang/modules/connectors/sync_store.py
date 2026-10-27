"""单租户只读同步：批次租约栅栏、页留档/镜像/确认游标同事务。"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from yuliang.modules.access.service import require_shop_account
from yuliang.modules.audit.service import record
from yuliang.modules.connectors.archive import _verify
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.http import OperationSpec, _check_fields
from yuliang.modules.connectors.redaction import ArchivePolicy, canonical, project
from yuliang.modules.connectors.sync_cursor import CursorCodec
from yuliang.platform.db import business_transaction

DEFAULT_MIRROR_POLICY = ArchivePolicy()


class SyncConflict(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__("同步状态不允许继续")
        self.reason = reason


@dataclass(frozen=True)
class SyncLease:
    run_id: UUID
    source_id: UUID
    token: UUID
    source_version: int
    page_no: int


@dataclass(frozen=True)
class PagePermit:
    lease: SyncLease
    outbound_id: UUID
    input_cursor_digest: str
    cursor: SecretStr | None = field(repr=False)


class MirrorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    native_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    values: dict[str, Any] = Field(repr=False)


@dataclass(frozen=True)
class SyncPage:
    records: tuple[MirrorRecord, ...] = field(repr=False)
    next_cursor: SecretStr | None = field(repr=False)
    terminal: bool
    fetched_at: datetime
    source_at: datetime | None = None
    scanned_items: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.terminal) is not bool
            or len(self.records) > 1000
            or len({row.native_id for row in self.records}) != len(self.records)
            or self.fetched_at.tzinfo is None
            or self.fetched_at.utcoffset() is None
            or self.source_at is not None
            and (self.source_at.tzinfo is None or self.source_at.utcoffset() is None)
            or not self.terminal
            and self.next_cursor is None
            or self.scanned_items is not None
            and (
                type(self.scanned_items) is not int
                or not len(self.records) <= self.scanned_items <= 1000
            )
        ):
            raise ValueError("同步页需唯一对象、有界条目、明确终页与带时区时间")


class SyncStore:
    def __init__(
        self, engine: Engine, codec: CursorCodec, *, policy: ArchivePolicy = DEFAULT_MIRROR_POLICY
    ) -> None:
        self.engine, self.codec, self.policy = engine, codec, policy

    @staticmethod
    def _read(session: Session, ctx: GatewayContext, spec: OperationSpec) -> None:
        if spec.effect != "READ" or not spec.idempotent_read or spec.max_read_attempts != 1:
            raise ValueError("分页同步只允许单次、已明确幂等的只读操作")
        _verify(session, ctx, spec)

    def _context(self, ctx: GatewayContext, spec: OperationSpec) -> str:
        return self.codec.hash(
            "context-v1",
            canonical(
                [
                    ctx.identity.tenant_id,
                    str(ctx.account_id),
                    ctx.native_account_id,
                    str(ctx.binding_id),
                    ctx.capability_version,
                    ctx.connector_id,
                    ctx.provider,
                    ctx.product_line,
                    ctx.catalog_id,
                    ctx.catalog_sha256,
                    spec.key,
                    spec.path,
                    spec.method,
                ]
            ),
        )

    def begin(
        self,
        ctx: GatewayContext,
        spec: OperationSpec,
        resource_kind: str,
        run_id: UUID,
        parameters: dict[str, Any],
    ) -> SyncLease | dict[str, Any]:
        if not re.fullmatch(r"[a-z][a-z0-9_.]{0,59}", resource_kind):
            raise ValueError("同步资源类型不合法")
        _check_fields(parameters)
        try:
            prepared = spec.parameters_model.model_validate(parameters, strict=True).model_dump(
                mode="json", by_alias=True
            )
        except ValueError:
            raise ValueError("同步参数不符合操作Schema") from None
        _check_fields(prepared)
        if (
            spec.account_parameter
            and str(prepared.get(spec.account_parameter)) != ctx.native_account_id
        ):
            raise ValueError("同步参数账户不匹配")
        if spec.cursor_parameter:
            if prepared.pop(spec.cursor_parameter, None) is not None:
                raise ValueError("初始同步查询不能指定未经确认的分页游标")
        filters = self.codec.hash("filters-v1", canonical(prepared))
        context = self._context(ctx, spec)
        digest = self.codec.hash(
            "run-v1", canonical([context, filters, resource_kind, str(ctx.identity.user_id)])
        )
        with business_transaction(self.engine, ctx.identity) as session:
            self._read(session, ctx, spec)
            session.execute(
                text("""INSERT INTO sync_sources(id,tenant_id,account_id,binding_id,
                resource_kind,context_digest,filter_digest,cursor_digest)
                VALUES(:id,:tenant,:account,:binding,:kind,:context,:filters,:cursor)
                ON CONFLICT(tenant_id,account_id,binding_id,resource_kind,filter_digest)
                DO NOTHING"""),
                {
                    "id": uuid4(),
                    "tenant": ctx.identity.tenant_id,
                    "account": ctx.account_id,
                    "binding": ctx.binding_id,
                    "kind": resource_kind,
                    "context": context,
                    "filters": filters,
                    "cursor": self.codec.digest(None),
                },
            )
            source = (
                session.execute(
                    text("""SELECT *,clock_timestamp() AS db_now FROM sync_sources
                WHERE account_id=:account AND binding_id=:binding AND resource_kind=:kind
                AND filter_digest=:filters FOR UPDATE"""),
                    {
                        "account": ctx.account_id,
                        "binding": ctx.binding_id,
                        "kind": resource_kind,
                        "filters": filters,
                    },
                )
                .mappings()
                .one()
            )
            if source["context_digest"] != context:
                raise SyncConflict("CONTEXT_CHANGED")
            existing = (
                session.execute(
                    text("SELECT * FROM sync_runs WHERE id=:id FOR UPDATE"), {"id": run_id}
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["request_digest"] != digest:
                    raise SyncConflict("IDEMPOTENCY_MISMATCH")
                if existing["state"] == "SUCCESS":
                    return self._summary(existing)
                if existing["source_version"] != source["version"]:
                    raise SyncConflict("SUPERSEDED")
            if not source["enabled"]:
                raise SyncConflict("PAUSED")
            if source["active_run_id"]:
                active = (
                    session.execute(
                        text("SELECT * FROM sync_runs WHERE id=:id FOR UPDATE"),
                        {"id": source["active_run_id"]},
                    )
                    .mappings()
                    .one()
                )
                if (
                    active["state"] == "RUNNING"
                    and active["lease_until"]
                    > session.execute(text("SELECT clock_timestamp()")).scalar_one()
                ):
                    raise SyncConflict("LEASE_ACTIVE")
                session.execute(
                    text("UPDATE sync_runs SET state='PARTIAL' WHERE id=:id AND state='RUNNING'"),
                    {"id": active["id"]},
                )
            token = uuid4()
            if existing:
                session.execute(
                    text("""UPDATE sync_runs SET state='RUNNING',lease_token=:token,
                    lease_until=clock_timestamp()+interval '120 seconds',finished_at=NULL,
                    last_error=NULL
                    WHERE id=:id"""),
                    {"id": run_id, "token": token},
                )
                page = existing["page_no"]
            else:
                session.execute(
                    text("""INSERT INTO sync_runs(id,tenant_id,source_id,user_id,
                    request_digest,source_version,state,lease_token,lease_until)
                    VALUES(:id,:tenant,:source,:user,:digest,:version,'RUNNING',:token,
                    clock_timestamp()+interval '120 seconds')"""),
                    {
                        "id": run_id,
                        "tenant": ctx.identity.tenant_id,
                        "source": source["id"],
                        "user": ctx.identity.user_id,
                        "digest": digest,
                        "version": source["version"],
                        "token": token,
                    },
                )
                page = 0
            session.execute(
                text("UPDATE sync_sources SET active_run_id=:run WHERE id=:source"),
                {"run": run_id, "source": source["id"]},
            )
            return SyncLease(run_id, source["id"], token, source["version"], page)

    def _locked(
        self,
        session: Session,
        ctx: GatewayContext,
        spec: OperationSpec,
        lease: SyncLease,
        *,
        allow_paused: bool = False,
    ) -> tuple[Any, Any]:
        self._read(session, ctx, spec)
        source = (
            session.execute(
                text(
                    "SELECT *,clock_timestamp() AS db_now FROM sync_sources WHERE id=:id FOR UPDATE"
                ),
                {"id": lease.source_id},
            )
            .mappings()
            .first()
        )
        run = (
            session.execute(
                text("SELECT * FROM sync_runs WHERE id=:id FOR UPDATE"), {"id": lease.run_id}
            )
            .mappings()
            .first()
        )
        if source is None or run is None or run["user_id"] != ctx.identity.user_id:
            raise SyncConflict("SCOPE_MISMATCH")
        if (source["account_id"], source["binding_id"]) != (ctx.account_id, ctx.binding_id):
            raise SyncConflict("SCOPE_MISMATCH")
        if source["context_digest"] != self._context(ctx, spec) or run["source_id"] != source["id"]:
            raise SyncConflict("CONTEXT_CHANGED")
        if not source["enabled"] and not allow_paused:
            raise SyncConflict("PAUSED")
        if (
            source["active_run_id"] != lease.run_id
            or run["lease_token"] != lease.token
            or run["state"] != "RUNNING"
            or run["lease_until"] <= session.execute(text("SELECT clock_timestamp()")).scalar_one()
            or source["version"] != lease.source_version
            or run["source_version"] != source["version"]
            or run["page_no"] != lease.page_no
        ):
            raise SyncConflict("LEASE_OR_CURSOR_CHANGED")
        return source, run

    def prepare(self, ctx: GatewayContext, spec: OperationSpec, lease: SyncLease) -> PagePermit:
        with business_transaction(self.engine, ctx.identity) as session:
            source, _ = self._locked(session, ctx, spec, lease)
            session.execute(
                text("""UPDATE sync_runs SET lease_until=clock_timestamp()+interval '120 seconds'
                WHERE id=:id"""),
                {"id": lease.run_id},
            )
            outbound = session.execute(
                text("""SELECT outbound_id FROM sync_page_intents
                WHERE run_id=:run AND page_no=:page AND lease_token=:token"""),
                {"run": lease.run_id, "page": lease.page_no, "token": lease.token},
            ).scalar_one_or_none()
            if outbound is None:
                outbound = session.execute(
                    text("""INSERT INTO sync_page_intents(outbound_id,tenant_id,run_id,
                page_no,lease_token,input_cursor_digest)
                VALUES(:id,:tenant,:run,:page,:token,:cursor) RETURNING outbound_id"""),
                    {
                        "id": uuid4(),
                        "tenant": ctx.identity.tenant_id,
                        "run": lease.run_id,
                        "page": lease.page_no,
                        "token": lease.token,
                        "cursor": source["cursor_digest"],
                    },
                ).scalar_one()
            cursor = self.codec.open(
                ctx.identity.tenant_id,
                lease.source_id,
                source["cursor_enc"],
                source["cursor_key_version"],
            )
            return PagePermit(lease, outbound, source["cursor_digest"], cursor)

    def dispatch_guard(
        self,
        session: Session,
        ctx: GatewayContext,
        spec: OperationSpec,
        permit: PagePermit,
        parameters: dict[str, Any],
    ) -> None:
        """交给Recorded/Governed.before_persist，和新请求留档共用同一事务及源行锁。"""
        source, _ = self._locked(session, ctx, spec, permit.lease)
        prepared = spec.parameters_model.model_validate(parameters, strict=True).model_dump(
            mode="json", by_alias=True
        )
        cursor = prepared.pop(spec.cursor_parameter, None) if spec.cursor_parameter else None
        expected = permit.cursor.get_secret_value() if permit.cursor else None
        if (None if cursor is None else str(cursor)) != expected or (
            self.codec.hash("filters-v1", canonical(prepared)) != source["filter_digest"]
        ):
            raise SyncConflict("QUERY_OR_CURSOR_MISMATCH")
        valid = session.execute(
            text("""SELECT 1 FROM sync_page_intents WHERE outbound_id=:outbound
            AND run_id=:run AND page_no=:page AND lease_token=:token
            AND input_cursor_digest=:cursor"""),
            {
                "outbound": permit.outbound_id,
                "run": permit.lease.run_id,
                "page": permit.lease.page_no,
                "token": permit.lease.token,
                "cursor": source["cursor_digest"],
            },
        ).first()
        if (
            valid is None
            or permit.input_cursor_digest != source["cursor_digest"]
            or self.codec.digest(permit.cursor) != source["cursor_digest"]
        ):
            raise SyncConflict("CURSOR_MISMATCH")

    def commit_page(
        self,
        ctx: GatewayContext,
        spec: OperationSpec,
        permit: PagePermit,
        page: SyncPage,
        *,
        project_facts: Callable[[Session], None] | None = None,
    ) -> SyncLease | dict[str, Any]:
        lease = permit.lease
        count = page.scanned_items if page.scanned_items is not None else len(page.records)
        if page.next_cursor is not None and spec.cursor_parameter is None:
            raise ValueError("非终止游标必须有Schema声明的分页参数")
        raw = [{"id": row.native_id, "values": row.values} for row in page.records]
        next_digest = self.codec.digest(page.next_cursor)
        digest = self.codec.hash(
            "page-v1",
            canonical(
                [
                    str(permit.outbound_id),
                    permit.input_cursor_digest,
                    next_digest,
                    page.terminal,
                    raw,
                    page.source_at.isoformat() if page.source_at else None,
                    page.fetched_at.isoformat(),
                    count,
                ]
            ),
        )
        projections = [project(row.values, self.policy) for row in page.records]
        if any(row["truncated"] for row in projections):
            raise ValueError("被截断的镜像页不能推进游标")
        with business_transaction(self.engine, ctx.identity) as session:
            previous = session.execute(
                text("SELECT page_digest FROM sync_pages WHERE run_id=:id AND page_no=:page"),
                {"id": lease.run_id, "page": lease.page_no},
            ).scalar_one_or_none()
            if previous is not None:
                if previous != digest:
                    raise SyncConflict("PAGE_MISMATCH")
                return self.status(ctx, lease.run_id)
            source, _ = self._locked(session, ctx, spec, lease, allow_paused=True)
            intent = session.execute(
                text("""SELECT input_cursor_digest FROM sync_page_intents WHERE
                outbound_id=:outbound AND run_id=:run AND page_no=:page AND lease_token=:token"""),
                {
                    "outbound": permit.outbound_id,
                    "run": lease.run_id,
                    "page": lease.page_no,
                    "token": lease.token,
                },
            ).scalar_one_or_none()
            if (
                intent != source["cursor_digest"]
                or permit.input_cursor_digest != intent
                or self.codec.digest(permit.cursor) != intent
            ):
                raise SyncConflict("CURSOR_MISMATCH")
            if not page.terminal and (
                next_digest == intent
                or session.execute(
                    text("""SELECT 1 FROM sync_pages
                WHERE run_id=:run AND input_cursor_digest=:digest LIMIT 1"""),
                    {"run": lease.run_id, "digest": next_digest},
                ).first()
            ):
                raise SyncConflict("CURSOR_LOOP")
            confirmed = session.execute(
                text("""SELECT p.id FROM platform_requests p
                JOIN platform_raw_payloads r ON r.outbound_id=p.id AND r.tenant_id=p.tenant_id
                WHERE p.id=:id AND p.account_id=:account AND p.binding_id=:binding
                 AND p.effect='READ'
                 AND r.phase='RESPONSE' AND r.external_outcome='READ_SUCCEEDED' LIMIT 1"""),
                {"id": permit.outbound_id, "account": ctx.account_id, "binding": ctx.binding_id},
            ).first()
            if confirmed is None:
                raise SyncConflict("NO_CONFIRMED_READ")
            session.execute(
                text("""INSERT INTO sync_pages(tenant_id,run_id,page_no,outbound_id,
                input_cursor_digest,output_cursor_digest,page_digest,terminal,records_count,
                source_updated_at,fetched_at) VALUES(:tenant,:run,:page,:outbound,:input,:output,
                :digest,:terminal,:count,:source_at,:fetched_at)"""),
                {
                    "tenant": ctx.identity.tenant_id,
                    "run": lease.run_id,
                    "page": lease.page_no,
                    "outbound": permit.outbound_id,
                    "input": intent,
                    "output": next_digest,
                    "digest": digest,
                    "terminal": page.terminal,
                    "count": count,
                    "source_at": page.source_at,
                    "fetched_at": page.fetched_at,
                },
            )
            for row, projection in zip(page.records, projections, strict=True):
                session.execute(
                    text("""INSERT INTO sync_resource_mirrors(tenant_id,source_id,native_id,
                    projection,outbound_id,source_at,fetched_at) VALUES(:tenant,:source,:native,
                    CAST(:projection AS jsonb),:outbound,:at,:fetched)
                    ON CONFLICT(tenant_id,source_id,native_id) DO UPDATE
                    SET projection=EXCLUDED.projection,
                    outbound_id=EXCLUDED.outbound_id,source_at=EXCLUDED.source_at,
                    fetched_at=EXCLUDED.fetched_at,updated_at=clock_timestamp()"""),
                    {
                        "tenant": ctx.identity.tenant_id,
                        "source": lease.source_id,
                        "native": row.native_id,
                        "projection": json.dumps(projection),
                        "outbound": permit.outbound_id,
                        "at": page.source_at,
                        "fetched": page.fetched_at,
                    },
                )
            if project_facts is not None:
                project_facts(session)
            encrypted, key_version = self.codec.seal(
                ctx.identity.tenant_id, lease.source_id, page.next_cursor
            )
            terminal = page.terminal
            state = "SUCCESS" if terminal else "RUNNING" if source["enabled"] else "PAUSED"
            session.execute(
                text("""UPDATE sync_sources SET cursor_enc=:cursor,cursor_key_version=:key,
                cursor_digest=:digest,version=version+1,active_run_id=:active WHERE id=:source"""),
                {
                    "cursor": encrypted,
                    "key": key_version,
                    "digest": next_digest,
                    "source": lease.source_id,
                    "active": lease.run_id if state == "RUNNING" else None,
                },
            )
            session.execute(
                text("""UPDATE sync_runs SET source_version=source_version+1,
                page_no=page_no+1,items_count=items_count+:count,state=:state,
                finished_at=CASE WHEN :terminal THEN clock_timestamp() ELSE NULL END
                WHERE id=:run"""),
                {
                    "count": count,
                    "state": state,
                    "run": lease.run_id,
                    "terminal": terminal,
                },
            )
            if state == "RUNNING":
                return SyncLease(
                    lease.run_id,
                    lease.source_id,
                    lease.token,
                    lease.source_version + 1,
                    lease.page_no + 1,
                )
        return self.status(ctx, lease.run_id)

    def stop(
        self,
        ctx: GatewayContext,
        spec: OperationSpec,
        lease: SyncLease,
        state: Literal["PARTIAL", "FAILED", "RATE_LIMITED"] = "PARTIAL",
    ) -> None:
        if state not in {"PARTIAL", "FAILED", "RATE_LIMITED"}:
            raise ValueError("同步中止原因不合法")
        with business_transaction(self.engine, ctx.identity) as session:
            self._locked(session, ctx, spec, lease, allow_paused=True)
            session.execute(
                text("""UPDATE sync_runs SET state=:state,lease_until=clock_timestamp(),
                last_error=:state WHERE id=:id"""),
                {"state": state, "id": lease.run_id},
            )
            session.execute(
                text("UPDATE sync_sources SET active_run_id=NULL WHERE id=:id"),
                {"id": lease.source_id},
            )

    def set_enabled(
        self, ctx: GatewayContext, spec: OperationSpec, source_id: UUID, enabled: bool
    ) -> None:
        if type(enabled) is not bool:
            raise ValueError("同步开关不合法")
        with business_transaction(self.engine, ctx.identity) as session:
            self._read(session, ctx, spec)
            require_shop_account(
                session, ctx.identity, ctx.shop_id, ctx.account_id, "connection.manage"
            )
            row = session.execute(
                text("SELECT context_digest FROM sync_sources WHERE id=:id FOR UPDATE"),
                {"id": source_id},
            ).scalar_one_or_none()
            if row != self._context(ctx, spec):
                raise SyncConflict("SCOPE_MISMATCH")
            session.execute(
                text("UPDATE sync_sources SET enabled=:enabled WHERE id=:id"),
                {"id": source_id, "enabled": enabled},
            )
            record(
                session,
                ctx.identity,
                {
                    "action": "connection.sync.toggle",
                    "object_type": "sync_source",
                    "object_id": source_id,
                    "request_id": ctx.request_id,
                    "after_hash": hashlib.sha256(canonical(enabled)).hexdigest(),
                    "detail": {"changed_fields": ["status"]},
                },
            )

    @staticmethod
    def _summary(row: Any) -> dict[str, Any]:
        return {
            "runId": str(row["id"]),
            "sourceId": str(row["source_id"]),
            "state": row["state"],
            "confirmedPages": row["page_no"],
            "itemsCount": row["items_count"],
            "sourceVersion": row["source_version"],
        }

    def status(self, ctx: GatewayContext, run_id: UUID) -> dict[str, Any]:
        with business_transaction(self.engine, ctx.identity) as session:
            row = (
                session.execute(
                    text("""SELECT r.* FROM sync_runs r JOIN sync_sources s
                        ON (s.tenant_id,s.id)=(r.tenant_id,r.source_id)
                        WHERE r.id=:id AND r.user_id=:user AND s.account_id=:account
                          AND s.binding_id=:binding"""),
                    {
                        "id": run_id,
                        "user": ctx.identity.user_id,
                        "account": ctx.account_id,
                        "binding": ctx.binding_id,
                    },
                )
                .mappings()
                .first()
            )
            if row is None:
                raise SyncConflict("SCOPE_MISMATCH")
            return self._summary(row)
