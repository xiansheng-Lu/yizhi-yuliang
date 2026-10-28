"""ADQ固定资金和操作日志读取；分页、归一化与实际应用资源守卫同链路。"""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, Literal
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.orm import Session

from yuliang.modules.connectors.adq_finance_protocol import (
    FINANCE_MIRROR_POLICY,
    FUNDS_FIELDS,
    OPERATION_FIELDS,
    FinancePage,
    OperationQuery,
    finance_spec,
    int64_text,
    parse_funds,
    parse_operations,
)
from yuliang.modules.connectors.adq_read_scope import require_adq_read
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.governance import CallDeferred
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.sync_store import PagePermit, SyncConflict, SyncLease, SyncStore
from yuliang.modules.delivery.finance_reads import (
    operations_projection,
    project_finance,
    wallet_projection,
)
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class AdqFinanceSync:
    def __init__(
        self,
        store: SyncStore,
        client: GovernedPlatformClient,
        tokens: Callable[[GatewayContext], Awaitable[SecretStr]],
        allowed_interfaces: tuple[str, ...],
        required_scopes: tuple[str, ...],
    ) -> None:
        if not required_scopes or len(set(required_scopes)) != len(required_scopes):
            raise ValueError("财务只读同步需要实际scope映射")
        if store.policy != FINANCE_MIRROR_POLICY:
            raise ValueError("财务只读同步使用固定最小字段投影")
        self.store, self.client, self.tokens = store, client, tokens
        self.allowed, self.scopes = frozenset(allowed_interfaces), frozenset(required_scopes)

    def _scope(self, session: Session, ctx: GatewayContext) -> int:
        return require_adq_read(session, ctx, self.client.application_id, self.scopes)

    def _context(self, ctx: GatewayContext) -> GatewayContext:
        with business_transaction(self.store.engine, ctx.identity) as session:
            version = self._scope(session, ctx)
        return replace(ctx, authorization_version=version)

    async def sync_funds(self, ctx: GatewayContext, run_id: UUID) -> dict[str, Any]:
        return await self._sync(ctx, "FUNDS", run_id)

    async def sync_operations(
        self, ctx: GatewayContext, run_id: UUID, query: OperationQuery, *, page_size: int = 100
    ) -> dict[str, Any]:
        query = OperationQuery.model_validate(query.model_dump())
        return await self._sync(ctx, "OPERATIONS", run_id, query, page_size)

    async def _sync(
        self,
        ctx: GatewayContext,
        kind: Literal["FUNDS", "OPERATIONS"],
        run_id: UUID,
        query: OperationQuery | None = None,
        page_size: int = 100,
    ) -> dict[str, Any]:
        spec = finance_spec(ctx, kind)
        if spec.path not in self.allowed:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "应用未明确核对该财务只读接口")
        int64_text(ctx.native_account_id, positive=True)
        ctx = self._context(ctx)
        parameters: dict[str, Any] = dict(
            account_id=ctx.native_account_id, fields=list(FUNDS_FIELDS)
        )
        if kind == "OPERATIONS":
            if query is None:
                raise ValueError("操作日志需要明确对象层级与日期窗口")
            parameters = query.model_dump() | dict(
                account_id=ctx.native_account_id,
                fields=list(OPERATION_FIELDS),
                page=1,
                page_size=page_size,
            )
        parameters = spec.parameters_model.model_validate(parameters, strict=True).model_dump()
        lease = self.store.begin(ctx, spec, f"adq.finance.{kind.lower()}", run_id, parameters)
        while isinstance(lease, SyncLease):
            active_lease = lease
            permit = self.store.prepare(ctx, spec, lease)
            wire_page = int(permit.cursor.get_secret_value()) if permit.cursor else 1
            if wire_page != lease.page_no + 1:
                raise SyncConflict("RESUME_ORIGINAL_RUN")
            payload = parameters | dict(page=wire_page) if kind == "OPERATIONS" else parameters

            def guard(
                session: Session, permit: PagePermit = permit, payload: dict[str, Any] = payload
            ) -> None:
                self._scope(session, ctx)
                self.store.dispatch_guard(session, ctx, spec, permit, payload)

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
            except CallDeferred:
                self.store.stop(ctx, spec, active_lease, "RATE_LIMITED")
                raise
            if result.exchange is None:
                self.store.stop(ctx, spec, active_lease, "PARTIAL")
                raise SyncConflict("READ_UNAVAILABLE")
            try:
                if kind == "FUNDS":
                    page = parse_funds(result.exchange, ctx.native_account_id)
                else:
                    if query is None:
                        raise ValueError("操作日志缺少明确查询上下文")
                    page = parse_operations(
                        result.exchange, query, wire_page, page_size, ctx.native_account_id
                    )

                def project(
                    session: Session, permit: PagePermit = permit, page: FinancePage = page
                ) -> None:
                    self._scope(session, ctx)
                    project_finance(session, ctx, kind, permit, page, self.store)

                lease = self.store.commit_page(ctx, spec, permit, page.sync, project_facts=project)
            except (ValueError, SyncConflict, ApiError):
                self.store.stop(ctx, spec, active_lease, "PARTIAL")
                raise
        return lease

    def wallet(self, ctx: GatewayContext, source_id: UUID) -> dict[str, Any]:
        if finance_spec(ctx, "FUNDS").path not in self.allowed:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "应用未开放该钱包读取入口")
        ctx = self._context(ctx)
        with business_transaction(self.store.engine, ctx.identity) as session:
            self._scope(session, ctx)
            return wallet_projection(session, ctx, source_id, self.store)

    def operations(
        self,
        ctx: GatewayContext,
        source_id: UUID,
        *,
        run_id: UUID | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        if finance_spec(ctx, "OPERATIONS").path not in self.allowed:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "应用未开放该日志读取入口")
        ctx = self._context(ctx)
        with business_transaction(self.store.engine, ctx.identity) as session:
            self._scope(session, ctx)
            return operations_projection(
                session, ctx, source_id, self.store, run_id=run_id, offset=offset, limit=limit
            )
