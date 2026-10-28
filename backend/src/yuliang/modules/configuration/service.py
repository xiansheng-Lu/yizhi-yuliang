"""只读V9目录/字段上下文；所有平台枚举来自精确上下文的维护依据。"""

import hashlib
import hmac
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from yuliang.modules.capability.models import Binding, Evidence, aggregate
from yuliang.modules.configuration.fields import initial_fields, unconfirmed
from yuliang.modules.configuration.models import (
    PRODUCT_CONTEXTS,
    ConfigurationAccount,
    ConfigurationCapability,
    ConfigurationCatalog,
    ConfigurationContext,
    ConfigurationEntity,
    ConfigurationShop,
    FieldCapability,
    FieldProof,
    FieldState,
    Product,
    Verification,
)
from yuliang.modules.connectors.redaction import canonical
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

ACCOUNT_CONTEXTS = PRODUCT_CONTEXTS | {
    "ocean_marketing": ("ocean", "ocean_marketing", ""),
    "dou_plus": ("ocean", "dou_plus", ""),
    "local_push": ("ocean", "local_push", ""),
}
VISIBLE_PAIRS = """SELECT DISTINCT a.id,a.name,a.entity_id,a.status,a.provider,a.product_line,
 e.name AS entity_name,s.id AS shop_id,s.name AS shop_name,s.entity_id AS shop_entity_id,
 se.name AS shop_entity_name,
 EXISTS(SELECT 1 FROM provider_authorizations p WHERE p.tenant_id=a.tenant_id
  AND p.provider=a.provider AND p.product_line=a.product_line AND p.status='ACTIVE'
  AND p.expires_at>clock_timestamp() AND cardinality(p.scopes)>0
  AND (p.account_id=a.id OR EXISTS(SELECT 1 FROM authorization_account_links l
   WHERE l.tenant_id=p.tenant_id AND l.authorization_id=p.id AND l.account_id=a.id))) AS connected
 FROM ad_accounts a JOIN business_entities e ON (e.tenant_id,e.id)=(a.tenant_id,a.entity_id)
 JOIN entity_links link ON (link.tenant_id,link.account_id)=(a.tenant_id,a.id)
 JOIN shops s ON (s.tenant_id,s.id)=(link.tenant_id,link.shop_id)
 JOIN business_entities se ON (se.tenant_id,se.id)=(s.tenant_id,s.entity_id)
 WHERE a.provider=:provider AND a.product_line=:line
 AND se.deleted_at IS NULL AND se.status='ACTIVE' AND (
  access.configuration_scope(a.id,s.id,a.entity_id,'snapshot.read')
  OR access.configuration_scope(a.id,s.id,a.entity_id,'plan.write'))"""


