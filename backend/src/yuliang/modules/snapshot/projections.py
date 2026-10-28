"""真实目录/规范商品/持久任务投影；必填未知不补0或静默漏集合。"""

from datetime import timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.access.service import require_shop_account
from yuliang.modules.capability.service import evaluate_binding
from yuliang.modules.command.projections import load_projection
from yuliang.modules.commerce.shop_details import product_view
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.policy.models import PolicyConfig
from yuliang.modules.snapshot.models import COLLECTIONS, PAIR_CONNECTORS, ReportingScope
from yuliang.modules.snapshot.scope import bounded
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.errors import ApiError, ErrorCode


def products(
    session: Session,
    identity: VerifiedIdentity,
    scope: ReportingScope,
    shopids: list[UUID],
    pairs: list[dict[str, Any]],
    gaps: list[dict[str, str]],
) -> list[dict[str, Any]]:
    rows = bounded(
        session,
        """SELECT * FROM products WHERE shop_id=ANY(CAST(:shops AS uuid[]))
AND deleted_at IS NULL ORDER BY id""",
        dict(shops=shopids),
        "products",
    )
    raw = bounded(
        session,
        """SELECT * FROM commerce_products WHERE shop_id=ANY(CAST(:shops AS
uuid[])) AND deleted_at IS NULL ORDER BY id""",
        dict(shops=shopids),
        "products",
    )
    allowed = {(p["shop_id"], p["id"]) for p in pairs}
    for item in raw:
        if (item["shop_id"], item["account_id"]) in allowed and not any(
            p["shop_id"] == item["shop_id"] and p["native_id"] == item["native_id"] for p in rows
        ):
            gaps.append(
                dict(
                    field="products", reason="OBSERVED_PRODUCT_NOT_LINKED_TO_NAMED_FRONTEND_CATALOG"
                )
            )
    result = []
    for row in rows:
        candidates = [
            r
            for r in raw
            if r["shop_id"] == row["shop_id"]
            and r["native_id"] == row["native_id"]
            and (r["shop_id"], r["account_id"]) in allowed
        ]
        if len(candidates) != 1:
            gaps.append(
                dict(field="products", reason="PRODUCT_ACCOUNT_OR_MEASURE_SOURCE_UNCONFIRMED")
            )
            continue
        source = candidates[0]
        binding = (
            session.execute(
                text("""SELECT b.*,a.native_id AS native_account,p.id AS
authorization_id,p.version AS authorization_version FROM
capability_bindings b JOIN ad_accounts a ON
(a.tenant_id,a.id)=(b.tenant_id,b.account_id) JOIN
authorization_account_links l ON
(l.tenant_id,l.account_id)=(a.tenant_id,a.id) JOIN
provider_authorizations p ON
(p.tenant_id,p.id)=(l.tenant_id,l.authorization_id) WHERE
b.account_id=:account AND b.connector_id='wxshop' AND
b.operation_kind='READ' AND
b.normalized_path='/channels/ec/product/get' AND b.active AND
p.status='ACTIVE' AND p.expires_at>clock_timestamp() AND
cardinality(p.scopes)>0 AND l.native_id_snapshot=a.native_id
ORDER BY p.id,b.id LIMIT 2"""),
                dict(account=source["account_id"]),
            )
            .mappings()
            .all()
        )
        if len(binding) != 1:
            gaps.append(
                dict(
                    field="products",
                    reason="CURRENT_PRODUCT_READ_AUTHORIZATION_AMBIGUOUS_OR_MISSING",
                )
            )
            continue
        b = binding[0]
        state = evaluate_binding(session, identity, row["shop_id"], b["id"])
        if state["status"] != "AUTHORIZED":
            gaps.append(
                dict(field="products", reason="PRODUCT_CURRENT_READ_CAPABILITY_UNCONFIRMED")
            )
            continue
        ctx = GatewayContext(
            identity,
            row["shop_id"],
            source["account_id"],
            b["native_account"],
            b["connector_id"],
            b["provider"],
            b["product_line"],
            b["authorization_id"],
            UUID(int=0),
            b["id"],
            b["revision"],
            b["catalog_id"],
            b["catalog_sha256"],
            b["authorization_version"],
        )
        view = product_view(session, ctx, row["native_id"])
        if view.get("deleted") is True:
            continue
        skus = view["skus"]
        if len(skus) != 1:
            gaps.append(
                dict(field="products", reason="FRONTEND_SINGLE_SKU_CARDINALITY_UNCONFIRMED")
            )
            continue
        sku = skus[0]
        if sku["priceMinor"] is None:
            gaps.append(
                dict(field="products.priceMinor", reason="REQUIRED_PRICE_UNIT_OR_VALUE_UNCONFIRMED")
            )
            continue
        canonical = session.execute(
            text("""SELECT id FROM skus WHERE product_id=:product AND
native_id=:native"""),
            dict(product=row["id"], native=sku["skuId"]),
        ).scalar_one_or_none()
        if canonical is None:
            gaps.append(dict(field="products.skuId", reason="NORMATIVE_SKU_DIRECTORY_LINK_MISSING"))
            continue
        values = session.execute(
            text("""SELECT metric_name,value_num,comparable_key FROM metric_facts
WHERE sku_id=:sku AND entity_kind='SKU' AND granularity='DAY' AND
fact_date=:day AND product_line='wxshop' AND shop_id=:shop AND
account_id=:account AND unit='COUNT' AND metric_name IN
('clicks','paid_order_count')"""),
            dict(sku=canonical, day=scope.date, shop=row["shop_id"], account=source["account_id"]),
        ).all()

        def metric(name: str, values: Any = values) -> int | None:
            found = [r for r in values if r.metric_name == name]
            return (
                int(found[0].value_num)
                if len(found) == 1 and found[0].value_num is not None
                else None
            )

        result.append(
            dict(
                id=str(row["id"]),
                tenantId=str(identity.tenant_id),
                shopId=str(row["shop_id"]),
                skuId=str(canonical),
                name=row["name"],
                priceMinor=sku["priceMinor"],
                stock=sku["stock"],
                clicks=metric("clicks"),
                paidOrders=metric("paid_order_count"),
                speechAdvice="",
                fetchedAt=view.get("fetchedAt"),
                sourceUpdatedAt=view.get("sourceAt"),
                freshness=view.get("freshness"),
            )
        )
    return result


