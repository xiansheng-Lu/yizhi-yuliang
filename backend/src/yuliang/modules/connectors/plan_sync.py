"""复用统一受控调用/唯一token provider的计划完整分页同步。"""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.orm import Session

from yuliang.modules.connectors.adq_assets import AssetPage, native_integer
from yuliang.modules.connectors.adq_read_scope import require_adq_read
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.governance import CallDeferred
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.plan_protocol import FIELDS, POLICY, PlanLevel, plan_page, plan_spec
from yuliang.modules.connectors.sync_store import PagePermit, SyncConflict, SyncLease, SyncStore
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class AdqPlanSync:
    def __init__(
        self,
        store: SyncStore,
        client: GovernedPlatformClient,
        tokens: Callable[[GatewayContext], Awaitable[SecretStr]],
        allowed_interfaces: tuple[str, ...],
        required_scopes: tuple[str, ...],
    ) -> None:
        if not required_scopes or len(set(required_scopes)) != len(required_scopes):
            raise ValueError("ADQ同步需要已核对的实际scope映射")
        if store.policy != POLICY:
            raise ValueError("ADQ镜像必须使用固定最小字段投影")
        self.store, self.client, self.tokens = store, client, tokens
        self.allowed, self.scopes = frozenset(allowed_interfaces), frozenset(required_scopes)

    def _scope(self, session: Session, ctx: GatewayContext) -> int:
        return require_adq_read(session, ctx, self.client.application_id, self.scopes)

    async def sync(
        self, ctx: GatewayContext, kind: PlanLevel, run_id: UUID, *, page_size: int = 100
    ) -> dict[str, Any]:
        spec = plan_spec(ctx, kind)
        if spec.path not in self.allowed:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "应用未明确核对该资产只读接口")
        native_integer(ctx.native_account_id, positive=True)
        with business_transaction(self.store.engine, ctx.identity) as session:
            version = self._scope(session, ctx)
        ctx = replace(ctx, authorization_version=version)
        parameters = spec.parameters_model.model_validate(
            dict(
                account_id=ctx.native_account_id,
                fields=list(FIELDS[kind]),
                page=1,
                page_size=page_size,
            ),
            strict=True,
        ).model_dump()
        lease = self.store.begin(ctx, spec, f"adq.plans.{kind.lower()}", run_id, parameters)
        while isinstance(lease, SyncLease):
            active_lease = lease
            permit = self.store.prepare(ctx, spec, lease)
            wire_page = int(permit.cursor.get_secret_value()) if permit.cursor else 1
            if wire_page != lease.page_no + 1:
                raise SyncConflict("RESUME_ORIGINAL_RUN")
            page_params = parameters | dict(page=wire_page)

            def guard(
                session: Session,
                permit: PagePermit = permit,
                page_params: dict[str, Any] = page_params,
            ) -> None:
                self._scope(session, ctx)
                self.store.dispatch_guard(session, ctx, spec, permit, page_params)

            try:
                result = await self.client.send(
                    ctx,
                    spec,
                    permit.outbound_id,
                    page_params,
                    await self.tokens(ctx),
                    lane="SYNC",
                    before_persist=guard,
                )
            except CallDeferred:
                self.store.stop(ctx, spec, active_lease, "RATE_LIMITED")
                raise
            if result.exchange is None:
                self.store.stop(ctx, spec, lease, "PARTIAL")
                raise SyncConflict("READ_UNAVAILABLE")
            try:
                page = plan_page(ctx, kind, result.exchange, wire_page, page_size)

                def project(
                    session: Session, permit: PagePermit = permit, page: AssetPage = page
                ) -> None:
                    self._scope(session, ctx)
                    from yuliang.modules.delivery.plan_reads import project_plan_page

                    project_plan_page(session, ctx, kind, permit, page)

                lease = self.store.commit_page(ctx, spec, permit, page.sync, project_facts=project)
            except (ValueError, SyncConflict, ApiError):
                self.store.stop(ctx, spec, active_lease, "PARTIAL")
                raise
        return lease
