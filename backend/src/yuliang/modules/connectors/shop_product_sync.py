"""小店商品标识受控同步；仅观察标识，不推断SKU/库存/下架。"""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.capability.service import evaluate_binding
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.governance import CallDeferred
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.native_reads import parse_page, read_spec
from yuliang.modules.connectors.shop_products import PRODUCT_ID_POLICY, product_id_page
from yuliang.modules.connectors.sync_store import PagePermit, SyncConflict, SyncLease, SyncStore
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class ShopProductIdSync:
    def __init__(
        self,
        store: SyncStore,
        client: GovernedPlatformClient,
        tokens: Callable[[GatewayContext], Awaitable[SecretStr]],
        allowed_interfaces: tuple[str, ...],
        required_scopes: tuple[str, ...],
    ) -> None:
        if not required_scopes or len(set(required_scopes)) != len(required_scopes):
            raise ValueError("小店同步需要实际scope映射")
        if store.policy != PRODUCT_ID_POLICY:
            raise ValueError("商品标识使用独立最小字段策略")
        self.store, self.client, self.tokens = store, client, tokens
        self.allowed, self.scopes = frozenset(allowed_interfaces), frozenset(required_scopes)

    def _scope(self, session: Session, ctx: GatewayContext) -> int:
        if (ctx.connector_id, ctx.provider, ctx.product_line) != ("wxshop", "wxshop", "wxshop"):
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "小店产品线不匹配")
        grant = session.execute(
            text("""SELECT p.version,p.scopes,p.application_id
          FROM provider_authorizations p JOIN authorization_account_links l
          ON l.tenant_id=p.tenant_id AND l.authorization_id=p.id
          JOIN ad_accounts a ON (a.tenant_id,a.id)=(l.tenant_id,l.account_id)
          WHERE p.id=:grant AND a.id=:account AND a.native_id=:native
          AND l.native_id_snapshot=a.native_id AND a.status='AUTHORIZED'
          AND a.deleted_at IS NULL AND p.status='ACTIVE' AND p.expires_at>clock_timestamp()
          AND p.connector_id='wxshop' AND p.provider='wxshop' AND p.product_line='wxshop'"""),
            dict(grant=ctx.authorization_id, account=ctx.account_id, native=ctx.native_account_id),
        ).first()
        state = evaluate_binding(session, ctx.identity, ctx.shop_id, ctx.binding_id)
        if (
            grant is None
            or grant.application_id != self.client.application_id
            or not self.scopes <= set(grant.scopes)
            or state["status"] != "AUTHORIZED"
        ):
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "小店授权范围或只读证据未就绪")
        if ctx.authorization_version is not None and ctx.authorization_version != grant.version:
            raise SyncConflict("AUTHORIZATION_CHANGED")
        return int(grant.version)

    async def sync(
        self, ctx: GatewayContext, run_id: UUID, *, page_size: int = 100
    ) -> dict[str, Any]:
        spec = replace(
            read_spec("wxshop.products", ctx.catalog_id, ctx.catalog_sha256), initial_cursor=""
        )
        if spec.path not in self.allowed:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "未核对商品标识读取入口")
        parameters = spec.parameters_model.model_validate(
            dict(page_size=page_size), strict=True
        ).model_dump()
        with business_transaction(self.store.engine, ctx.identity) as session:
            version = self._scope(session, ctx)
        ctx = replace(ctx, authorization_version=version)
        lease = self.store.begin(ctx, spec, "wxshop.product_ids", run_id, parameters)
        while isinstance(lease, SyncLease):
            active = lease
            permit = self.store.prepare(ctx, spec, lease)
            payload = parameters | dict(
                next_key=permit.cursor.get_secret_value() if permit.cursor else ""
            )

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
                if result.exchange is None:
                    raise SyncConflict("READ_UNAVAILABLE")
                page = product_id_page(
                    parse_page("wxshop.products", result.exchange), requested_size=page_size
                )

                def check(session: Session) -> None:
                    self._scope(session, ctx)

                lease = self.store.commit_page(ctx, spec, permit, page, project_facts=check)
            except CallDeferred:
                self.store.stop(ctx, spec, active, "RATE_LIMITED")
                raise
            except (ValueError, SyncConflict, ApiError):
                self.store.stop(ctx, spec, active, "PARTIAL")
                raise
        return lease
