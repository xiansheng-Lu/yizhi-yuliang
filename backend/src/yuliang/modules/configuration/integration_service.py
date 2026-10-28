"""当前授权范围的资源上下文；关系、平台授权与资源入口就绪分别核对。"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from yuliang.modules.capability.models import Binding, Evidence, Phase, aggregate
from yuliang.modules.capability.route_sources import current_source
from yuliang.modules.configuration.integration_models import (
    IntegrationAccount,
    IntegrationContext,
    IntegrationEntity,
    IntegrationLink,
    IntegrationOperation,
    IntegrationShop,
    OperationDeclaration,
    ProductLine,
    StageEvidence,
    VerificationStage,
)
from yuliang.modules.configuration.service import ACCOUNT_CONTEXTS
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

DESCRIPTORS = ACCOUNT_CONTEXTS | {"wxshop": ("wxshop", "wxshop", "wxshop")}
PHASES: dict[VerificationStage, Phase] = {
    "path": Phase.PATH,
    "applicationScope": Phase.SCOPE,
    "account": Phase.ACCOUNT,
    "backend": Phase.BACKEND,
    "requestSchema": Phase.SCHEMA,
    "execution": Phase.EXECUTION,
}
PAIRS = """SELECT a.id,a.native_id,a.name,a.entity_id,a.status,
 e.name AS entity_name,s.id AS shop_id,s.name AS shop_name,s.entity_id AS shop_entity_id,
 s.platform AS shop_provider,se.name AS shop_entity_name,link.created_at AS linked_at,
 (SELECT max(p.expires_at) FROM provider_authorizations p WHERE p.tenant_id=a.tenant_id
  AND p.provider=a.provider AND p.product_line=a.product_line AND p.status='ACTIVE'
  AND p.expires_at>clock_timestamp() AND cardinality(p.scopes)>0
  AND EXISTS(SELECT 1 FROM authorization_account_links al
   WHERE al.tenant_id=p.tenant_id AND al.authorization_id=p.id
    AND al.account_id=a.id AND al.native_id_snapshot=a.native_id)) AS connected_until
 FROM ad_accounts a JOIN business_entities e ON (e.tenant_id,e.id)=(a.tenant_id,a.entity_id)
 JOIN entity_links link ON (link.tenant_id,link.account_id)=(a.tenant_id,a.id)
 JOIN shops s ON (s.tenant_id,s.id)=(link.tenant_id,link.shop_id)
 JOIN business_entities se ON (se.tenant_id,se.id)=(s.tenant_id,s.entity_id)
 WHERE a.provider=:provider AND a.product_line=:line
 AND se.deleted_at IS NULL AND se.status='ACTIVE'
 AND (access.configuration_scope(a.id,s.id,a.entity_id,'snapshot.read')
  OR access.configuration_scope(a.id,s.id,a.entity_id,'plan.write'))
 ORDER BY a.id,s.id LIMIT 1001"""


class IntegrationService:
    def __init__(self, engine: Engine, identity: VerifiedIdentity) -> None:
        self.engine, self.identity = engine, identity

    def _operation(
        self,
        session: Session,
        row: Any,
        account: IntegrationAccount,
        line: ProductLine,
        head: str | None,
        now: datetime,
        shop_ids: list[UUID],
    ) -> tuple[IntegrationOperation | None, datetime | None]:
        try:
            proof = OperationDeclaration.model_validate(row["payload"])
        except ValueError:
            return None, None
        if (
            proof.id != row["id"]
            or proof.binding_id != row["binding_id"]
            or proof.resource != row["resource"]
        ):
            return None, None
        stored = (
            session.execute(
                text("SELECT * FROM capability_bindings WHERE id=:id AND active"),
                {"id": proof.binding_id},
            )
            .mappings()
            .first()
        )
        if stored is None:
            return None, None
        binding = Binding.model_validate({key: stored[key] for key in Binding.model_fields})
        if (binding.account_id, binding.provider, binding.product_line, binding.connector_id) != (
            account.id,
            *DESCRIPTORS[line],
        ):
            return None, None
        source = session.execute(
            text(
                "SELECT normalized_path,http_method,record_json FROM endpoint_catalog "
                "WHERE catalog_sha256=:sha AND catalog_id=:id AND provider=:provider"
            ),
            {"sha": binding.catalog_sha256, "id": binding.catalog_id, "provider": binding.provider},
        ).first()
        if source is not None and source.record_json.get("frontendExposed") is False:
            return None, None
        evidence: dict[VerificationStage, StageEvidence] = {
            "directory": StageEvidence(
                verified=source is not None,
                proof_id=f"catalog/{head}/{binding.catalog_id}" if source is not None else None,
                checked_at=now if source is not None else None,
                reason="当前目录行存在；不等于路径、授权或入口就绪"
                if source is not None
                else "当前目录无此记录",
            )
        }
        items = (
            session.execute(
                text("SELECT * FROM capability_assertions WHERE binding_id=:id"), {"id": binding.id}
            )
            .mappings()
            .all()
        )
        assertions = [
            Evidence.model_validate({k: item[k] for k in Evidence.model_fields}) for item in items
        ]
        latest: dict[Phase, Evidence] = {}
        for item in assertions:
            previous = latest.get(item.phase)
            if previous is None or (item.checked_at, item.sequence) > (
                previous.checked_at,
                previous.sequence,
            ):
                latest[item.phase] = item
        allowed = bool(
            session.execute(
                text(
                    "SELECT bool_and(access.configuration_scope(:account,s,:entity,:permission)) "
                    "FROM unnest(CAST(:shops AS uuid[])) s"
                ),
                {
                    "shops": shop_ids,
                    "account": account.id,
                    "entity": account.business_entity_id,
                    "permission": binding.permission_code,
                },
            ).scalar_one()
        )
        grant = (
            session.execute(
                text("""SELECT p.version,p.scopes,p.status,p.expires_at
          FROM provider_authorizations p WHERE p.id=:id AND p.provider=:provider
          AND p.product_line=:line AND EXISTS(
           SELECT 1 FROM authorization_account_links al WHERE al.tenant_id=p.tenant_id
            AND al.authorization_id=p.id AND al.account_id=:account
            AND al.native_id_snapshot=:native)"""),
                {
                    "id": proof.authorization_id,
                    "provider": binding.provider,
                    "line": binding.product_line,
                    "account": account.id,
                    "native": account.native_id,
                },
            )
            .mappings()
            .first()
        )
        authorized = bool(
            grant is not None
            and grant["status"] == "ACTIVE"
            and grant["version"] == proof.authorization_version
            and grant["expires_at"] > now + timedelta(seconds=2)
            and set(proof.required_scopes) <= set(grant["scopes"])
            and proof.required_scopes
            and account.authorization == "AUTHORIZED"
        )
        state = aggregate(
            binding, assertions, now=now, permitted=allowed, account_authorized=authorized
        )
        fresh = proof.checked_at <= now < proof.expires_at - timedelta(seconds=2)
        current = (
            source is not None
            and binding.catalog_sha256 == head
            and binding.revision == proof.binding_version
        )
        prefix = fresh and current
        for stage, phase in PHASES.items():
            phase_item = latest.get(phase)
            effective = bool(prefix and state["stages"][phase.value]["effective"])
            if stage in {"applicationScope", "account"}:
                effective = effective and authorized
            if stage == "account":
                effective = effective and allowed
            reason = "对应来源阶段仍有效" if effective else "当前上下文缺少有效的阶段证据"
            if stage == "backend" and (
                binding.operation_kind == "WRITE"
                or line == "wxshop"
                or state["status"] != "AUTHORIZED"
            ):
                effective = False
                reason = (
                    "资源命令入口尚待M3-11b交付"
                    if binding.operation_kind == "WRITE"
                    else (
                        "现有上下文未绑定店铺级操作范围，待逐请求范围核验"
                        if line == "wxshop"
                        else "六阶段、当前权限或产品线集成尚未全部就绪"
                    )
                )
            prefix = effective
            evidence[stage] = StageEvidence(
                verified=effective,
                proof_id=phase_item.proof_ref if phase_item is not None else None,
                checked_at=phase_item.checked_at if phase_item is not None else None,
                reason=reason,
            )
        if (
            source is not None
            and (
                source.normalized_path != binding.normalized_path
                or (
                    source.http_method not in {None, "UNKNOWN"}
                    and source.http_method != binding.http_method
                )
            )
            and current_source(session, binding, now) is None
        ):
            for stage in PHASES:
                evidence[stage] = StageEvidence(
                    verified=False,
                    proof_id=None,
                    checked_at=None,
                    reason="当前目录路径/方法与绑定不一致",
                )
        deadline = None
        if grant is not None and all(item.verified for item in evidence.values()):
            deadline = min(
                proof.expires_at,
                grant["expires_at"],
                *(item.expires_at for item in latest.values()),
            )
        return IntegrationOperation(
            catalog_id=binding.catalog_id,
            resource=proof.resource,
            kind=binding.operation_kind,
            account_ids=(account.id,),
            evidence=evidence,
            permission=binding.permission_code,
            editable_fields=proof.editable_fields if binding.operation_kind == "WRITE" else (),
        ), deadline

    def context(self, line: ProductLine) -> IntegrationContext:
        provider, product, _ = DESCRIPTORS[line]
        engine = self.engine.execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=True
        )
        with business_transaction(engine, self.identity) as session:
            now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
            pairs = (
                session.execute(text(PAIRS), {"provider": provider, "line": product})
                .mappings()
                .all()
            )
            if len(pairs) > 1000:
                raise ApiError(
                    ErrorCode.DATA_COVERAGE_INSUFFICIENT, "资源上下文过大，需要先收窄范围"
                )
            accounts: dict[UUID, IntegrationAccount] = {}
            shops: dict[UUID, IntegrationShop] = {}
            entities: dict[UUID, IntegrationEntity] = {}
            links = []
            expires = now + timedelta(seconds=60)
            for pair in pairs:
                authorized = (
                    pair["connected_until"] is not None
                    and pair["connected_until"] > now + timedelta(seconds=2)
                    and pair["status"] == "AUTHORIZED"
                )
                if authorized:
                    expires = min(expires, pair["connected_until"])
                accounts[pair["id"]] = IntegrationAccount(
                    id=pair["id"],
                    native_id=pair["native_id"],
                    name=pair["name"],
                    business_entity_id=pair["entity_id"],
                    provider=provider,
                    product_line=line,
                    authorization="AUTHORIZED" if authorized else "UNAUTHORIZED",
                )
                shops[pair["shop_id"]] = IntegrationShop(
                    id=pair["shop_id"],
                    name=pair["shop_name"],
                    business_entity_id=pair["shop_entity_id"],
                    provider=pair["shop_provider"],
                )
                for identifier, name in (
                    (pair["entity_id"], pair["entity_name"]),
                    (pair["shop_entity_id"], pair["shop_entity_name"]),
                ):
                    entities[identifier] = IntegrationEntity(id=identifier, name=name)
                links.append(
                    IntegrationLink(
                        account_id=pair["id"],
                        shop_id=pair["shop_id"],
                        state="AUTHORIZED" if authorized else "BOUND",
                        checked_at=now,
                    )
                )
            if line == "wxshop":
                native_shops = (
                    session.execute(
                        text("""SELECT s.id,s.name,s.entity_id,s.platform,
                  e.name AS entity_name FROM shops s JOIN business_entities e
                  ON (e.tenant_id,e.id)=(s.tenant_id,s.entity_id)
                  WHERE s.platform='wxshop' AND (
                   access.integration_shop_scope(s.id,'snapshot.read')
                   OR access.integration_shop_scope(s.id,'plan.write')) ORDER BY s.id LIMIT 1001""")
                    )
                    .mappings()
                    .all()
                )
                if len(native_shops) > 1000:
                    raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "店铺上下文过大")
                for native_shop in native_shops:
                    shops[native_shop["id"]] = IntegrationShop(
                        id=native_shop["id"],
                        name=native_shop["name"],
                        business_entity_id=native_shop["entity_id"],
                        provider=native_shop["platform"],
                    )
                    entities[native_shop["entity_id"]] = IntegrationEntity(
                        id=native_shop["entity_id"], name=native_shop["entity_name"]
                    )
            head = session.execute(
                text("SELECT catalog_sha256 FROM catalog_head WHERE singleton")
            ).scalar_one_or_none()
            rows = (
                session.execute(
                    text("""SELECT DISTINCT ON(binding_id,resource) *
              FROM integration_operation_assertions
              ORDER BY binding_id,resource,revision DESC LIMIT 1001""")
                )
                .mappings()
                .all()
            )
            if len(rows) > 1000:
                raise ApiError(
                    ErrorCode.DATA_COVERAGE_INSUFFICIENT, "资源操作依据过大，需要先收窄范围"
                )
            operations = []
            for row in rows:
                account = accounts.get(row["account_id"])
                if account is None:
                    continue
                operation, deadline = self._operation(
                    session,
                    row,
                    account,
                    line,
                    head,
                    now,
                    [link.shop_id for link in links if link.account_id == account.id],
                )
                if operation is not None:
                    operations.append(operation)
                if deadline is not None:
                    expires = min(expires, deadline)
            return IntegrationContext(
                tenant_id=str(self.identity.tenant_id),
                product_line=line,
                checked_at=now,
                expires_at=expires,
                entities=tuple(entities.values()),
                shops=tuple(shops.values()),
                accounts=tuple(accounts.values()),
                links=tuple(links),
                operations=tuple(operations),
            )
