"""连接投影不宣告适配器已接入；断开是本地冻结，不声称供应商撤权。"""

import hmac
import json
import re
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.modules.audit.service import record
from yuliang.modules.capability.models import CONNECTOR_CONTEXTS
from yuliang.modules.capability.service import evaluate_binding, maintenance
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.freshness import ResourceFreshness
from yuliang.modules.connectors.redaction import canonical, fingerprint
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode


def link_authorization(
    connection: Connection, authorization_id: UUID, account_id: UUID, proof_ref: str
) -> None:
    """只接纳已核验的账户发现结果；运行时和HTTP没有自关联入口。"""
    maintenance(connection)
    if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,180}", proof_ref):
        raise ValueError("关联需要可核对证据")
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:id,760118))"),
        {"id": str(authorization_id)},
    )
    grant = (
        connection.execute(
            text("SELECT * FROM provider_authorizations WHERE id=:id"), {"id": authorization_id}
        )
        .mappings()
        .one()
    )
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760117))"),
        {"key": f"{grant['tenant_id']}|{account_id}"},
    )
    account = (
        connection.execute(text("SELECT * FROM ad_accounts WHERE id=:id"), {"id": account_id})
        .mappings()
        .one()
    )
    now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
    if (
        grant["status"] != "ACTIVE"
        or grant["expires_at"] <= now
        or (grant["account_id"] is not None and grant["account_id"] != account_id)
        or (grant["tenant_id"], grant["provider"], grant["product_line"])
        != (account["tenant_id"], account["provider"], account["product_line"])
        or account["status"] != "AUTHORIZED"
        or account["deleted_at"] is not None
    ):
        raise ValueError("授权、账户或产品线不匹配")
    connection.execute(
        text("""INSERT INTO authorization_account_links(tenant_id,authorization_id,
        account_id,provider,product_line,proof_ref,native_id_snapshot)
        VALUES(:tenant,:auth,:account,:provider,:product,:proof,:native)
        ON CONFLICT(tenant_id,authorization_id,account_id) DO UPDATE
        SET native_id_snapshot=EXCLUDED.native_id_snapshot,proof_ref=EXCLUDED.proof_ref,
         verified_at=clock_timestamp()
        WHERE authorization_account_links.native_id_snapshot
         IS DISTINCT FROM EXCLUDED.native_id_snapshot"""),
        {
            "tenant": grant["tenant_id"],
            "auth": authorization_id,
            "account": account_id,
            "provider": grant["provider"],
            "product": grant["product_line"],
            "proof": proof_ref,
            "native": account["native_id"],
        },
    )


