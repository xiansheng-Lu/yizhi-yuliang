"""ADQ各只读采集器共用的实际应用/授权/原生关联/六阶段守卫。"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.capability.service import evaluate_binding
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.sync_store import SyncConflict
from yuliang.platform.errors import ApiError, ErrorCode


def require_adq_read(
    session: Session,
    ctx: GatewayContext,
    application_id: UUID,
    required_scopes: frozenset[str],
) -> int:
    if not required_scopes or (ctx.connector_id, ctx.provider, ctx.product_line) != (
        "adq",
        "tencent",
        "adq",
    ):
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "未提供ADQ实际只读scope上下文")
    grant = session.execute(
        text("""SELECT p.version,p.scopes,p.application_id
      FROM provider_authorizations p JOIN authorization_account_links l
       ON l.tenant_id=p.tenant_id AND l.authorization_id=p.id
      JOIN ad_accounts a ON (a.tenant_id,a.id)=(l.tenant_id,l.account_id)
      WHERE p.id=:grant AND a.id=:account AND a.native_id=:native
       AND l.native_id_snapshot=a.native_id AND a.status='AUTHORIZED' AND a.deleted_at IS NULL
       AND p.status='ACTIVE' AND p.expires_at>clock_timestamp()
       AND p.connector_id='adq' AND p.provider='tencent' AND p.product_line='adq'"""),
        dict(grant=ctx.authorization_id, account=ctx.account_id, native=ctx.native_account_id),
    ).first()
    state = evaluate_binding(session, ctx.identity, ctx.shop_id, ctx.binding_id)
    if (
        grant is None
        or grant.application_id != application_id
        or not required_scopes <= set(grant.scopes)
        or state["status"] != "AUTHORIZED"
    ):
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "账户关联、scope或六阶段只读证据未就绪")
    if ctx.authorization_version is not None and ctx.authorization_version != grant.version:
        raise SyncConflict("AUTHORIZATION_CHANGED")
    return int(grant.version)
