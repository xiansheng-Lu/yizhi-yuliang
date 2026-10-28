"""服务端授权范围与有界完整查询；对象存在不构成权限。"""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.snapshot.models import PAIR_CONNECTORS, ReportingScope
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.errors import ApiError, ErrorCode

MAX_ROWS = 10000


def bounded(
    session: Session, sql: str, params: dict[str, Any], collection: str
) -> list[dict[str, Any]]:
    rows = [
        dict(r)
        for r in session.execute(text(sql + " LIMIT " + str(MAX_ROWS + 1)), params).mappings()
    ]
    if len(rows) > MAX_ROWS:
        raise ApiError(
            ErrorCode.DATA_COVERAGE_INSUFFICIENT,
            "当前范围数据过大，请收窄范围",
            details=[dict(field=collection, reason="COMPLETE_COLLECTION_EXCEEDS_10000")],
        )
    return rows


def require_member(session: Session, identity: VerifiedIdentity) -> dict[str, Any]:
    row = (
        session.execute(
            text("SELECT access.snapshot_member_version(:member,:user) AS authz_version"),
            dict(member=identity.membership_id, user=identity.user_id),
        )
        .mappings()
        .first()
    )
    if row is None or row["authz_version"] is None:
        raise ApiError(ErrorCode.FORBIDDEN, "没有当前工作区读取权限")
    return dict(row)


def visible(
    session: Session, identity: VerifiedIdentity, scope: ReportingScope | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    params = dict(
        member=identity.membership_id,
        selected_shop=UUID(scope.shop_id) if scope is not None and scope.shop_id != "all" else None,
    )
    shops = bounded(
        session,
        """SELECT s.* FROM shops s WHERE s.deleted_at IS NULL AND
(CAST(:selected_shop AS uuid) IS NULL OR s.id=CAST(:selected_shop
AS uuid)) AND EXISTS( SELECT 1 FROM data_scopes d WHERE
d.membership_id=:member AND (d.permissions_mask IS NULL OR
'snapshot.read'=ANY(d.permissions_mask)) AND
(d.scope_type='ALL_TENANT' OR d.shop_id=s.id OR
d.entity_id=s.entity_id)) ORDER BY s.id""",
        params,
        "shops",
    )
    pairs = bounded(
        session,
        """SELECT a.*,l.shop_id FROM ad_accounts a JOIN entity_links l ON
(l.tenant_id,l.account_id)=(a.tenant_id,a.id) JOIN shops s ON
(s.tenant_id,s.id)=(l.tenant_id,l.shop_id) WHERE
(CAST(:selected_shop AS uuid) IS NULL OR s.id=CAST(:selected_shop
AS uuid)) AND a.deleted_at IS NULL AND s.deleted_at IS NULL AND
a.status IN ('BOUND','AUTHORIZED','DEGRADED') AND EXISTS( SELECT
1 FROM data_scopes d WHERE d.membership_id=:member AND
(d.permissions_mask IS NULL OR
'snapshot.read'=ANY(d.permissions_mask)) AND
(d.scope_type='ALL_TENANT' OR d.shop_id=s.id OR
d.entity_id=s.entity_id)) AND EXISTS(SELECT 1 FROM data_scopes d
WHERE d.membership_id=:member AND (d.permissions_mask IS NULL OR
'snapshot.read'=ANY(d.permissions_mask)) AND
(d.scope_type='ALL_TENANT' OR d.account_id=a.id OR
d.entity_id=a.entity_id)) ORDER BY a.id,l.shop_id""",
        params,
        "adAccounts",
    )
    return shops, pairs


def validate_scope(
    session: Session,
    scope: ReportingScope,
    shops: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
) -> None:
    shopids = {str(s["id"]) for s in shops}
    if scope.shop_id != "all" and scope.shop_id not in shopids:
        raise ApiError(ErrorCode.FORBIDDEN, "选择的店铺不在当前授权范围")
    if scope.live_session_id != "all":
        live = session.execute(
            text("SELECT shop_id FROM live_sessions WHERE id=:id"),
            dict(id=UUID(scope.live_session_id)),
        ).scalar_one_or_none()
        if live is None or str(live) not in shopids or scope.shop_id not in {"all", str(live)}:
            raise ApiError(ErrorCode.FORBIDDEN, "选择的场次不在当前店铺范围")
    if scope.connector_id != "all":
        available = {
            PAIR_CONNECTORS.get((p["provider"], p["product_line"]))
            for p in pairs
            if scope.shop_id in {"all", str(p["shop_id"])}
        } | {s["platform"] for s in shops if scope.shop_id in {"all", str(s["id"])}}
        if scope.connector_id not in available:
            raise ApiError(ErrorCode.FORBIDDEN, "选择的连接器不在当前范围")
