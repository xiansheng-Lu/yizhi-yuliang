"""真实自定义人群全量受控分页；完整run才供LookupPage使用。"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.modules.configuration.lookup_sources import LookupSourceProof, lookup_source
from yuliang.modules.configuration.models import ConfigurationContext
from yuliang.modules.configuration.service import ConfigurationService
from yuliang.modules.connectors.adq_read_scope import require_adq_read
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.governance import CallDeferred
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.lookup_protocol import (
    LOOKUP_FIELDS,
    LOOKUP_POLICY,
    AudienceParameters,
    audience_page,
    audience_spec,
)
from yuliang.modules.connectors.sync_store import (
    PagePermit,
    SyncConflict,
    SyncLease,
    SyncPage,
    SyncStore,
)
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class AudienceLookupSync:
    def __init__(
        self,
        store: SyncStore,
        client: GovernedPlatformClient,
        tokens: Callable[[GatewayContext], Awaitable[SecretStr]],
        allowed_interfaces: tuple[str, ...],
    ) -> None:
        if store.policy != LOOKUP_POLICY:
            raise ValueError("人群同步必须使用独立白名单")
        self.store, self.client, self.tokens = store, client, tokens
        self.allowed = frozenset(allowed_interfaces)

    def scope(
        self, session: Session, ctx: GatewayContext, context: ConfigurationContext
    ) -> LookupSourceProof:
        if (
            context.product != "adq"
            or context.account_id != ctx.account_id
            or context.shop_id != str(ctx.shop_id)
            or not ConfigurationService._allowed(session, context, "snapshot.read")
        ):
            raise ApiError(ErrorCode.FORBIDDEN, "无当前资源上下文权限")
        proof = lookup_source(session, context)
        if proof is None:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "该上下文尚无人群资源适用依据")
        version = require_adq_read(
            session, ctx, self.client.application_id, frozenset(proof.required_scopes)
        )
        if (
            proof.binding_version != ctx.capability_version
            or proof.binding_id != ctx.binding_id
            or proof.authorization_id != ctx.authorization_id
            or proof.authorization_version != version
        ):
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "资源来源授权或版本已改变")
        spec = audience_spec(ctx)
        if spec.path not in self.allowed:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "应用未开放人群只读来源")
        self.store._read(session, ctx, spec)
        return proof

    async def sync(
        self,
        ctx: GatewayContext,
        context: ConfigurationContext,
        run_id: UUID,
        *,
        page_size: int = 100,
    ) -> dict[str, Any]:
        spec = audience_spec(ctx)
        with business_transaction(self.store.engine, ctx.identity) as session:
            proof = self.scope(session, ctx, context)
        ctx = replace(ctx, authorization_version=proof.authorization_version)
        parameters = AudienceParameters(
            account_id=ctx.native_account_id, page_size=page_size, fields=list(LOOKUP_FIELDS)
        ).model_dump()
        lease = self.store.begin(
            ctx, spec, "lookup.audiences." + proof.id.hex, run_id, parameters, restart_cursor=True
        )
        while isinstance(lease, SyncLease):
            active = lease
            permit = self.store.prepare(ctx, spec, lease)
            number = int(permit.cursor.get_secret_value()) if permit.cursor else 1
            payload = parameters | dict(page=number)

            def guard(
                session: Session, permit: PagePermit = permit, payload: dict[str, Any] = payload
            ) -> None:
                current = self.scope(session, ctx, context)
                if current.id != proof.id:
                    raise SyncConflict("LOOKUP_CONTEXT_CHANGED")
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
                    raise SyncConflict("LOOKUP_READ_UNAVAILABLE")
                page, total, pages = audience_page(
                    result.exchange, ctx.native_account_id, number, page_size
                )
                if total > 10000:
                    raise ApiError(
                        ErrorCode.DATA_COVERAGE_INSUFFICIENT, "人群来源过大，不能静默截断"
                    )

                def project(
                    session: Session,
                    permit: PagePermit = permit,
                    total: int = total,
                    pages: int = pages,
                    items: tuple[dict[str, Any], ...] = tuple(r.values for r in page.records),
                    page: SyncPage = page,
                ) -> None:
                    if self.scope(session, ctx, context).id != proof.id:
                        raise SyncConflict("LOOKUP_CONTEXT_CHANGED")
                    old = session.execute(
                        text(
                            "SELECT total_number,total_page FROM resource_lookup_pages "
                            "WHERE run_id=:run LIMIT 1"
                        ),
                        dict(run=run_id),
                    ).first()
                    if old is not None and tuple(old) != (total, pages):
                        raise SyncConflict("LOOKUP_PAGINATION_CHANGED")
                    seen = set(
                        session.execute(
                            text(
                                "SELECT i->>'audience_id' FROM resource_lookup_pages p "
                                "CROSS JOIN LATERAL jsonb_array_elements(p.items) i "
                                "WHERE p.run_id=:run"
                            ),
                            dict(run=run_id),
                        ).scalars()
                    )
                    if any(i["audience_id"] in seen for i in items):
                        raise SyncConflict("LOOKUP_DUPLICATE_RESOURCE")
                    session.execute(
                        text("""INSERT INTO resource_lookup_pages
                      (tenant_id,account_id,source_id,run_id,page_no,outbound_id,
                      proof_id,items,total_number,total_page,fetched_at)
                      VALUES(:tenant,:account,:source,:run,:page,:outbound,:proof,
                      CAST(:items AS jsonb),:total,:pages,:fetched)"""),
                        dict(
                            tenant=ctx.identity.tenant_id,
                            account=ctx.account_id,
                            source=permit.lease.source_id,
                            run=run_id,
                            page=permit.lease.page_no,
                            outbound=permit.outbound_id,
                            proof=proof.id,
                            items=json.dumps(items),
                            total=total,
                            pages=pages,
                            fetched=page.fetched_at,
                        ),
                    )

                lease = self.store.commit_page(ctx, spec, permit, page, project_facts=project)
            except (CallDeferred, ValueError, SyncConflict, ApiError, DBAPIError) as error:
                try:
                    self.store.stop(
                        ctx,
                        spec,
                        active,
                        "RATE_LIMITED" if isinstance(error, CallDeferred) else "PARTIAL",
                    )
                except (SyncConflict, ApiError):
                    pass
                raise
        return lease
