"""商品详情追加观察与独立口径依据；不把不可见或失败当删除。"""

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from yuliang.modules.capability.service import maintenance
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.shop_detail_protocol import DetailPage
from yuliang.modules.connectors.sync_store import PagePermit


class ProductMeasureProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    shop_id: UUID
    account_id: UUID
    binding_id: UUID
    price_unit: Literal["UNCONFIRMED", "CNY_MINOR"] = "UNCONFIRMED"
    stock_unit: Literal["UNCONFIRMED", "ITEM_COUNT"] = "UNCONFIRMED"
    status_mapping: dict[str, Literal["ON_SALE", "OFF_SALE", "RECYCLED"]] = Field(
        default_factory=dict
    )
    source_ref: str = Field(pattern=r"^[A-Za-z0-9_./:-]{1,180}$")
    checked_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def valid(self) -> "ProductMeasureProof":
        if (
            self.checked_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.expires_at <= self.checked_at
        ):
            raise ValueError("依据有效期非法")
        if any(not k.isdecimal() or len(k) > 10 for k in self.status_mapping):
            raise ValueError("状态原生代码非法")
        return self


def register_product_proof(connection: Connection, tenant: int, proof: ProductMeasureProof) -> None:
    maintenance(connection)
    now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
    if not proof.checked_at <= now < proof.expires_at:
        raise ValueError("只接纳当前有效依据")
    binding = connection.execute(
        text("""SELECT b.id FROM capability_bindings b JOIN entity_links l
        ON (l.tenant_id,l.account_id)=(b.tenant_id,b.account_id)
        JOIN ad_accounts a ON (a.tenant_id,a.id)=(b.tenant_id,b.account_id)
        WHERE b.tenant_id=:tenant AND b.id=:binding AND b.account_id=:account AND l.shop_id=:shop
        AND b.connector_id='wxshop' AND b.provider='wxshop' AND b.product_line='wxshop'
        AND b.operation_kind='READ' AND b.http_method='POST'
        AND b.normalized_path='/channels/ec/product/get' AND b.active
        AND a.status='AUTHORIZED' AND a.deleted_at IS NULL"""),
        dict(tenant=tenant, binding=proof.binding_id, account=proof.account_id, shop=proof.shop_id),
    ).first()
    if binding is None:
        raise ValueError("依据未绑定实际小店详情范围")
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,760188))"),
        dict(key=f"{tenant}|{proof.shop_id}|{proof.account_id}|{proof.binding_id}"),
    )
    if connection.execute(
        text("SELECT 1 FROM shop_product_measure_proofs WHERE id=:id"), dict(id=proof.id)
    ).first():
        raise ValueError("依据ID不能覆盖或复活")
    connection.execute(
        text(
            "UPDATE shop_product_measure_proofs SET active=false WHERE tenant_id=:tenant "
            "AND shop_id=:shop AND account_id=:account AND binding_id=:binding AND active"
        ),
        dict(tenant=tenant, shop=proof.shop_id, account=proof.account_id, binding=proof.binding_id),
    )
    connection.execute(
        text(
            "INSERT INTO shop_product_measure_proofs(id,tenant_id,shop_id,account_id,binding_id,"
            "payload,checked_at,expires_at) "
            "VALUES(:id,:tenant,:shop,:account,:binding,CAST(:payload AS jsonb),:checked,:expires)"
        ),
        dict(
            id=proof.id,
            tenant=tenant,
            shop=proof.shop_id,
            account=proof.account_id,
            binding=proof.binding_id,
            payload=proof.model_dump_json(),
            checked=proof.checked_at,
            expires=proof.expires_at,
        ),
    )


def project_detail(
    session: Session, ctx: GatewayContext, permit: PagePermit, page: DetailPage
) -> None:
    values = dict(
        id=uuid4(),
        tenant=ctx.identity.tenant_id,
        shop=ctx.shop_id,
        account=ctx.account_id,
        native=page.product["product_id"],
    )
    session.execute(
        text(
            "INSERT INTO commerce_products(id,tenant_id,shop_id,account_id,native_id) "
            "VALUES(:id,:tenant,:shop,:account,:native) ON CONFLICT DO NOTHING"
        ),
        values,
    )
    internal = session.execute(
        text(
            "SELECT id FROM commerce_products WHERE shop_id=:shop "
            "AND account_id=:account AND native_id=:native"
        ),
        values,
    ).scalar_one()
    for sku in page.product["skus"]:
        session.execute(
            text(
                "INSERT INTO commerce_skus(id,tenant_id,product_id,native_id) "
                "VALUES(:id,:tenant,:product,:native) ON CONFLICT DO NOTHING"
            ),
            dict(id=uuid4(), tenant=ctx.identity.tenant_id, product=internal, native=sku["sku_id"]),
        )
    session.execute(
        text("""INSERT INTO shop_product_detail_pages(tenant_id,shop_id,account_id,source_id,
        run_id,page_no,outbound_id,product_id,source_at,fetched_at,payload)
        VALUES(:tenant,:shop,:account,:source,:run,:page,:outbound,:product,NULL,
        :fetched,CAST(:payload AS jsonb))"""),
        dict(
            tenant=ctx.identity.tenant_id,
            shop=ctx.shop_id,
            account=ctx.account_id,
            source=permit.lease.source_id,
            run=permit.lease.run_id,
            page=permit.lease.page_no,
            outbound=permit.outbound_id,
            product=page.product["product_id"],
            fetched=page.sync.fetched_at,
            payload=json.dumps(page.product),
        ),
    )


