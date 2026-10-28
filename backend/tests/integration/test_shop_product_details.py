"""商品详情真实PG原子投影；平台和单位依据均合成。"""

import json
import secrets
from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretBytes, SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.capability.service import register_binding
from yuliang.modules.commerce.shop_details import ProductMeasureProof, register_product_proof
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.native_reads import decode_verdict, native_signer
from yuliang.modules.connectors.shop_detail_protocol import (
    DETAIL_ARCHIVE_POLICY,
    DETAIL_MIRROR_POLICY,
)
from yuliang.modules.connectors.shop_product_details import ShopProductDetailSync
from yuliang.modules.connectors.sync_cursor import CursorCodec
from yuliang.modules.connectors.sync_store import SyncStore
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_capability_evidence import populate
from .test_configuration_context import configuration_ready as configuration_ready
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_platform_payloads import setup
from .test_shop_products import products as products

pytestmark = pytest.mark.integration


@pytest.fixture
async def details(products: dict[str, Any], governance: Any) -> dict[str, Any]:
    d = products
    old = d["productctx"]
    entry = next(
        r for r in d["bundle"].records if r["normalizedPath"] == "/channels/ec/product/get"
    )
    binding = d["read_binding"].model_copy(
        update=dict(
            id=uuid4(),
            account_id=old.account_id,
            connector_id="wxshop",
            provider="wxshop",
            product_line="wxshop",
            http_method="POST",
            normalized_path=entry["normalizedPath"],
            catalog_id=entry["id"],
        )
    )
    with d["maintenance"].begin() as c:
        c.execute(text("SET LOCAL ROLE app_owner"))
        register_binding(c, binding)
        app = c.execute(
            text("SELECT application_id FROM provider_authorizations WHERE id=:id"),
            dict(id=old.authorization_id),
        ).scalar_one()
    populate(d, execution=True, target=binding)
    ctx = replace(old, binding_id=binding.id, catalog_id=binding.catalog_id)
    calls = []
    state = dict(
        product=dict(
            product_id="1",
            status=6,
            skus=[dict(sku_id="11", sale_price=0, stock_num=0), dict(sku_id="12")],
        ),
        fail=False,
    )

    def upstream(req: httpx.Request) -> httpx.Response:
        calls.append(json.loads(req.content))
        return httpx.Response(
            200,
            json=dict(errcode=1) if state["fail"] else dict(errcode=0, product=state["product"]),
        )

    async def tokens(_: Any) -> SecretStr:
        return SecretStr("synthetic-token")

    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(native_signer, transport=httpx.MockTransport(upstream)),
            setup(d)[3],
            policy=DETAIL_ARCHIVE_POLICY,
            decoder=decode_verdict,
        ),
        governance,
        app,
    )
    reader = ShopProductDetailSync(
        SyncStore(
            d["engine"],
            CursorCodec(
                {1: SecretBytes(secrets.token_bytes(32))}, 1, SecretStr(secrets.token_urlsafe(48))
            ),
            policy=DETAIL_MIRROR_POLICY,
        ),
        client,
        tokens,
        (binding.normalized_path,),
        ("read",),
    )
    return d | dict(detailctx=ctx, detailreader=reader, detailstate=state, detailcalls=calls)


def proof(d: dict[str, Any]) -> ProductMeasureProof:
    ctx = d["detailctx"]
    p = ProductMeasureProof(
        id=uuid4(),
        shop_id=ctx.shop_id,
        account_id=ctx.account_id,
        binding_id=ctx.binding_id,
        price_unit="CNY_MINOR",
        stock_unit="ITEM_COUNT",
        status_mapping={"6": "RECYCLED", "5": "ON_SALE"},
        source_ref="synthetic/units",
        checked_at=d["now"] - timedelta(minutes=1),
        expires_at=d["now"] + timedelta(hours=1),
    )
    with d["maintenance"].begin() as c:
        c.execute(text("SET LOCAL ROLE app_owner"))
        register_product_proof(c, d["tenant"], p)
    return p


@pytest.mark.asyncio
async def test_unknown_units_zero_and_soft_delete_require_current_proof(
    details: dict[str, Any],
) -> None:
    d = details
    ctx = d["detailctx"]
    reader = d["detailreader"]
    run = uuid4()
    await reader.sync(ctx, run, "1")
    v = reader.view(ctx, "1")
    assert not v["deleted"] and v["state"] == "UNKNOWN"
    assert v["skus"][0]["nativeStock"] == "0" and v["skus"][0]["stock"] is None
    p = proof(d)
    v = reader.view(ctx, "1")
    assert v["deleted"] and v["skus"][0]["stock"] == 0 and v["skus"][0]["priceMinor"] == 0
    assert v["skus"][1]["stock"] is None
    await reader.sync(ctx, run, "1")
    assert len(d["detailcalls"]) == 1
    with d["maintenance"].begin() as c:
        c.execute(text("SET LOCAL ROLE app_owner"))
        c.execute(
            text("UPDATE shop_product_measure_proofs SET active=false WHERE id=:id"), dict(id=p.id)
        )
    assert not reader.view(ctx, "1")["deleted"]


