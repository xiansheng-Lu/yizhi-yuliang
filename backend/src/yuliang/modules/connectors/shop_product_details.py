"""小店商品详情单产品原子同步，独立观察和依据投影。"""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.orm import Session

from yuliang.modules.commerce.shop_details import product_view, project_detail
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.governance import CallDeferred
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.shop_detail_protocol import (
    DETAIL_MIRROR_POLICY,
    detail_spec,
    identifier,
    parse_detail,
)
from yuliang.modules.connectors.shop_read_scope import require_shop_read
from yuliang.modules.connectors.sync_store import SyncConflict, SyncLease, SyncStore
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class ShopProductDetailSync:
    def __init__(
        self,
        store: SyncStore,
        client: GovernedPlatformClient,
        tokens: Callable[[GatewayContext], Awaitable[SecretStr]],
        allowed_interfaces: tuple[str, ...],
        required_scopes: tuple[str, ...],
    ) -> None:
        if store.policy != DETAIL_MIRROR_POLICY or not required_scopes:
            raise ValueError("详情同步需要独立最小字段与实际scope")
        self.store, self.client, self.tokens = store, client, tokens
        self.allowed, self.scopes = frozenset(allowed_interfaces), frozenset(required_scopes)

    def _scope(self, session: Session, ctx: GatewayContext) -> int:
        return require_shop_read(session, ctx, self.client.application_id, self.scopes)

    def _context(self, ctx: GatewayContext) -> GatewayContext:
        with business_transaction(self.store.engine, ctx.identity) as session:
            version = self._scope(session, ctx)
        return replace(ctx, authorization_version=version)

    async def sync(self, ctx: GatewayContext, run_id: UUID, product_id: str) -> dict[str, Any]:
        product_id = identifier(product_id)
        spec = detail_spec(ctx)
        if spec.path not in self.allowed:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "未开放商品详情只读")
        ctx = self._context(ctx)
        parameters = dict(product_id=product_id)
        lease = self.store.begin(ctx, spec, "wxshop.product.detail", run_id, parameters)
        if not isinstance(lease, SyncLease):
            return lease
        permit = self.store.prepare(ctx, spec, lease)

        def guard(session: Session) -> None:
            self._scope(session, ctx)
            self.store.dispatch_guard(session, ctx, spec, permit, parameters)

        try:
            result = await self.client.send(
                ctx,
                spec,
                permit.outbound_id,
                parameters,
                await self.tokens(ctx),
                lane="SYNC",
                before_persist=guard,
            )
            if result.exchange is None:
                raise SyncConflict("READ_UNAVAILABLE")
            page = parse_detail(result.exchange, product_id)

            def project(session: Session) -> None:
                self._scope(session, ctx)
                project_detail(session, ctx, permit, page)

            completed = self.store.commit_page(ctx, spec, permit, page.sync, project_facts=project)
            if isinstance(completed, SyncLease):
                raise SyncConflict("DETAIL_NOT_TERMINAL")
            return completed
        except CallDeferred:
            self.store.stop(ctx, spec, lease, "RATE_LIMITED")
            raise
        except (ValueError, SyncConflict, ApiError):
            self.store.stop(ctx, spec, lease, "PARTIAL")
            raise

    def view(self, ctx: GatewayContext, product_id: str) -> dict[str, Any]:
        if detail_spec(ctx).path not in self.allowed:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "未开放商品详情只读")
        ctx = self._context(ctx)
        with business_transaction(self.store.engine, ctx.identity) as session:
            self._scope(session, ctx)
            return product_view(session, ctx, identifier(product_id))