def product_view(session: Session, ctx: GatewayContext, product_id: str) -> dict[str, Any]:
    row = (
        session.execute(
            text("""SELECT p.* FROM shop_product_detail_pages p JOIN sync_sources s
      ON (s.tenant_id,s.id)=(p.tenant_id,p.source_id)
      WHERE p.shop_id=:shop AND p.account_id=:account AND s.binding_id=:binding
      AND p.product_id=:product
      ORDER BY p.fetched_at DESC,p.sequence DESC LIMIT 1"""),
            dict(
                shop=ctx.shop_id, account=ctx.account_id, binding=ctx.binding_id, product=product_id
            ),
        )
        .mappings()
        .first()
    )
    proofrow = (
        session.execute(
            text("""SELECT * FROM shop_product_measure_proofs WHERE shop_id=:shop
      AND account_id=:account AND binding_id=:binding AND active"""),
            dict(shop=ctx.shop_id, account=ctx.account_id, binding=ctx.binding_id),
        )
        .mappings()
        .first()
    )
    proof = None
    if proofrow is not None:
        candidate = ProductMeasureProof.model_validate(proofrow["payload"])
        now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
        if (
            candidate.id,
            candidate.shop_id,
            candidate.account_id,
            candidate.binding_id,
            candidate.checked_at,
            candidate.expires_at,
        ) == (
            proofrow["id"],
            ctx.shop_id,
            ctx.account_id,
            ctx.binding_id,
            proofrow["checked_at"],
            proofrow["expires_at"],
        ) and candidate.checked_at <= now < candidate.expires_at:
            proof = candidate
    if row is None:
        return dict(productId=product_id, state="NO_OBSERVATION", skus=[], deleted=False)
    internal = session.execute(
        text(
            "SELECT id FROM commerce_products WHERE shop_id=:shop "
            "AND account_id=:account AND native_id=:native"
        ),
        dict(shop=ctx.shop_id, account=ctx.account_id, native=product_id),
    ).scalar_one()
    latest_run = (
        session.execute(
            text(
                "SELECT state,started_at FROM sync_runs WHERE source_id=:source "
                "ORDER BY started_at DESC,id DESC LIMIT 1"
            ),
            dict(source=row["source_id"]),
        )
        .mappings()
        .one()
    )
    raw = row["payload"]
    state = proof.status_mapping.get(raw["native_status"], "UNKNOWN") if proof else "UNKNOWN"
    skus = []
    for sku in raw["skus"]:
        price = sku["native_price"] if proof and proof.price_unit == "CNY_MINOR" else None
        stock = sku["native_stock"] if proof and proof.stock_unit == "ITEM_COUNT" else None
        price = int(price) if price is not None and int(price) <= 9007199254740991 else None
        stock = int(stock) if stock is not None and int(stock) <= 9007199254740991 else None
        skus.append(
            dict(
                skuId=sku["sku_id"],
                priceMinor=price,
                stock=stock,
                priceState="KNOWN" if price is not None else "UNCONFIRMED_OR_MISSING",
                stockState="KNOWN" if stock is not None else "UNCONFIRMED_OR_MISSING",
                nativePrice=sku["native_price"],
                nativeStock=sku["native_stock"],
            )
        )
    return dict(
        productId=product_id,
        internalId=str(internal),
        state=state,
        deleted=state == "RECYCLED",
        skuPresence=raw["sku_presence"],
        skus=skus,
        freshness="UPSTREAM_ERROR"
        if latest_run["state"] in {"PARTIAL", "FAILED", "RATE_LIMITED"}
        else "UNCONFIRMED_NO_POLICY",
        lastRunState=latest_run["state"],
        lastAttemptAt=latest_run["started_at"].isoformat(),
        sourceAt=None,
        fetchedAt=row["fetched_at"].isoformat(),
        runId=str(row["run_id"]),
        outboundId=str(row["outbound_id"]),
        proofId=str(proof.id) if proof else None,
    )