class ConfigurationService:
    def __init__(self, engine: Engine, identity: VerifiedIdentity, key: SecretStr) -> None:
        if len(key.get_secret_value()) < 32:
            raise ValueError("字段上下文签名需要服务端密钥")
        self.engine, self.identity, self.key = engine, identity, key

    @contextmanager
    def _snapshot(self) -> Iterator[Session]:
        engine = self.engine.execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=True
        )
        with business_transaction(engine, self.identity) as session:
            yield session

    def catalog(self, product: Product) -> ConfigurationCatalog:
        provider, line, _ = ACCOUNT_CONTEXTS[product]
        with self._snapshot() as session:
            rows = (
                session.execute(
                    text(VISIBLE_PAIRS + " ORDER BY a.id,s.id LIMIT 1001"),
                    {"provider": provider, "line": line},
                )
                .mappings()
                .all()
            )
            if len(rows) > 1000:
                raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "配置目录过大，需要先收窄范围")
        accounts: dict[UUID, ConfigurationAccount] = {}
        shops: dict[UUID, ConfigurationShop] = {}
        entities: dict[UUID, ConfigurationEntity] = {}
        for row in rows:
            previous = accounts.get(row["id"])
            ids = tuple(sorted(set((previous.shop_ids if previous else ()) + (row["shop_id"],))))
            accounts[row["id"]] = ConfigurationAccount(
                id=row["id"],
                name=row["name"],
                business_entity_id=row["entity_id"],
                shop_ids=ids,
                authorization="AUTHORIZED"
                if row["connected"] and row["status"] == "AUTHORIZED"
                else "UNAUTHORIZED",
                writable=False,
            )
            shops[row["shop_id"]] = ConfigurationShop(
                id=row["shop_id"], name=row["shop_name"], business_entity_id=row["shop_entity_id"]
            )
            for identifier, name in (
                (row["entity_id"], row["entity_name"]),
                (row["shop_entity_id"], row["shop_entity_name"]),
            ):
                entities[identifier] = ConfigurationEntity(id=identifier, name=name)
        return ConfigurationCatalog(
            tenant_id=str(self.identity.tenant_id),
            product=product,
            accounts=tuple(accounts.values()),
            shops=tuple(shops.values()),
            entities=tuple(entities.values()),
            reason="当前范围的实际主体/账户/店铺；创建执行通道仍未开放"
            if rows
            else "当前产品尚无已核对且可见的主体/账户/店铺关联",
        )

    @staticmethod
    def _allowed(session: Session, context: ConfigurationContext, permission: str) -> bool:
        return bool(
            session.execute(
                text("SELECT access.configuration_scope(:account,:shop,:entity,:permission)"),
                {
                    "account": context.account_id,
                    "shop": UUID(context.shop_id) if context.shop_id else None,
                    "entity": context.business_entity_id,
                    "permission": permission,
                },
            ).scalar_one()
        )

    def _available(
        self,
        session: Session,
        context: ConfigurationContext,
        proof: FieldProof,
        head: str | None,
        now: datetime,
        connected: bool,
    ) -> tuple[FieldCapability, datetime | None]:
        row = (
            session.execute(
                text("SELECT * FROM capability_bindings WHERE id=:id AND active"),
                {"id": proof.binding_id},
            )
            .mappings()
            .first()
        )
        descriptor = ACCOUNT_CONTEXTS[context.product]
        if row is None or (
            row["account_id"],
            row["provider"],
            row["product_line"],
            row["connector_id"],
        ) != (context.account_id, *descriptor):
            return unconfirmed(proof.field_id, "字段来源的产品/账户绑定不再有效"), None
        grant = (
            session.execute(
                text("""SELECT p.* FROM provider_authorizations p WHERE p.id=:id
            AND p.provider=:provider AND p.product_line=:line AND (p.account_id=:account
            OR EXISTS(SELECT 1 FROM authorization_account_links l WHERE l.tenant_id=p.tenant_id
              AND l.authorization_id=p.id AND l.account_id=:account))"""),
                {
                    "id": proof.authorization_id,
                    "provider": descriptor[0],
                    "line": descriptor[1],
                    "account": context.account_id,
                },
            )
            .mappings()
            .first()
        )
        if (
            not connected
            or grant is None
            or grant["status"] != "ACTIVE"
            or grant["expires_at"] <= now + timedelta(seconds=2)
            or grant["version"] != proof.authorization_version
            or not set(proof.required_scopes) <= set(grant["scopes"])
        ):
            return FieldCapability(
                state="AUTH_REQUIRED",
                value_type=proof.capability.value_type,
                reason="字段来源授权失效、scope或授权版本已改变",
            ), None
        binding = Binding.model_validate({key: row[key] for key in Binding.model_fields})
        if binding.revision != proof.binding_version:
            return unconfirmed(proof.field_id, "字段来源能力版本已改变，需要重新核对"), None
        rows = (
            session.execute(
                text("SELECT * FROM capability_assertions WHERE binding_id=:id"), {"id": binding.id}
            )
            .mappings()
            .all()
        )
        evidence = [
            Evidence.model_validate({key: r[key] for key in Evidence.model_fields}) for r in rows
        ]
        state = aggregate(
            binding,
            evidence,
            now=now,
            permitted=self._allowed(session, context, binding.permission_code),
            account_authorized=True,
        )
        if binding.catalog_sha256 != head or state["status"] != "AUTHORIZED":
            field_state: FieldState = (
                "AUTH_REQUIRED"
                if state["status"] in {"FORBIDDEN", "UNAUTHORIZED"}
                else "API_PENDING"
            )
            return FieldCapability(
                state=field_state,
                reason="字段来源能力需要重新核对：" + state["reason"],
                value_type=proof.capability.value_type,
            ), None
        latest: dict[str, Evidence] = {}
        for item in evidence:
            previous = latest.get(item.phase)
            if previous is None or (item.checked_at, item.sequence) > (
                previous.checked_at,
                previous.sequence,
            ):
                latest[item.phase] = item
        expires = min(item.expires_at for item in latest.values())
        if expires <= now + timedelta(seconds=2):
            return unconfirmed(proof.field_id, "字段来源证据即将过期，需要重新核对"), None
        return proof.capability, min(expires, proof.expires_at, grant["expires_at"])

    def capabilities(self, context: ConfigurationContext) -> ConfigurationCapability:
        provider, line, _ = ACCOUNT_CONTEXTS[context.product]
        with self._snapshot() as session:
            pairs = (
                session.execute(
                    text(
                        VISIBLE_PAIRS + " AND a.id=:account AND a.entity_id=:entity "
                        "AND (CAST(:shop AS uuid) IS NULL OR s.id=:shop) ORDER BY s.id"
                    ),
                    {
                        "provider": provider,
                        "line": line,
                        "account": context.account_id,
                        "entity": context.business_entity_id,
                        "shop": UUID(context.shop_id) if context.shop_id else None,
                    },
                )
                .mappings()
                .all()
            )
            if not pairs:
                raise ApiError(ErrorCode.NO_DATA, "没有当前产品/主体/店铺范围内的账户")
            now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
            head = session.execute(
                text("SELECT catalog_sha256 FROM catalog_head WHERE singleton")
            ).scalar_one_or_none()
            digest = hashlib.sha256(context.context_key.encode()).hexdigest()
            records = (
                session.execute(
                    text("""SELECT DISTINCT ON(field_id) * FROM configuration_field_assertions
                WHERE account_id=:account AND context_digest=:digest AND context_key=:context
                ORDER BY field_id,revision DESC"""),
                    {
                        "account": context.account_id,
                        "digest": digest,
                        "context": context.context_key,
                    },
                )
                .mappings()
                .all()
            )
            fields = initial_fields()
            connected = bool(pairs[0]["connected"] and pairs[0]["status"] == "AUTHORIZED")
            expires = now + timedelta(seconds=60)
            proof_ids = []
            for row in records:
                try:
                    proof = FieldProof.model_validate(row["payload"])
                    if (
                        proof.context.context_key != context.context_key
                        or proof.field_id != row["field_id"]
                    ):
                        raise ValueError("字段依据上下文已改变")
                except ValueError:
                    # 任意损坏依据不升级字段，也不向客户端回显原始内容。
                    continue
                proof_ids.append(str(proof.id))
                if not proof.checked_at <= now < proof.expires_at - timedelta(seconds=2):
                    fields[proof.field_id] = unconfirmed(proof.field_id, "字段依据过期或时间不可信")
                    continue
                value, deadline = (
                    self._available(session, context, proof, head, now, connected)
                    if proof.capability.state == "AVAILABLE"
                    else (proof.capability, proof.expires_at)
                )
                fields[proof.field_id] = value
                if deadline is not None:
                    expires = min(expires, deadline)
            writable = self._allowed(session, context, "plan.write")
            connected = bool(pairs[0]["connected"] and pairs[0]["status"] == "AUTHORIZED")
            material = session.execute(
                text("""SELECT jsonb_build_object('authz',m.authz_version,
                'scopes',(SELECT jsonb_agg(to_jsonb(ds) ORDER BY ds.id) FROM data_scopes ds
                 WHERE ds.membership_id=m.id),
                'permissions',(SELECT jsonb_agg(p.permission_code ORDER BY p.permission_code)
                 FROM role_grants g JOIN role_permissions p
                 ON (p.tenant_id,p.role_id)=(g.tenant_id,g.role_id)
                 WHERE g.membership_id=m.id)) FROM memberships m WHERE m.id=:member"""),
                {"member": self.identity.membership_id},
            ).scalar_one()
            signature = hmac.new(
                self.key.get_secret_value().encode(),
                canonical(
                    {
                        "kind": "configuration-v9",
                        "tenant": self.identity.tenant_id,
                        "actor": str(self.identity.user_id),
                        "context": context.context_key,
                        "head": head,
                        "fields": {
                            key: value.model_dump(mode="json", by_alias=True, exclude_none=True)
                            for key, value in fields.items()
                        },
                        "proofs": proof_ids,
                        "scope": material,
                        "connected": connected,
                    }
                ),
                hashlib.sha256,
            ).hexdigest()
            # 创建执行器归后续M3-13。字段可查询不是已具备创建/上传/预估通道。
            return ConfigurationCapability(
                tenant_id=str(self.identity.tenant_id),
                context_key=context.context_key,
                version=f"cfg1.{signature}",
                checked_at=now,
                expires_at=expires,
                fields=fields,
                authorization="FORBIDDEN"
                if not writable
                else "UNAUTHORIZED"
                if not connected
                else "NOT_INTEGRATED",
                write_channel="NONE",
                write_permission="plan.write",
                audience_management="PLATFORM_SMART" if context.product == "shop" else "MANUAL",
                estimate_supported=False,
                upload_supported=False,
                currency="BEAN" if context.product == "beans" else "CNY",
                reason="字段按完整配置上下文核对；创建执行通道尚未交付",
                api_version="UNCONFIRMED",
                verification=Verification(
                    catalog_ids=(),
                    path_verified=False,
                    application_scope_granted=False,
                    account_authorized=False,
                    backend_integrated=False,
                    request_schema_verified=False,
                    execution_verified=False,
                    proof_id="",
                    checked_at=now,
                ),
            )
