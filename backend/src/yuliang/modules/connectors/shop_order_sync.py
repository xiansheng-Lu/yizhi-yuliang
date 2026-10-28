"""小店订单、售后与独立商家结算受控读取及追加观察。"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.governance import CallDeferred
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.shop_orders import (
    ORDER_POLICY,
    Kind,
    order_observation,
    shop_order_spec,
)
from yuliang.modules.connectors.shop_read_scope import require_shop_read
from yuliang.modules.connectors.sync_store import (
    PagePermit,
    SyncConflict,
    SyncLease,
    SyncPage,
    SyncStore,
)
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class ShopOrderSync:
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
        if store.policy != ORDER_POLICY:
            raise ValueError("订单/结算使用独立最小字段策略")
        self.store, self.client, self.tokens = store, client, tokens
        self.allowed, self.scopes = frozenset(allowed_interfaces), frozenset(required_scopes)

    def _scope(self, session: Session, ctx: GatewayContext) -> int:
        return require_shop_read(session, ctx, self.client.application_id, self.scopes)

    async def sync(
        self,
        ctx: GatewayContext,
        run_id: UUID,
        *,
        kind: Kind,
        order_id: str | None = None,
        parameters_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        spec = shop_order_spec(kind, ctx.catalog_id, ctx.catalog_sha256)
        if spec.path not in self.allowed:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "未核对订单/商家结算读取入口")
        parameters = spec.parameters_model.model_validate(
            (parameters_input or {})
            if kind in {"ORDER_LIST", "AFTERSALE_LIST"}
            else (
                dict(order_id=order_id)
                if kind == "ORDER"
                else dict(after_sale_order_id=order_id)
                if kind == "AFTERSALE"
                else {}
            ),
            strict=True,
        ).model_dump()
        if kind in {"ORDER_LIST", "AFTERSALE_LIST"} and parameters.get("next_key"):
            raise ValueError("完整列表只能从首页开始，续跑游标由服务器保管")
        with business_transaction(self.store.engine, ctx.identity) as session:
            version = self._scope(session, ctx)
        ctx = replace(ctx, authorization_version=version)
        lease = self.store.begin(
            ctx,
            spec,
            "wxshop." + kind.lower(),
            run_id,
            parameters,
        )
        while isinstance(lease, SyncLease):
            active = lease
            permit = self.store.prepare(ctx, spec, lease)
            payload = parameters | (
                {"next_key": permit.cursor.get_secret_value() if permit.cursor else ""}
                if kind in {"ORDER_LIST", "AFTERSALE_LIST"}
                else {}
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
                page = order_observation(kind, result.exchange, order_id)

                def check(
                    session: Session, permit: PagePermit = permit, page: SyncPage = page
                ) -> None:
                    self._scope(session, ctx)
                    for row_no, observation in enumerate(page.records):
                        session.execute(
                            text("""INSERT INTO shop_order_observations
                            (tenant_id,run_id,page_no,row_no,outbound_id,kind,native_id,values,fetched_at)
                            VALUES(:tenant,:run,:page,:row,:outbound,:kind,:native,
                            CAST(:values AS jsonb),:at)"""),
                            dict(
                                tenant=ctx.identity.tenant_id,
                                run=permit.lease.run_id,
                                page=permit.lease.page_no,
                                row=row_no,
                                outbound=permit.outbound_id,
                                kind=kind,
                                native=observation.native_id,
                                values=json.dumps(observation.values),
                                at=page.fetched_at,
                            ),
                        )

                lease = self.store.commit_page(ctx, spec, permit, page, project_facts=check)
            except CallDeferred:
                self.store.stop(ctx, spec, active, "RATE_LIMITED")
                raise
            except (ValueError, SyncConflict, ApiError):
                self.store.stop(ctx, spec, active, "PARTIAL")
                raise
        return lease
