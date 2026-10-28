"""内部商家结算视图：当前权限和独立单位依据前置，不供广告预算使用。"""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.shop_read_scope import require_shop_read


def settlement_view(
    session: Session,
    ctx: GatewayContext,
    application_id: UUID,
    scopes: frozenset[str],
    run_id: UUID,
) -> dict[str, Any]:
    require_shop_read(session, ctx, application_id, scopes)
    from yuliang.modules.access.service import require_shop_account

    require_shop_account(session, ctx.identity, ctx.shop_id, ctx.account_id, "connection.manage")
    row = session.execute(
        text("""SELECT o.values FROM shop_order_observations o
        JOIN sync_runs r ON (r.tenant_id,r.id)=(o.tenant_id,o.run_id)
        JOIN sync_sources s ON (s.tenant_id,s.id)=(r.tenant_id,r.source_id)
        WHERE o.run_id=:run AND o.kind='SETTLEMENT' AND s.account_id=:account
        AND s.binding_id=:binding AND r.state='SUCCESS'"""),
        dict(run=run_id, account=ctx.account_id, shop=ctx.shop_id, binding=ctx.binding_id),
    ).first()
    proof = session.execute(
        text("""SELECT unit FROM shop_settlement_proofs
        WHERE account_id=:account AND shop_id=:shop AND binding_id=:binding AND active
        AND source_sha256=:digest AND checked_at<=clock_timestamp()
        AND expires_at>clock_timestamp()"""),
        dict(
            account=ctx.account_id,
            shop=ctx.shop_id,
            binding=ctx.binding_id,
            digest=ctx.catalog_sha256,
        ),
    ).first()
    result: dict[str, Any] = dict(
        walletKind="MERCHANT_SETTLEMENT",
        budgetUsable=False,
        availableMinor=None,
        pendingMinor=None,
        status="UNCONFIRMED",
    )
    if row is None or proof is None:
        return result
    scale = 1 if proof.unit == "CNY_MINOR" else 100
    for raw, key in (("available_amount", "availableMinor"), ("pending_amount", "pendingMinor")):
        value = row[0].get(raw)
        if value is not None:
            normalized = int(value) * scale
            if abs(normalized) <= 9007199254740991:
                result[key] = normalized
    result["status"] = "NORMALIZED_HISTORICAL_OBSERVATION_NOT_CURRENT_BALANCE"
    return result
