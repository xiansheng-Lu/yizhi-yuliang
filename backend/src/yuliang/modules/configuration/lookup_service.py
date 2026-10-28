"""V9 LookupPage：从完整真实来源快照分页搜索，签名游标绑定用户与精确上下文。"""

import base64
import hashlib
import hmac
import json
from datetime import timedelta
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text

from yuliang.modules.configuration.models import ConfigurationContext
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.lookup_sync import AudienceLookupSync
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


class LookupService:
    def __init__(self, reader: AudienceLookupSync, key: SecretStr) -> None:
        if len(key.get_secret_value()) < 32:
            raise ValueError("资源游标签名需要独立密钥")
        self.reader, self.key = reader, key

    def _seal(self, data: dict[str, Any]) -> str:
        raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        signature = hmac.new(
            self.key.get_secret_value().encode(), encoded.encode(), hashlib.sha256
        ).hexdigest()
        return encoded + "." + signature

    def _open(self, value: str) -> dict[str, Any]:
        try:
            if len(value) > 16384:
                raise ValueError("large")
            encoded, signature = value.split(".")
            expected = hmac.new(
                self.key.get_secret_value().encode(), encoded.encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature")
            data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if not isinstance(data, dict):
                raise ValueError("format")
            return data
        except (ValueError, UnicodeError):
            raise ApiError(ErrorCode.INVALID_INPUT, "资源游标无效，请重新查询") from None

    def lookup(
        self,
        ctx: GatewayContext,
        context: ConfigurationContext,
        kind: str,
        *,
        q: str = "",
        cursor: str | None = None,
        category: str = "",
        parent_id: str = "",
        page_size: int = 50,
    ) -> dict[str, Any]:
        if kind != self.reader.kind or context.product != "adq":
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "该资源类别尚无正式只读来源实现")
        if category or parent_id:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "自定义资源来源未定义该分类或父级过滤")
        if (
            len(q) > 100
            or any(ord(c) < 32 for c in q)
            or type(page_size) is not int
            or not 1 <= page_size <= 100
        ):
            raise ApiError(ErrorCode.INVALID_INPUT, "资源查询范围非法")
        with business_transaction(self.reader.store.engine, ctx.identity) as session:
            proof = self.reader.scope(session, ctx, context)
            now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
            latest = session.execute(
                text("""SELECT r.id,r.state,r.started_at,
              (SELECT min(p.fetched_at) FROM resource_lookup_pages p WHERE p.run_id=r.id) AS fetched
              FROM sync_runs r JOIN sync_sources s ON (s.tenant_id,s.id)=(r.tenant_id,r.source_id)
              WHERE s.binding_id=:binding AND s.account_id=:account AND s.resource_kind=:resource
              ORDER BY r.started_at DESC,r.id DESC LIMIT 1"""),
                dict(
                    binding=ctx.binding_id,
                    account=ctx.account_id,
                    resource="lookup." + kind + "." + proof.id.hex,
                ),
            ).first()
            if latest is None:
                raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "资源来源尚未完成实际同步")
            if latest.state != "SUCCESS":
                raise ApiError(
                    ErrorCode.UPSTREAM_ERROR, "资源来源同步未完整成功，保留历史但禁止选择"
                )
            if (
                latest.fetched is None
                or latest.fetched + timedelta(seconds=proof.max_age_seconds) <= now
            ):
                raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "资源快照已过期，需要重新同步")
            identity = dict(
                tenant=ctx.identity.tenant_id,
                member=str(ctx.identity.membership_id),
                user=str(ctx.identity.user_id),
                context=hashlib.sha256(context.context_key.encode()).hexdigest(),
                proof=str(proof.id),
                q=q,
                kind=kind,
                size=page_size,
            )
            offset = 0
            if cursor:
                previous = self._open(cursor)
                if (
                    any(previous.get(k) != v for k, v in identity.items())
                    or previous.get("run") != str(latest.id)
                    or type(previous.get("offset")) is not int
                    or not 0 <= previous["offset"] <= 10000
                ):
                    raise ApiError(ErrorCode.VERSION_CONFLICT, "资源上下文或来源已改变，请重新查询")
                offset = previous["offset"]
            rows = (
                session.execute(
                    text(
                        "SELECT items FROM resource_lookup_pages WHERE run_id=:run ORDER BY page_no"
                    ),
                    dict(run=latest.id),
                )
                .scalars()
                .all()
            )
            items = []
            for page in rows:
                for item in page:
                    if (
                        q.casefold() not in item[self.reader.resource_name].casefold()
                        and q not in item[self.reader.resource_id]
                    ):
                        continue
                    items.append(self.reader.selected(item, proof))
            items.sort(key=lambda i: int(i["platformId"]))
            selected = items[offset : offset + page_size]
            next_cursor = (
                self._seal(identity | dict(run=str(latest.id), offset=offset + page_size))
                if offset + page_size < len(items)
                else None
            )
            return dict(
                contractVersion="advertising-v9.0",
                source="API",
                tenantId=str(ctx.identity.tenant_id),
                contextKey=context.context_key,
                kind=kind,
                items=selected,
                nextCursor=next_cursor,
                reason="完整授权资源来源快照；搜索仅过滤真实资源"
                if selected
                else "完整授权来源在当前筛选下无匹配资源",
            )

    def options(
        self,
        identity: VerifiedIdentity,
        context: ConfigurationContext,
        kind: str,
        *,
        request_id: UUID,
        q: str = "",
        cursor: str | None = None,
        category: str = "",
        parent_id: str = "",
    ) -> dict[str, Any]:
        from yuliang.modules.configuration.lookup_sources import lookup_source
        from yuliang.modules.configuration.service import ConfigurationService

        with business_transaction(self.reader.store.engine, identity) as session:
            if not ConfigurationService._allowed(session, context, "snapshot.read"):
                raise ApiError(ErrorCode.FORBIDDEN, "无当前资源上下文权限")
            proof = lookup_source(session, context, kind)
            if proof is None:
                raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "该上下文尚无正式资源来源")
            row = session.execute(
                text("""SELECT a.native_id,b.connector_id,b.provider,b.product_line,
              b.revision,b.catalog_id,b.catalog_sha256 FROM capability_bindings b JOIN ad_accounts a
              ON (a.tenant_id,a.id)=(b.tenant_id,b.account_id)
              WHERE b.id=:binding AND b.account_id=:account AND a.entity_id=:entity
              AND a.deleted_at IS NULL"""),
                dict(
                    binding=proof.binding_id,
                    account=context.account_id,
                    entity=context.business_entity_id,
                ),
            ).first()
            if row is None or not context.shop_id:
                raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "实际资源账户绑定不可用")
            ctx = GatewayContext(
                identity=identity,
                shop_id=UUID(context.shop_id),
                account_id=context.account_id,
                native_account_id=row.native_id,
                connector_id=row.connector_id,
                provider=row.provider,
                product_line=row.product_line,
                authorization_id=proof.authorization_id,
                request_id=request_id,
                binding_id=proof.binding_id,
                capability_version=row.revision,
                catalog_id=row.catalog_id,
                catalog_sha256=row.catalog_sha256,
                authorization_version=proof.authorization_version,
            )
        return self.lookup(
            ctx, context, kind, q=q, cursor=cursor, category=category, parent_id=parent_id
        )