class ConnectionService:
    def __init__(
        self,
        engine: Engine,
        identity: VerifiedIdentity,
        session_id: UUID,
        request_id: UUID,
        audit_key: SecretStr,
        *,
        implemented_reads: frozenset[str] = frozenset(),
        reauthorization_applications: frozenset[UUID] = frozenset(),
    ) -> None:
        if not implemented_reads <= {"adq", "wxpromo", "wxshop"}:
            raise ValueError("产品线尚未支持适配")
        self.engine, self.identity, self.session_id = engine, identity, session_id
        self.request_id, self.key, self.implemented_reads = request_id, audit_key, implemented_reads
        self.reauthorization_applications = reauthorization_applications

    def _admin(self, session: Session) -> None:
        if not session.execute(
            text("SELECT access.oauth_actor(:tenant,:member,:user,:session)"),
            {
                "tenant": self.identity.tenant_id,
                "member": self.identity.membership_id,
                "user": self.identity.user_id,
                "session": self.session_id,
            },
        ).scalar_one():
            raise ApiError(ErrorCode.FORBIDDEN, "需要当前管理员与完整连接管理范围")

    def _impact(self, session: Session, authorization_id: UUID, **changes: Any) -> dict[str, Any]:
        try:
            return dict(
                session.execute(
                    text("""SELECT access.connection_impact(:member,:user,:session,:auth,
                :apply,CAST(:expected AS jsonb),:digest,:request)"""),
                    {
                        "member": self.identity.membership_id,
                        "user": self.identity.user_id,
                        "session": self.session_id,
                        "auth": authorization_id,
                        "apply": False,
                        "expected": None,
                        "digest": None,
                        "request": self.request_id,
                    }
                    | changes,
                ).scalar_one()
            )
        except DBAPIError as error:
            code = getattr(error.orig, "sqlstate", None)
            mapped = {
                "42501": ErrorCode.FORBIDDEN,
                "P0002": ErrorCode.NO_DATA,
                "40001": ErrorCode.VERSION_CONFLICT,
                "54000": ErrorCode.DATA_COVERAGE_INSUFFICIENT,
            }
            if code in mapped:
                raise ApiError(mapped[code], "连接权限、影响面或版本需要重新核对") from None
            raise

    def _digest(self, impact: dict[str, Any]) -> str:
        return fingerprint(self.key, "connection-impact-v1", canonical(impact))

    def preview(self, authorization_id: UUID) -> dict[str, Any]:
        with business_transaction(self.engine, self.identity) as session:
            impact = self._impact(session, authorization_id)
            return impact | {"impactDigest": self._digest(impact)}

    def disconnect(self, authorization_id: UUID, digest: str) -> dict[str, Any]:
        with business_transaction(self.engine, self.identity) as session:
            impact = self._impact(session, authorization_id)
            receipt = (
                session.execute(
                    text(
                        "SELECT preview_digest,impact FROM connection_disconnections "
                        "WHERE authorization_id=:id"
                    ),
                    {"id": authorization_id},
                )
                .mappings()
                .first()
            )
            if receipt:
                if not hmac.compare_digest(receipt["preview_digest"], digest):
                    raise ApiError(ErrorCode.VERSION_CONFLICT, "连接已断开，请刷新原记录")
                return self._disconnected(receipt["impact"], replayed=True)
            if not hmac.compare_digest(self._digest(impact), digest):
                raise ApiError(ErrorCode.VERSION_CONFLICT, "影响面已变化，请重新确认")
            applied = self._impact(
                session, authorization_id, apply=True, expected=json.dumps(impact), digest=digest
            )
            record(
                session,
                self.identity,
                {
                    "action": "connection.disconnect",
                    "object_type": "authorization",
                    "object_id": authorization_id,
                    "request_id": self.request_id,
                    "before_hash": digest,
                    "after_hash": fingerprint(
                        self.key, "connection-disconnected", canonical(applied)
                    ),
                    "detail": {"changed_fields": ["status"]},
                },
            )
            return self._disconnected(applied, replayed=False)

    @staticmethod
    def _disconnected(impact: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
        return {
            "authorizationId": impact["authorizationId"],
            "status": "REVOKED",
            "version": impact["authorizationVersion"] + 1,
            "appliedImpact": impact,
            "providerRevocation": "NOT_REQUESTED",
            "replayed": replayed,
        }

    def reauthorize(self, authorization_id: UUID) -> dict[str, Any]:
        with business_transaction(self.engine, self.identity) as session:
            self._admin(session)
            exists = session.execute(
                text("SELECT id FROM provider_authorizations WHERE id=:id"),
                {"id": authorization_id},
            ).scalar_one_or_none()
            if not exists:
                raise ApiError(ErrorCode.NO_DATA, "连接不存在")
        # M1-06/07实际OAuth入口未装配，不能伪造跳转URL或复活旧能力证据。
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "当前产品线重授入口尚未就绪")

    def listing(self) -> dict[str, Any]:
        with business_transaction(self.engine, self.identity) as session:
            self._admin(session)
            rows = (
                session.execute(
                    text("""SELECT id,account_id,application_id,connector_id,
            provider,product_line,scopes,status,expires_at,version,authorized_at,last_verified_at
            FROM provider_authorizations ORDER BY authorized_at,id LIMIT 1001""")
                )
                .mappings()
                .all()
            )
            if len(rows) > 1000:
                raise ApiError(ErrorCode.DATA_COVERAGE_INSUFFICIENT, "连接过多，需要分范围读取")
            now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
            connections = []
            for grant in rows:
                status = (
                    "EXPIRED"
                    if grant["status"] == "ACTIVE" and grant["expires_at"] <= now
                    else grant["status"]
                )
                accounts = list(
                    session.execute(
                        text("""SELECT account_id FROM authorization_account_links
                WHERE authorization_id=:id UNION SELECT account_id FROM provider_authorizations
                WHERE id=:id AND account_id IS NOT NULL ORDER BY account_id"""),
                        {"id": grant["id"]},
                    ).scalars()
                )
                operations, health = (
                    self._read_state(session, grant, accounts) if status == "ACTIVE" else ([], [])
                )
                connections.append(
                    {
                        "id": str(grant["id"]),
                        "connectorId": grant["connector_id"],
                        "applicationId": str(grant["application_id"]),
                        "provider": grant["provider"],
                        "productLine": grant["product_line"],
                        "accountIds": [str(a) for a in accounts],
                        "status": status,
                        "scopes": list(grant["scopes"]),
                        "expiresAt": grant["expires_at"],
                        "checkedAt": grant["last_verified_at"],
                        "version": grant["version"],
                        "operations": operations,
                        "resources": health,
                        "reauthorizationAvailable": grant["application_id"]
                        in self.reauthorization_applications,
                    }
                )
            connectors = []
            for key in CONNECTOR_CONTEXTS:
                matching = [c for c in connections if c["connectorId"] == key]
                active = [c for c in matching if c["status"] == "ACTIVE"]
                degraded = (bool(matching) and not active) or any(
                    r["status"] in {"DEGRADED", "STALE"} for c in active for r in c["resources"]
                )
                state = (
                    "DEGRADED"
                    if degraded
                    else "READ_ONLY"
                    if active and any(c["operations"] for c in active)
                    else "UNKNOWN"
                )
                checked = [
                    c["checkedAt"] for c in (active or matching) if c["checkedAt"] is not None
                ]
                connectors.append(
                    {
                        "id": key,
                        "status": state,
                        "scopes": sorted({v for c in active for v in c["scopes"]}),
                        "checkedAt": min(checked) if checked else None,
                    }
                )
            return {"connectors": connectors, "connections": connections}

    def _read_state(
        self, session: Session, grant: Any, accounts: list[UUID]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if grant["connector_id"] not in self.implemented_reads:
            return [], []
        operations: list[dict[str, Any]] = []
        health: list[dict[str, Any]] = []
        for account in accounts:
            shop = session.execute(
                text(
                    "SELECT shop_id FROM entity_links WHERE account_id=:id ORDER BY shop_id LIMIT 1"
                ),
                {"id": account},
            ).scalar_one_or_none()
            if shop is None:
                continue
            bindings = (
                session.execute(
                    text("""SELECT * FROM capability_bindings WHERE account_id=:id
            AND connector_id=:connector AND operation_kind='READ' AND active ORDER BY id"""),
                    {"id": account, "connector": grant["connector_id"]},
                )
                .mappings()
                .all()
            )
            for binding in bindings:
                result = evaluate_binding(session, self.identity, shop, binding["id"])
                if result["status"] != "AUTHORIZED":
                    continue
                operations.extend(
                    dict(op, accountId=str(account), bindingId=str(binding["id"]))
                    for op in result["operations"]
                )
                ctx = GatewayContext(
                    self.identity,
                    shop,
                    account,
                    session.execute(
                        text("SELECT native_id FROM ad_accounts WHERE id=:id"), {"id": account}
                    ).scalar_one(),
                    binding["connector_id"],
                    binding["provider"],
                    binding["product_line"],
                    grant["id"],
                    self.request_id,
                    binding["id"],
                    binding["revision"],
                    binding["catalog_id"],
                    binding["catalog_sha256"],
                )
                for source in session.execute(
                    text("SELECT id FROM sync_sources WHERE binding_id=:id ORDER BY id"),
                    {"id": binding["id"]},
                ).scalars():
                    value = ResourceFreshness(self.engine).inspect(session, ctx, source)
                    health.append(
                        {
                            key: value[key]
                            for key in (
                                "sourceId",
                                "resourceKey",
                                "status",
                                "reason",
                                "dataAvailable",
                                "sourceUpdatedAt",
                                "fetchedAt",
                                "queriedAt",
                                "maxAgeSeconds",
                                "refreshSeconds",
                            )
                        }
                    )
        return operations, health
