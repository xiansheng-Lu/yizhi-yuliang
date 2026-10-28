"""整账户日报/小时报表：逐日来源、受控出站、同事务事实和有界历史重拉。"""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.modules.connectors.adq_finance_protocol import int64_text
from yuliang.modules.connectors.adq_read_scope import require_adq_read
from yuliang.modules.connectors.adq_report_protocol import (
    REPORT_MIRROR_POLICY,
    VALUE_FIELDS,
    ReportPage,
    ReportParameters,
    ReportQuery,
    parse_report,
    report_spec,
)
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.governance import CallDeferred
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.sync_store import PagePermit, SyncConflict, SyncLease, SyncStore
from yuliang.modules.reporting.adq_batches import batch_status, mark_day, start_batch
from yuliang.modules.reporting.adq_observations import observation_view
from yuliang.modules.reporting.adq_projection import project_report
from yuliang.modules.reporting.service import FactConflict, FactWriter
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class AdqReportSync:
    def __init__(
        self,
        store: SyncStore,
        client: GovernedPlatformClient,
        tokens: Callable[[GatewayContext], Awaitable[SecretStr]],
        writer: FactWriter,
        allowed_interfaces: tuple[str, ...],
        required_scopes: tuple[str, ...],
    ) -> None:
        if (
            not required_scopes
            or len(set(required_scopes)) != len(required_scopes)
            or store.policy != REPORT_MIRROR_POLICY
            or store.engine is not writer.engine
        ):
            raise ValueError("报表需要实际scope、固定投影和同一事实事务数据库")
        self.store, self.client, self.tokens, self.writer = store, client, tokens, writer
        self.allowed, self.scopes = frozenset(allowed_interfaces), frozenset(required_scopes)

    def _scope(self, session: Session, ctx: GatewayContext) -> int:
        return require_adq_read(session, ctx, self.client.application_id, self.scopes)

    def observations(
        self,
        ctx: GatewayContext,
        batch_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        with business_transaction(self.store.engine, ctx.identity) as session:
            ctx = replace(ctx, authorization_version=self._scope(session, ctx))
            batch = session.execute(
                text("SELECT kind FROM adq_report_batches WHERE id=:id"), dict(id=batch_id)
            ).scalar_one_or_none()
            if batch is None or batch not in {"DAILY", "HOURLY"}:
                raise SyncConflict("REPORT_BATCH_CONTEXT_CHANGED")
            spec = report_spec(ctx, batch)
            if spec.path not in self.allowed:
                raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "应用未开放该报表观察入口")
            self.store._read(session, ctx, spec)
            return observation_view(session, ctx, batch_id, limit=limit, offset=offset)

    async def sync(
        self,
        ctx: GatewayContext,
        batch_id: UUID,
        query: ReportQuery,
        *,
        page_size: int = 100,
    ) -> dict[str, Any]:
        query = ReportQuery.model_validate(query.model_dump())
        spec = report_spec(ctx, query.kind)
        int64_text(ctx.native_account_id, positive=True)
        if spec.path not in self.allowed:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "应用未核对固定版本的报表只读接口")
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("报表分页大小必须为1至100的整数")
        with business_transaction(self.store.engine, ctx.identity) as session:
            ctx = replace(ctx, authorization_version=self._scope(session, ctx))
            batch = start_batch(session, ctx, batch_id, query, page_size, self.store)
            contract_id = batch["contract_id"]
            days = (
                session.execute(
                    text(
                        "SELECT * FROM adq_report_batch_days "
                        "WHERE batch_id=:id ORDER BY report_date"
                    ),
                    dict(id=batch_id),
                )
                .mappings()
                .all()
            )
        # 逻辑来源跨令牌版本稳定；每次出站/提交仍在实际ctx上强制核对批次版本。
        storage_ctx = replace(ctx, authorization_version=None)
        for item in days:
            if item["state"] == "SUCCESS":
                continue
            day: date = item["report_date"]
            parameters = ReportParameters.model_validate(
                dict(
                    account_id=ctx.native_account_id,
                    date_range=dict(start_date=day.isoformat(), end_date=day.isoformat()),
                    time_line=query.time_line,
                    page_size=page_size,
                    fields=[
                        "account_id",
                        "date" if query.kind == "DAILY" else "hour",
                        *VALUE_FIELDS,
                    ],
                ),
                strict=True,
            ).model_dump()
            lease = self.store.begin(
                storage_ctx,
                spec,
                f"adq.report.{query.kind.lower()}",
                item["run_id"],
                parameters,
                restart_cursor=True,
            )
            while isinstance(lease, SyncLease):
                active_lease = lease
                with business_transaction(self.store.engine, ctx.identity) as session:
                    self._scope(session, ctx)
                    mark_day(session, batch_id, day, "RUNNING", lease.source_id)
                permit = self.store.prepare(storage_ctx, spec, lease)
                wire_page = int(permit.cursor.get_secret_value()) if permit.cursor else 1
                if wire_page != lease.page_no + 1:
                    raise SyncConflict("RESUME_ORIGINAL_RUN")
                payload = parameters | dict(page=wire_page)

                def guard(
                    session: Session,
                    permit: PagePermit = permit,
                    payload: dict[str, Any] = payload,
                ) -> None:
                    self._scope(session, ctx)
                    self.store.dispatch_guard(session, storage_ctx, spec, permit, payload)

                try:
                    result = await self.client.send(
                        ctx,
                        spec,
                        permit.outbound_id,
                        payload,
                        await self.tokens(ctx),
                        lane="SYNC",
                        before_persist=guard,
                    )
                    if result.exchange is None:
                        raise SyncConflict("READ_UNAVAILABLE")
                    page = parse_report(
                        result.exchange,
                        query.kind,
                        day,
                        ctx.native_account_id,
                        wire_page,
                        page_size,
                    )

                    def project(
                        session: Session,
                        permit: PagePermit = permit,
                        page: ReportPage = page,
                        day: date = day,
                    ) -> None:
                        self._scope(session, ctx)
                        project_report(
                            session,
                            ctx,
                            query,
                            day,
                            permit,
                            page,
                            self.store,
                            self.writer,
                            contract_id,
                        )

                    lease = self.store.commit_page(
                        storage_ctx,
                        spec,
                        permit,
                        page.sync,
                        project_facts=project,
                    )
                except (
                    CallDeferred,
                    ValueError,
                    SyncConflict,
                    ApiError,
                    FactConflict,
                    DBAPIError,
                ) as e:
                    state: Literal["RATE_LIMITED", "PARTIAL"] = (
                        "RATE_LIMITED" if isinstance(e, CallDeferred) else "PARTIAL"
                    )
                    try:
                        self.store.stop(storage_ctx, spec, active_lease, state)
                    except (SyncConflict, ApiError):
                        pass  # 当前授权撤销时不得弱化守卫来清理旧租约。
                    with business_transaction(self.store.engine, ctx.identity) as session:
                        mark_day(session, batch_id, day, state)
                        batch_status(session, batch_id)
                    raise
            with business_transaction(self.store.engine, ctx.identity) as session:
                self._scope(session, ctx)
                mark_day(session, batch_id, day, "SUCCESS", UUID(lease["sourceId"]))
                batch_status(session, batch_id)
        with business_transaction(self.store.engine, ctx.identity) as session:
            self._scope(session, ctx)
            return batch_status(session, batch_id)