def project(
    session: Session,
    identity: VerifiedIdentity,
    scope: ReportingScope,
    shops: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
) -> dict[str, Any]:
    arrays: dict[str, list[dict[str, Any]]] = {key: [] for key in COLLECTIONS}
    gaps: list[dict[str, str]] = []
    tenant = (
        session.execute(
            text("SELECT id,name FROM tenants WHERE id=:id"), dict(id=identity.tenant_id)
        )
        .mappings()
        .one()
    )
    arrays["tenants"] = [dict(id=str(tenant["id"]), name=tenant["name"])]
    scopedpairs = [
        p
        for p in pairs
        if scope.shop_id in {"all", str(p["shop_id"])}
        and scope.connector_id in {"all", PAIR_CONNECTORS.get((p["provider"], p["product_line"]))}
    ]
    pairaccounts = {p["id"] for p in scopedpairs}
    shopids = [
        s["id"]
        for s in shops
        if scope.shop_id in {"all", str(s["id"])}
        and (
            scope.connector_id == "all"
            or s["platform"] == scope.connector_id
            or any(p["shop_id"] == s["id"] for p in scopedpairs)
        )
    ]
    lives = bounded(
        session,
        """SELECT * FROM live_sessions WHERE shop_id=ANY(CAST(:shops AS
uuid[])) ORDER BY id""",
        dict(shops=shopids),
        "liveSessions",
    )
    if scope.live_session_id != "all":
        lives = [live_row for live_row in lives if str(live_row["id"]) == scope.live_session_id]
        shopids = list({live_row["shop_id"] for live_row in lives})
        scopedpairs = [p for p in scopedpairs if p["shop_id"] in shopids]
        pairaccounts = {p["id"] for p in scopedpairs}
    for _live in lives:
        gaps.append(
            dict(
                field="liveSessions",
                reason="LIVE_PLATFORM_TIMES_AND_METRICS_PROJECTION_NOT_IMPLEMENTED",
            )
        )
    arrays["shops"] = [
        dict(
            id=str(s["id"]),
            tenantId=str(identity.tenant_id),
            name=s["name"],
            connectorId=s["platform"],
        )
        for s in shops
        if s["id"] in shopids
    ]
    seen = set()
    for pair in scopedpairs:
        if pair["id"] in seen:
            gaps.append(
                dict(
                    field="adAccounts", reason="MULTI_SHOP_ACCOUNT_REQUIRES_NARROWER_FRONTEND_SCOPE"
                )
            )
            continue
        seen.add(pair["id"])
        connector = PAIR_CONNECTORS.get((pair["provider"], pair["product_line"]))
        if connector is None:
            gaps.append(
                dict(field="adAccounts", reason="PRODUCT_LINE_NOT_IN_FRONTEND_CONNECTOR_CONTRACT")
            )
            continue
        arrays["adAccounts"].append(
            dict(
                id=str(pair["id"]),
                tenantId=str(identity.tenant_id),
                shopId=str(pair["shop_id"]),
                connectorId=connector,
                name=pair["name"],
            )
        )
    campaigns = bounded(
        session,
        """SELECT * FROM campaigns WHERE shop_id=ANY(CAST(:shops AS uuid[]))
AND account_id=ANY(CAST(:accounts AS uuid[])) ORDER BY id""",
        dict(shops=shopids, accounts=list(pairaccounts)),
        "plans",
    )
    observed = bounded(
        session,
        """SELECT id FROM adq_asset_mirrors WHERE
account_id=ANY(CAST(:accounts AS uuid[])) AND kind='CAMPAIGN'
ORDER BY id""",
        dict(accounts=list(pairaccounts)),
        "plans",
    )
    if campaigns or observed:
        gaps.append(
            dict(
                field="plans",
                reason="REQUIRED_PLAN_BUDGET_BID_ROI_STATUS_SOURCE_TIME_NOT_ALL_CONFIRMED",
            )
        )
    arrays["products"] = products(session, identity, scope, shopids, scopedpairs, gaps)
    productids = [UUID(p["id"]) for p in arrays["products"]]
    assets = bounded(
        session,
        """SELECT a.*,c.account_id AS source_account,p.id AS
frontend_product_id FROM content_assets a JOIN commerce_products
c ON (c.tenant_id,c.id)=(a.tenant_id,a.product_id) JOIN products
p ON p.shop_id=c.shop_id AND p.native_id=c.native_id WHERE
p.id=ANY(CAST(:products AS uuid[])) AND a.shop_id=ANY(CAST(:shops
AS uuid[])) AND a.deleted_at IS NULL AND c.deleted_at IS NULL
ORDER BY a.id""",
        dict(products=productids, shops=shopids),
        "materials",
    )
    for asset in assets:
        try:
            require_shop_account(
                session, identity, asset["shop_id"], asset["source_account"], "overview.read"
            )
        except ApiError as error:
            if error.code != ErrorCode.FORBIDDEN:
                raise
            continue
        if asset["kind"] not in {"image", "text"} or asset["duration_state"] != "NOT_APPLICABLE":
            gaps.append(dict(field="materials", reason="ASSET_AVAILABILITY_NOT_IMPLEMENTED"))
            continue
        kind = {"image": "IMAGE", "video": "VIDEO", "text": "SCRIPT"}.get(asset["kind"])
        if kind is None:
            gaps.append(dict(field="materials", reason="ASSET_KIND_NOT_IN_FRONTEND_CONTRACT"))
            continue
        arrays["materials"].append(
            dict(
                id=str(asset["id"]),
                tenantId=str(identity.tenant_id),
                productId=str(asset["frontend_product_id"]),
                name=asset["name"],
                version=asset["object_version"],
                kind=kind,
                status="READY",
            )
        )
    jobs = bounded(
        session,
        """SELECT * FROM command_jobs WHERE shop_id=ANY(CAST(:shops AS
uuid[])) AND (account_id IS NULL OR account_id=ANY(CAST(:accounts
AS uuid[]))) ORDER BY id""",
        dict(shops=shopids, accounts=list(pairaccounts)),
        "tasks",
    )
    for job in jobs:
        if scope.live_session_id != "all" and str(job["live_session_id"]) != scope.live_session_id:
            continue
        if job["created_at"].astimezone(timezone(timedelta(hours=8))).date() != scope.date:
            continue
        if job["document"] is None:
            gaps.append(dict(field="tasks", reason="PERSISTED_JOB_DOCUMENT_MISSING"))
            continue
        arrays["tasks"].append(load_projection(session, job).job.model_dump(mode="json"))
    alltenant = session.execute(
        text(
            """SELECT EXISTS(SELECT 1 FROM data_scopes WHERE
membership_id=:member AND scope_type='ALL_TENANT' AND
(permissions_mask IS NULL OR
'snapshot.read'=ANY(permissions_mask)))"""
        ),
        dict(member=identity.membership_id),
    ).scalar_one()
    if alltenant:
        for policy in bounded(
            session,
            """SELECT r.id,r.version,p.config FROM risk_policies r JOIN
policy_versions p ON
(p.tenant_id,p.policy_id,p.version)=(r.tenant_id,r.id,r.version)
ORDER BY r.id""",
            {},
            "riskPolicies",
        ):
            config = PolicyConfig.model_validate(policy["config"])
            arrays["riskPolicies"].append(
                dict(
                    id=str(policy["id"]),
                    tenantId=str(identity.tenant_id),
                    dailyBudgetMinor=config.daily_budget_minor,
                    targetRoi=float(config.target_roi),
                    maxChangePercent=float(config.max_change_percent),
                    cooldownMinutes=config.cooldown_minutes,
                    mode=config.mode,
                    version=policy["version"],
                )
            )
    connectorids = {p["connectorId"] for p in arrays["adAccounts"]} | {
        s["connectorId"] for s in arrays["shops"]
    }
    for c in sorted(connectorids):
        arrays["connectors"].append(dict(id=c, status="UNKNOWN", scopes=[], checkedAt=None))
    return dict(arrays=arrays, gaps=gaps)
