"""按显式私有交接件装配原生授权和只读适配器；未就绪资源保持禁用。"""

import base64
import hashlib
import hmac
import json
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import SecretBytes, SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from yuliang.modules.configuration.lookup_service import LookupService
from yuliang.modules.configuration.models import ConfigurationContext
from yuliang.modules.connectors.adq_assets import MIRROR_POLICY, AdqAssetSync, AssetKind
from yuliang.modules.connectors.adq_finance import AdqFinanceSync
from yuliang.modules.connectors.adq_finance_protocol import (
    FINANCE_ARCHIVE_POLICY,
    FINANCE_MIRROR_POLICY,
    OperationQuery,
)
from yuliang.modules.connectors.adq_report_protocol import (
    REPORT_ARCHIVE_POLICY,
    REPORT_MIRROR_POLICY,
    ReportQuery,
)
from yuliang.modules.connectors.adq_reports import AdqReportSync
from yuliang.modules.connectors.archive import PlatformArchive, RecordedPlatformClient
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.external_tokens import ExternalTokenConfig, ExternalTokenProvider
from yuliang.modules.connectors.governance import PlatformGovernance, QuotaLimits
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.lookup_protocol import (
    LOOKUP_ARCHIVE,
    LOOKUP_POLICY,
    TARGET_ARCHIVE,
    TARGET_POLICY,
)
from yuliang.modules.connectors.lookup_sync import AudienceLookupSync, TargetLookupSync
from yuliang.modules.connectors.native_auth import NativeAuthorization
from yuliang.modules.connectors.native_reads import (
    ReadOnlyNativeAdapter,
    decode_verdict,
    native_signer,
)
from yuliang.modules.connectors.oauth import OAuthRejected, OAuthService, require_admin
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.connectors.registry import GatewayRegistry
from yuliang.modules.connectors.resources import ApplicationResource, ResourceBundle
from yuliang.modules.connectors.shop_compass_protocol import COMPASS_ARCHIVE_POLICY, COMPASS_POLICY
from yuliang.modules.connectors.shop_compass_sync import ShopCompassSync
from yuliang.modules.connectors.shop_detail_protocol import (
    DETAIL_ARCHIVE_POLICY,
    DETAIL_MIRROR_POLICY,
)
from yuliang.modules.connectors.shop_order_sync import ShopOrderSync
from yuliang.modules.connectors.shop_orders import ORDER_ARCHIVE_POLICY, ORDER_POLICY, Kind
from yuliang.modules.connectors.shop_product_details import ShopProductDetailSync
from yuliang.modules.connectors.shop_product_sync import ShopProductIdSync
from yuliang.modules.connectors.shop_products import PRODUCT_ID_POLICY
from yuliang.modules.connectors.sync_cursor import CursorCodec
from yuliang.modules.connectors.sync_store import SyncStore
from yuliang.modules.connectors.token_cipher import Ciphertext, TokenCipher, TokenContext
from yuliang.modules.reporting.service import FactWriter
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.settings import Settings