@pytest.mark.asyncio
async def test_failed_or_missing_skus_never_delete_product(details: dict[str, Any]) -> None:
    d = details
    ctx = d["detailctx"]
    r = d["detailreader"]
    proof(d)
    await r.sync(ctx, uuid4(), "1")
    d["detailstate"]["fail"] = True
    with pytest.raises(ApiError):
        await r.sync(ctx, uuid4(), "1")
    assert len(r.view(ctx, "1")["skus"]) == 2
    assert r.view(ctx,"1")["freshness"]=="UPSTREAM_ERROR"
    d["detailstate"]["fail"] = False
    d["detailstate"]["product"] = dict(product_id="1", status=5)
    await r.sync(ctx, uuid4(), "1")
    v = r.view(ctx, "1")
    assert not v["deleted"] and v["skuPresence"] == "MISSING" and v["skus"] == []
    with business_transaction(d["engine"], d["identity"]) as s:
        assert s.execute(text("SELECT count(*) FROM shop_product_detail_pages")).scalar_one() == 2


@pytest.mark.asyncio
async def test_projection_fault_rolls_back_page_and_cursor(details: dict[str, Any]) -> None:
    d = details
    r = d["detailreader"]
    ctx = d["detailctx"]
    run = uuid4()
    with patch(
        "yuliang.modules.connectors.shop_product_details.project_detail",
        side_effect=ValueError("injected"),
    ):
        with pytest.raises(ValueError):
            await r.sync(ctx, run, "1")
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM sync_pages WHERE run_id=:id"), dict(id=run)
            ).scalar_one()
            == 0
        )
        assert s.execute(text("SELECT count(*) FROM shop_product_detail_pages")).scalar_one() == 0
    await r.sync(ctx, run, "1")
    assert len(d["detailcalls"]) == 2  # 新读取意图可重读幂等只读；先前原始档案保留


@pytest.mark.asyncio
async def test_shop_context_and_proof_writes_fail_closed(details: dict[str, Any]) -> None:
    d = details
    r = d["detailreader"]
    ctx = d["detailctx"]
    await r.sync(ctx, uuid4(), "1")
    with pytest.raises(ApiError):
        r.view(replace(ctx, shop_id=uuid4()), "1")
    with business_transaction(d["engine"], d["identity"]) as s:
        with pytest.raises(DBAPIError):
            s.execute(text("UPDATE shop_product_detail_pages SET payload='{}'"))


@pytest.mark.asyncio
async def test_expired_proof_and_authorized_second_shop_same_product_id(
    details: dict[str, Any],
) -> None:
    d = details
    r = d["detailreader"]
    ctx = d["detailctx"]
    await r.sync(ctx, uuid4(), "1")
    p = proof(d)
    with d["maintenance"].begin() as c:
        c.execute(text("SET LOCAL ROLE app_owner"))
        c.execute(
            text("UPDATE shop_product_measure_proofs SET active=false WHERE id=:id"), dict(id=p.id)
        )
        old = p.model_copy(
            update=dict(
                id=uuid4(),
                checked_at=d["now"] - timedelta(hours=2),
                expires_at=d["now"] - timedelta(hours=1),
            )
        )
        replacement = p.model_copy(update=dict(id=uuid4()))
        register_product_proof(c, d["tenant"], replacement)
        with pytest.raises(ValueError):
            register_product_proof(c, d["tenant"], p)
        expired = replacement.model_copy(
            update=dict(checked_at=old.checked_at, expires_at=old.expires_at)
        )
        c.execute(
            text(
                "UPDATE shop_product_measure_proofs SET payload=CAST(:payload AS jsonb), "
                "checked_at=:checked,expires_at=:expires WHERE id=:id"
            ),
            dict(
                payload=expired.model_dump_json(),
                checked=expired.checked_at,
                expires=expired.expires_at,
                id=expired.id,
            ),
        )
        other = uuid4()
        c.execute(
            text(
                "INSERT INTO shops(id,tenant_id,platform,native_id,name) "
                "VALUES(:id,:tenant,'wxshop',:native,'Synthetic')"
            ),
            dict(id=other, tenant=d["tenant"], native=str(other)),
        )
        c.execute(
            text(
                "INSERT INTO entity_links(tenant_id,shop_id,account_id) "
                "VALUES(:tenant,:shop,:account)"
            ),
            dict(tenant=d["tenant"], shop=other, account=ctx.account_id),
        )
    assert r.view(ctx, "1")["state"] == "UNKNOWN"
    assert r.view(replace(ctx, shop_id=other), "1")["state"] == "NO_OBSERVATION"
