"""按显式私有交接件装配原生授权和只读适配器；未就绪资源保持禁用。"""

import base64
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import SecretBytes, SecretStr
from sqlalchemy import Engine, text

from yuliang.modules.connectors.archive import PlatformArchive, RecordedPlatformClient
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.governance import PlatformGovernance, QuotaLimits
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.native_auth import NativeAuthorization
from yuliang.modules.connectors.native_reads import (
    ReadOnlyNativeAdapter,
    decode_verdict,
    native_signer,
)
from yuliang.modules.connectors.oauth import OAuthRejected, OAuthService, require_admin
from yuliang.modules.connectors.registry import GatewayRegistry
from yuliang.modules.connectors.resources import ApplicationResource, ResourceBundle
from yuliang.modules.connectors.token_cipher import Ciphertext, TokenCipher, TokenContext
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.settings import Settings


class PlatformRuntime:
    def __init__(self, engine: Engine, settings: Settings, bundle: ResourceBundle) -> None:
        self.engine, self.bundle = engine, bundle
        self.oauth: OAuthService | None = None
        self.governance: PlatformGovernance | None = None
        self.registry = GatewayRegistry()
        self.ports = {
            item.id: NativeAuthorization(item) for item in bundle.applications if item.enabled
        }
        if not self.ports:
            return
        for port in self.ports.values():
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
        if port is None or port.resource.tenant_id != identity.tenant_id or self.oauth is None:
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
            row = (
                session.execute(
                    text("""SELECT * FROM provider_authorizations
                WHERE id=:id AND status='ACTIVE' AND expires_at>clock_timestamp()"""),
                    {"id": ctx.authorization_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise OAuthRejected()
            port = self.ports.get(row["application_id"])
            if port is None:
                raise OAuthRejected()
            item: ApplicationResource = port.resource
            if (
                ctx.identity.tenant_id,
                ctx.shop_id,
                ctx.account_id,
                ctx.native_account_id,
                ctx.connector_id,
                row["app_id"],
            ) != (
                item.tenant_id,
                item.shop_id,
                item.account_id,
                item.native_id,
                item.connector_id,
                item.app_id,
            ):
                raise OAuthRejected()
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
