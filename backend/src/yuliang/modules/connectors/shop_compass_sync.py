"""罗盘固定单日只读同步；页、观察与确认水位原子提交。"""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.modules.commerce.compass_observations import save_compass
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.governance import CallDeferred
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.shop_compass_protocol import (
    COMPASS_POLICY,
    CompassParameters,
    compass_page,
    compass_spec,
)
from yuliang.modules.connectors.shop_read_scope import require_shop_read
from yuliang.modules.connectors.sync_store import SyncConflict, SyncLease, SyncStore
from yuliang.modules.reporting.service import FactConflict, FactWriter
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class ShopCompassSync:
    def __init__(
        self,
        store: SyncStore,
        client: GovernedPlatformClient,
        tokens: Callable[[GatewayContext], Awaitable[SecretStr]],
        allowed_interfaces: tuple[str, ...],
        required_scopes: tuple[str, ...],
        writer: FactWriter,
    ) -> None:
        if (
            store.engine is not writer.engine
            or store.policy != COMPASS_POLICY
            or not required_scopes
            or len(set(required_scopes)) != len(required_scopes)
        ):
            raise ValueError("罗盘须独立白名单策略与已确认scope")
        self.store, self.client, self.tokens = store, client, tokens
        self.writer = writer
        self.allowed, self.scopes = frozenset(allowed_interfaces), frozenset(required_scopes)

    def _scope(self, session: Session, ctx: GatewayContext) -> int:
        return require_shop_read(session, ctx, self.client.application_id, self.scopes)

    async def sync(self, ctx: GatewayContext, run_id: UUID, *, day: date) -> dict[str, Any]:
        if type(day) is not date:
            raise ValueError("必须指定明确自然日")
        spec = compass_spec(ctx)
        if spec.path not in self.allowed:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "未核对罗盘只读入口")
        parameters = CompassParameters(ds=day.strftime("%Y%m%d")).model_dump()
        with business_transaction(self.store.engine, ctx.identity) as session:
            ctx = replace(ctx, authorization_version=self._scope(session, ctx))
            session.execute(
                text("""INSERT INTO shop_compass_runs
              (id,tenant_id,account_id,binding_id,authorization_id,authorization_version,report_date)
              VALUES(:id,:tenant,:account,:binding,:auth,:version,:day)
              ON CONFLICT(id) DO NOTHING"""),
                dict(
                    id=run_id,
                    tenant=ctx.identity.tenant_id,
                    account=ctx.account_id,
                    binding=ctx.binding_id,
                    auth=ctx.authorization_id,
                    version=ctx.authorization_version,
                    day=day,
                ),
            )
            pinned = session.execute(
                text("""SELECT account_id,binding_id,authorization_id,
              authorization_version,report_date FROM shop_compass_runs WHERE id=:id"""),
                dict(id=run_id),
            ).first()
            if pinned is None or tuple(pinned) != (
                ctx.account_id,
                ctx.binding_id,
                ctx.authorization_id,
                ctx.authorization_version,
                day,
            ):
                raise SyncConflict("RESUME_ORIGINAL_AUTHORIZATION_RUN")
        storage_ctx = replace(ctx, authorization_version=None)
        lease = self.store.begin(
            storage_ctx, spec, "wxshop.compass.overall", run_id, parameters, restart_cursor=True
        )
        if not isinstance(lease, SyncLease):
            return lease
        active = lease
        permit = self.store.prepare(storage_ctx, spec, lease)

        def guard(session: Session) -> None:
            self._scope(session, ctx)
            self.store.dispatch_guard(session, storage_ctx, spec, permit, parameters)

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
            page = compass_page(result.exchange, day)

            def project(session: Session) -> None:
                self._scope(session, ctx)
                save_compass(session, ctx, permit, day, page, self.writer)

            completed = self.store.commit_page(
                storage_ctx, spec, permit, page, project_facts=project
            )
            if isinstance(completed, SyncLease):
                raise SyncConflict("UNEXPECTED_COMPASS_CONTINUATION")
            return completed
        except (
            CallDeferred,
            ValueError,
            SyncConflict,
            ApiError,
            DBAPIError,
            FactConflict,
        ) as error:
            try:
                self.store.stop(
                    storage_ctx,
                    spec,
                    active,
                    "RATE_LIMITED" if isinstance(error, CallDeferred) else "PARTIAL",
                )
            except (SyncConflict, ApiError):
                pass
            raise

    def observations(self, ctx: GatewayContext, *, day: date) -> dict[str, Any]:
        spec = compass_spec(ctx)
        if spec.path not in self.allowed:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "未核对罗盘只读入口")
        with business_transaction(self.store.engine, ctx.identity) as session:
            self._scope(session, ctx)
            self.store._read(session, ctx, spec)
            latest = session.execute(
                text("""SELECT r.state,r.started_at,r.id FROM sync_runs r
              JOIN shop_compass_runs c ON c.id=r.id AND c.tenant_id=r.tenant_id
              WHERE c.binding_id=:binding AND c.account_id=:account AND c.report_date=:day
              ORDER BY r.started_at DESC,r.id DESC LIMIT 1"""),
                dict(binding=ctx.binding_id, account=ctx.account_id, day=day),
            ).first()
            row = session.execute(
                text("""SELECT o.observation,o.fetched_at,o.contract_id,o.fact_decisions
              FROM shop_compass_observations o JOIN sync_sources s
              ON (s.tenant_id,s.id)=(o.tenant_id,o.source_id)
              WHERE s.binding_id=:binding AND o.account_id=:account AND o.shop_id=:shop
              AND o.report_date=:day ORDER BY o.fetched_at DESC,o.run_id DESC LIMIT 1"""),
                dict(binding=ctx.binding_id, account=ctx.account_id, shop=ctx.shop_id, day=day),
            ).first()
            return dict(
                state="NOT_SYNCED"
                if latest is None
                else "UPSTREAM_ERROR"
                if latest.state in {"PARTIAL", "FAILED", "RATE_LIMITED"}
                else row.observation["state"]
                if row
                else "SYNCING",
                observations=row.observation if row else None,
                fetchedAt=row.fetched_at.isoformat() if row else None,
                sourceUpdatedAt=None,
                freshness="UNCONFIRMED",
                contractId=str(row.contract_id) if row and row.contract_id else None,
                decisions=row.fact_decisions if row else [],
                lastRunState=latest.state if latest else None,
            )