class PlatformRuntime:
    def __init__(self, engine: Engine, settings: Settings, bundle: ResourceBundle) -> None:
        self.engine, self.bundle = engine, bundle
        self.oauth: OAuthService | None = None
        self.governance: PlatformGovernance | None = None
        self.lookup_key: SecretStr | None = None
        self.registry = GatewayRegistry()
        self.adq_assets: dict[UUID, AdqAssetSync] = {}
        self.adq_finance: dict[UUID, AdqFinanceSync] = {}
        self.adq_reports: dict[UUID, AdqReportSync] = {}
        self.shop_product_ids: dict[UUID, ShopProductIdSync] = {}
        self.shop_details: dict[UUID, ShopProductDetailSync] = {}
        self.shop_compass: dict[UUID, ShopCompassSync] = {}
        self.shop_orders: dict[UUID, ShopOrderSync] = {}
        self.audience_lookups: dict[UUID, AudienceLookupSync] = {}
        self.target_lookups: dict[UUID, TargetLookupSync] = {}
        self.ports = {
            item.id: NativeAuthorization(item) for item in bundle.applications if item.enabled
        }
        if not self.ports:
            return
        for port in self.ports.values():
            if port.resource.external_token_service is None:
                port.application()
        if bundle.keyring_file is None or bundle.state_secret_file is None:
            raise ValueError("启用平台接入需要独立令牌密钥环和state密钥")
        ring = json.loads(bundle.keyring_file.read_text(encoding="utf-8"))
        keys = {
            int(version): SecretBytes(base64.b64decode(key, validate=True))
            for version, key in ring.items()
        }
        state_key = SecretStr(bundle.state_secret_file.read_text(encoding="utf-8").strip())
        if len(state_key.get_secret_value()) < 32 or state_key in (
            settings.session_secret,
            settings.auth_audit_secret,
        ):
            raise ValueError("平台state密钥须独立且至少32字符")
        self.oauth = OAuthService(engine, state_key, TokenCipher(keys, bundle.key_version))
        self.lookup_key = SecretStr(
            hmac.new(
                state_key.get_secret_value().encode(),
                b"yuliang-resource-lookup-cursor-v1",
                hashlib.sha256,
            ).hexdigest()
        )
        if settings.auth_audit_secret is None:
            raise ValueError("平台存档需要独立审计密钥")
        self.governance = PlatformGovernance.from_url(
            settings.redis_url,
            QuotaLimits(
                app_limit=1,
                account_limit=1,
                app_execute_reserve=0,
                app_readback_reserve=0,
                account_execute_reserve=0,
                account_readback_reserve=0,
                window_ms=1000,
            ),
        )
        # 注册表按provider/product索引，一个产品配置多个应用需单独路由，不隐式取首个。
        enabled_connectors = [port.resource.connector_id for port in self.ports.values()]
        if len(set(enabled_connectors)) != len(enabled_connectors):
            self.close()
            raise ValueError("当前装配每个产品线只支持一个显式试点应用")
        audit_secret = settings.auth_audit_secret
        governance = self.governance
        for port in self.ports.values():
            item = port.resource
            client = GovernedPlatformClient(
                RecordedPlatformClient(
                    PlatformHttpClient(native_signer),
                    PlatformArchive(engine, settings.auth_audit_secret),
                    decoder=decode_verdict,
                ),
                self.governance,
                item.id,
            )
            self.registry.register(
                item.provider,
                item.connector_id,
                ReadOnlyNativeAdapter(
                    client, self.token_for_read, item.connector_id, item.readonly_interfaces
                ),
            )
            if item.connector_id == "adq" and item.granted_scopes:
                self.adq_assets[item.id] = AdqAssetSync(
                    SyncStore(
                        engine,
                        CursorCodec(keys, bundle.key_version, settings.auth_audit_secret),
                        policy=MIRROR_POLICY,
                    ),
                    client,
                    self.token_for_read,
                    item.readonly_interfaces,
                    item.granted_scopes or (),
                )
                finance_client = GovernedPlatformClient(
                    RecordedPlatformClient(
                        PlatformHttpClient(native_signer),
                        PlatformArchive(engine, settings.auth_audit_secret),
                        policy=FINANCE_ARCHIVE_POLICY,
                        decoder=decode_verdict,
                    ),
                    self.governance,
                    item.id,
                )
                self.adq_finance[item.id] = AdqFinanceSync(
                    SyncStore(
                        engine,
                        CursorCodec(keys, bundle.key_version, settings.auth_audit_secret),
                        policy=FINANCE_MIRROR_POLICY,
                    ),
                    finance_client,
                    self.token_for_read,
                    item.readonly_interfaces,
                    item.granted_scopes,
                )
                report_client = GovernedPlatformClient(
                    RecordedPlatformClient(
                        PlatformHttpClient(native_signer),
                        PlatformArchive(engine, settings.auth_audit_secret),
                        policy=REPORT_ARCHIVE_POLICY,
                        decoder=decode_verdict,
                    ),
                    self.governance,
                    item.id,
                )
                self.adq_reports[item.id] = AdqReportSync(
                    SyncStore(
                        engine,
                        CursorCodec(keys, bundle.key_version, settings.auth_audit_secret),
                        policy=REPORT_MIRROR_POLICY,
                    ),
                    report_client,
                    self.token_for_read,
                    FactWriter(engine, settings.auth_audit_secret),
                    item.readonly_interfaces,
                    item.granted_scopes,
                )
                self.audience_lookups[item.id] = AudienceLookupSync(
                    SyncStore(
                        engine,
                        CursorCodec(keys, bundle.key_version, audit_secret),
                        policy=LOOKUP_POLICY,
                    ),
                    GovernedPlatformClient(
                        RecordedPlatformClient(
                            PlatformHttpClient(native_signer),
                            PlatformArchive(engine, audit_secret),
                            policy=LOOKUP_ARCHIVE,
                            decoder=decode_verdict,
                        ),
                        governance,
                        item.id,
                    ),
                    self.token_for_read,
                    item.readonly_interfaces,
                )
                self.target_lookups[item.id] = TargetLookupSync(
                    SyncStore(
                        engine,
                        CursorCodec(keys, bundle.key_version, audit_secret),
                        policy=TARGET_POLICY,
                    ),
                    GovernedPlatformClient(
                        RecordedPlatformClient(
                            PlatformHttpClient(native_signer),
                            PlatformArchive(engine, audit_secret),
                            policy=TARGET_ARCHIVE,
                            decoder=decode_verdict,
                        ),
                        governance,
                        item.id,
                    ),
                    self.token_for_read,
                    item.readonly_interfaces,
                )

            if item.connector_id == "wxshop" and item.granted_scopes:
                product_client = GovernedPlatformClient(
                    RecordedPlatformClient(
                        PlatformHttpClient(native_signer),
                        PlatformArchive(engine, settings.auth_audit_secret),
                        policy=ArchivePolicy(
                            fields=frozenset({"errcode", "product_ids", "next_key"})
                        ),
                        decoder=decode_verdict,
                    ),
                    self.governance,
                    item.id,
                )
                self.shop_product_ids[item.id] = ShopProductIdSync(
                    SyncStore(
                        engine,
                        CursorCodec(keys, bundle.key_version, settings.auth_audit_secret),
                        policy=PRODUCT_ID_POLICY,
                    ),
                    product_client,
                    self.token_for_read,
                    item.readonly_interfaces,
                    item.granted_scopes,
                )

                def shop_client(
                    policy: ArchivePolicy, application_id: UUID = item.id
                ) -> GovernedPlatformClient:
                    return GovernedPlatformClient(
                        RecordedPlatformClient(
                            PlatformHttpClient(native_signer),
                            PlatformArchive(engine, audit_secret),
                            policy=policy,
                            decoder=decode_verdict,
                        ),
                        governance,
                        application_id,
                    )

                def shop_store(policy: ArchivePolicy) -> SyncStore:
                    return SyncStore(
                        engine,
                        CursorCodec(keys, bundle.key_version, audit_secret),
                        policy=policy,
                    )

                self.shop_details[item.id] = ShopProductDetailSync(
                    shop_store(DETAIL_MIRROR_POLICY),
                    shop_client(DETAIL_ARCHIVE_POLICY),
                    self.token_for_read,
                    item.readonly_interfaces,
                    item.granted_scopes,
                )
                self.shop_compass[item.id] = ShopCompassSync(
                    shop_store(COMPASS_POLICY),
                    shop_client(COMPASS_ARCHIVE_POLICY),
                    self.token_for_read,
                    item.readonly_interfaces,
                    item.granted_scopes,
                    FactWriter(engine, settings.auth_audit_secret),
                )
                self.shop_orders[item.id] = ShopOrderSync(
                    shop_store(ORDER_POLICY),
                    shop_client(ORDER_ARCHIVE_POLICY),
                    self.token_for_read,
                    item.readonly_interfaces,
                    item.granted_scopes,
                )

    @classmethod
    def load(cls, engine: Engine, settings: Settings, path: Path) -> "PlatformRuntime":
        return cls(engine, settings, ResourceBundle.model_validate_json(path.read_bytes()))

    def close(self) -> None:
        if self.governance is not None:
            self.governance.client.close()

    def require(
        self, identity: VerifiedIdentity, session_id: UUID, app_id: UUID
    ) -> NativeAuthorization:
        with business_transaction(self.engine, identity) as session:
            require_admin(session, identity, session_id)
        port = self.ports.get(app_id)
        if (
            port is None
            or port.resource.tenant_id != identity.tenant_id
            or self.oauth is None
            or port.resource.external_token_service is not None
        ):
            raise OAuthRejected()
        return port

    def listing(self, identity: VerifiedIdentity, session_id: UUID) -> dict[str, Any]:
        with business_transaction(self.engine, identity) as session:
            require_admin(session, identity, session_id)
        return {
            "applications": [
                {
                    "id": str(item.id),
                    "connectorId": item.connector_id,
                    "mode": item.mode,
                    "enabled": item.enabled,
                    "shopId": str(item.shop_id) if item.shop_id else None,
                    "readonlyInterfaceCount": len(item.readonly_interfaces),
                }
                for item in self.bundle.applications
                if item.tenant_id == identity.tenant_id
            ]
        }

    async def token_for_read(self, ctx: GatewayContext) -> SecretStr:
        if self.oauth is None:
            raise OAuthRejected()
        with business_transaction(self.engine, ctx.identity) as session:
            item, row = self._read_resource(session, ctx)
        reference = item.external_token_service
        if reference is not None:
            provider = ExternalTokenProvider(
                ExternalTokenConfig(
                    base_url=reference.base_url,
                    api_key=reference.api_key.resolve(),
                    product=item.connector_id,
                    account_id=item.native_id if item.connector_id == "adq" else None,
                )
            )
            token = await provider.get_token()
            # 托管服务可能按需刷新；等待期间本地撤权/版本改变仍必须拒绝旧请求。
            with business_transaction(self.engine, ctx.identity) as session:
                _, current = self._read_resource(session, ctx)
                if current["version"] != row["version"]:
                    raise OAuthRejected()
            return token.access_token
        return self.oauth.cipher.open(
            TokenContext(
                row["tenant_id"],
                row["id"],
                row["application_id"],
                row["app_id"],
                row["connector_id"],
                row["provider"],
                row["product_line"],
            ),
            "access",
            Ciphertext(row["key_version"], row["access_token_enc"]),
        )

    def _read_resource(
        self, session: Session, ctx: GatewayContext
    ) -> tuple[ApplicationResource, dict[str, Any]]:
        found = (
            session.execute(
                text("""SELECT * FROM provider_authorizations
            WHERE id=:id AND status='ACTIVE' AND expires_at>clock_timestamp()"""),
                {"id": ctx.authorization_id},
            )
            .mappings()
            .first()
        )
        if found is None:
            raise OAuthRejected()
        row = dict(found)
        port = self.ports.get(row["application_id"])
        if port is None:
            raise OAuthRejected()
        item = port.resource
        if (
            ctx.identity.tenant_id,
            ctx.shop_id,
            ctx.account_id,
            ctx.native_account_id,
            ctx.connector_id,
            ctx.provider,
            ctx.product_line,
            row["app_id"],
        ) != (
            item.tenant_id,
            item.shop_id,
            item.account_id,
            item.native_id,
            item.connector_id,
            item.provider,
            item.connector_id,
            item.app_id,
        ) or (row["tenant_id"], row["connector_id"], row["provider"], row["product_line"]) != (
            item.tenant_id,
            item.connector_id,
            item.provider,
            item.connector_id,
        ):
            raise OAuthRejected()
        if ctx.authorization_version is not None and ctx.authorization_version != row["version"]:
            raise OAuthRejected()
        return item, row

    async def sync_shop_product_ids(self, ctx: GatewayContext, run_id: UUID) -> dict[str, Any]:
        await self.token_for_read(ctx)
        with business_transaction(self.engine, ctx.identity) as session:
            app_id = session.execute(
                text("SELECT application_id FROM provider_authorizations WHERE id=:id"),
                {"id": ctx.authorization_id},
            ).scalar_one()
        reader = self.shop_product_ids.get(app_id)
        if reader is None:
            raise OAuthRejected()
        return await reader.sync(ctx, run_id)

    def _application_for_read(self, ctx: GatewayContext) -> UUID:
        with business_transaction(self.engine, ctx.identity) as session:
            item, _ = self._read_resource(session, ctx)
            return item.id

    def lookup_options(
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
        from yuliang.platform.errors import ApiError, ErrorCode

        if kind not in {"audiences", "targets"}:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "该资源类别尚无正式只读来源实现")
        readers: dict[UUID, AudienceLookupSync] = (
            self.audience_lookups if kind == "audiences" else dict(self.target_lookups)
        )
        candidates = [
            readers[item.id]
            for item in self.bundle.applications
            if item.enabled
            and item.tenant_id == identity.tenant_id
            and item.connector_id == context.product
            and item.account_id == context.account_id
            and str(item.shop_id) == context.shop_id
            and item.id in readers
        ]
        if len(candidates) != 1 or self.lookup_key is None:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "该资源范围尚未接入")
        return LookupService(candidates[0], self.lookup_key).options(
            identity,
            context,
            kind,
            request_id=request_id,
            q=q,
            cursor=cursor,
            category=category,
            parent_id=parent_id,
        )

    async def sync_lookup_audiences(
        self,
        ctx: GatewayContext,
        context: ConfigurationContext,
        run_id: UUID,
        *,
        page_size: int = 100,
    ) -> dict[str, Any]:
        reader = self.audience_lookups.get(self._application_for_read(ctx))
        if reader is None:
            raise OAuthRejected()
        return await reader.sync(ctx, context, run_id, page_size=page_size)

    async def sync_lookup_targets(
        self,
        ctx: GatewayContext,
        context: ConfigurationContext,
        run_id: UUID,
        *,
        page_size: int = 100,
    ) -> dict[str, Any]:
        reader = self.target_lookups.get(self._application_for_read(ctx))
        if reader is None:
            raise OAuthRejected()
        return await reader.sync(ctx, context, run_id, page_size=page_size)

    async def sync_shop_detail(
        self, ctx: GatewayContext, run_id: UUID, product_id: str
    ) -> dict[str, Any]:
        reader = self.shop_details.get(self._application_for_read(ctx))
        if reader is None:
            raise OAuthRejected()
        return await reader.sync(ctx, run_id, product_id)

    async def sync_shop_compass(
        self, ctx: GatewayContext, run_id: UUID, *, day: date
    ) -> dict[str, Any]:
        reader = self.shop_compass.get(self._application_for_read(ctx))
        if reader is None:
            raise OAuthRejected()
        return await reader.sync(ctx, run_id, day=day)

    async def sync_shop_orders(
        self,
        ctx: GatewayContext,
        run_id: UUID,
        *,
        kind: Kind,
        order_id: str | None = None,
        parameters_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        reader = self.shop_orders.get(self._application_for_read(ctx))
        if reader is None:
            raise OAuthRejected()
        return await reader.sync(
            ctx, run_id, kind=kind, order_id=order_id, parameters_input=parameters_input
        )

    async def sync_adq_assets(
        self, ctx: GatewayContext, kind: AssetKind, run_id: UUID
    ) -> dict[str, Any]:
        # 受控服务端入口；不接受URL、账户身份、路由或任意query覆盖。
        await self.token_for_read(ctx)
        with business_transaction(self.engine, ctx.identity) as session:
            app_id = session.execute(
                text("SELECT application_id FROM provider_authorizations WHERE id=:id"),
                {"id": ctx.authorization_id},
            ).scalar_one()
        reader = self.adq_assets.get(app_id)
        if reader is None:
            raise OAuthRejected()
        return await reader.sync(ctx, kind, run_id)

    async def sync_adq_finance(
        self, ctx: GatewayContext, run_id: UUID, query: OperationQuery | None = None
    ) -> dict[str, Any]:
        await self.token_for_read(ctx)
        with business_transaction(self.engine, ctx.identity) as session:
            app_id = session.execute(
                text("SELECT application_id FROM provider_authorizations WHERE id=:id"),
                {"id": ctx.authorization_id},
            ).scalar_one()
        reader = self.adq_finance.get(app_id)
        if reader is None:
            raise OAuthRejected()
        return (
            await reader.sync_funds(ctx, run_id)
            if query is None
            else await reader.sync_operations(ctx, run_id, query)
        )

    async def sync_adq_reports(
        self,
        ctx: GatewayContext,
        batch_id: UUID,
        query: ReportQuery,
    ) -> dict[str, Any]:
        await self.token_for_read(ctx)
        with business_transaction(self.engine, ctx.identity) as session:
            app_id = session.execute(
                text("SELECT application_id FROM provider_authorizations WHERE id=:id"),
                dict(id=ctx.authorization_id),
            ).scalar_one()
        reader = self.adq_reports.get(app_id)
        if reader is None:
            raise OAuthRejected()
        return await reader.sync(ctx, batch_id, query)
