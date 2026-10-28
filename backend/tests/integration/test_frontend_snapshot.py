"""真实PG/HTTP范围持久化与完整数组；缺少必填规范来源必须明确拒绝。"""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.snapshot.models import COLLECTIONS
from yuliang.modules.snapshot.service import SnapshotService
from yuliang.platform.db import business_transaction

from .test_configuration_context import capability as capability
from .test_configuration_context import configuration_ready as configuration_ready
from .test_configuration_context import database as database
from .test_configuration_context import login_api as login_api
from .test_configuration_context import oauth_ready as oauth_ready
from .test_shop_product_details import details as details
from .test_shop_product_details import governance as governance
from .test_shop_product_details import products as products

pytestmark = pytest.mark.integration


@pytest.fixture
async def snapshot(configuration_ready: dict[str, Any]) -> dict[str, Any]:
    d = configuration_ready
    return d | dict(snapshot=SnapshotService(d["engine"], d["identity"]))


def csrf(d: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": d["client"].get("/v1/auth/csrf").json()["data"]["token"]}


def test_snapshot_full_directory_revision_scope_and_actual_http(snapshot: dict[str, Any]) -> None:
    d = snapshot
    c = d["client"]
    first = c.get("/v1/frontend/snapshot")
    assert first.status_code == 200, first.text
    value = first.json()["data"]
    assert all(isinstance(value[k], list) for k in COLLECTIONS)
    assert value["source"] == "API" and value["schemaVersion"] == "command-job-v1.0"
    assert value["shops"][0]["id"] == str(d["shop"])
    assert value["adAccounts"][0]["id"] == str(d["read_binding"].account_id)
    assert c.get("/v1/frontend/snapshot").json()["data"]["revision"] == value["revision"]
    selected = value["scope"] | dict(shop_id=str(d["shop"]), date="2026-10-28")
    changed = c.put("/v1/frontend/reporting-scope", json=selected, headers=csrf(d))
    assert changed.status_code == 200 and changed.json()["data"] is None
    second = c.get("/v1/frontend/snapshot").json()["data"]
    assert second["scope"] == selected and second["revision"] > value["revision"]
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE ad_accounts SET name='Updated real directory' WHERE id=:id"),
            dict(id=d["read_binding"].account_id),
        )
    assert c.get("/v1/frontend/snapshot").json()["data"]["revision"] > second["revision"]
    evidence = Path(__file__).resolve().parents[3] / ".runtime/snapshot-frontend-fixture.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(second), encoding="utf-8")


def test_snapshot_scope_rejects_csrf_foreign_objects_and_identity_override(
    snapshot: dict[str, Any],
) -> None:
    d = snapshot
    c = d["client"]
    value = c.get("/v1/frontend/snapshot").json()["data"]["scope"]
    assert c.put("/v1/frontend/reporting-scope", json=value).status_code == 403
    assert (
        c.put(
            "/v1/frontend/reporting-scope", json=value | dict(shop_id=str(uuid4())), headers=csrf(d)
        ).status_code
        == 403
    )
    assert (
        c.put(
            "/v1/frontend/reporting-scope", json=value | dict(tenant_id="other"), headers=csrf(d)
        ).status_code
        == 422
    )
    assert c.get("/v1/frontend/snapshot?tenant_id=other").status_code == 422


def test_snapshot_unknown_plan_fields_are_explicit_coverage_gap(snapshot: dict[str, Any]) -> None:
    d = snapshot
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                """INSERT INTO campaigns(id,tenant_id,shop_id,account_id,
                provider,product_line,native_id)
                VALUES(:id,:tenant,:shop,:account,'tencent','adq','synthetic-plan')"""
            ),
            dict(
                id=uuid4(), tenant=d["tenant"], shop=d["shop"], account=d["read_binding"].account_id
            ),
        )
    response = d["client"].get("/v1/frontend/snapshot")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DATA_COVERAGE_INSUFFICIENT"
    assert "REQUIRED_PLAN_" in response.text
    assert '"plans":[]' not in response.text


def test_snapshot_oversized_array_is_explicit_not_truncated(snapshot: dict[str, Any]) -> None:
    d = snapshot
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO shops(tenant_id,platform,native_id,name)
          SELECT :tenant,'wxshop','oversize-'||g,'Synthetic size boundary'
          FROM generate_series(1,10000) g"""),
            dict(tenant=d["tenant"]),
        )
    response = d["client"].get("/v1/frontend/snapshot")
    assert response.status_code == 422 and "EXCEEDS_10000" in response.text
    narrow = dict(
        shop_id=str(d["shop"]),
        live_session_id="all",
        connector_id="all",
        date="2026-10-28",
        timezone="Asia/Shanghai",
    )
    assert (
        d["client"].put("/v1/frontend/reporting-scope", json=narrow, headers=csrf(d)).status_code
        == 200
    )
    result = d["client"].get("/v1/frontend/snapshot")
    assert result.status_code == 200, result.text
    assert len(result.json()["data"]["shops"]) == 1


def test_snapshot_state_rls_cannot_write_other_user(snapshot: dict[str, Any]) -> None:
    d = snapshot
    d["snapshot"].get()
    with pytest.raises(DBAPIError):
        with business_transaction(d["engine"], d["identity"]) as s:
            s.execute(text("UPDATE frontend_snapshot_states SET user_id=:user"), dict(user=uuid4()))


def test_snapshot_clips_current_member_shop_and_account_scopes(snapshot: dict[str, Any]) -> None:
    d = snapshot
    hidden_shop = uuid4()
    hidden_account = uuid4()
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO shops(id,tenant_id,platform,native_id,name)
          VALUES(:id,:tenant,'wxshop','hidden-shop','Hidden shop')"""),
            dict(id=hidden_shop, tenant=d["tenant"]),
        )
        owner.execute(
            text("""INSERT INTO ad_accounts(id,tenant_id,provider,product_line,native_id,name)
          VALUES(:id,:tenant,'tencent','adq','hidden-account','Hidden account')"""),
            dict(id=hidden_account, tenant=d["tenant"]),
        )
        owner.execute(
            text(
                """INSERT INTO entity_links(tenant_id,shop_id,account_id)
                VALUES(:tenant,:shop,:account)"""
            ),
            dict(tenant=d["tenant"], shop=d["shop"], account=hidden_account),
        )
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:member"),
            dict(member=d["identity"].membership_id),
        )
        owner.execute(
            text("""INSERT INTO data_scopes(tenant_id,membership_id,scope_type,shop_id)
          VALUES(:tenant,:member,'SHOP',:shop)"""),
            dict(tenant=d["tenant"], member=d["identity"].membership_id, shop=d["shop"]),
        )
        owner.execute(
            text("""INSERT INTO data_scopes(tenant_id,membership_id,scope_type,account_id)
          VALUES(:tenant,:member,'ACCOUNT',:account)"""),
            dict(
                tenant=d["tenant"],
                member=d["identity"].membership_id,
                account=d["read_binding"].account_id,
            ),
        )
    value = d["snapshot"].get()
    assert [s["id"] for s in value["shops"]] == [str(d["shop"])]
    assert [a["id"] for a in value["adAccounts"]] == [str(d["read_binding"].account_id)]
    assert "Hidden" not in json.dumps(value)


@pytest.mark.asyncio
async def test_snapshot_real_product_projection_requires_confirmed_unit(
    details: dict[str, Any],
) -> None:
    from .test_shop_product_details import proof

    d = details
    d["detailstate"]["product"] = dict(
        product_id="1", status=5, skus=[dict(sku_id="11", sale_price=0, stock_num=0)]
    )
    await d["detailreader"].sync(d["detailctx"], uuid4(), "1")
    product = uuid4()
    sku = uuid4()
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO products(id,tenant_id,shop_id,native_id,name)
          VALUES(:id,:tenant,:shop,'1','Actual named catalog product')"""),
            dict(id=product, tenant=d["tenant"], shop=d["shop"]),
        )
        owner.execute(
            text("""INSERT INTO skus(id,tenant_id,product_id,native_id)
            VALUES(:id,:tenant,:product,'11')"""),
            dict(id=sku, tenant=d["tenant"], product=product),
        )
    service = SnapshotService(d["engine"], d["identity"])
    from yuliang.platform.errors import ApiError

    with pytest.raises(ApiError) as unknown:
        service.get()
    assert unknown.value.code == "DATA_COVERAGE_INSUFFICIENT"
    proof(d)
    value = service.get()
    assert len(value["products"]) == 1
    item = value["products"][0]
    assert item["id"] == str(product) and item["skuId"] == str(sku)
    assert item["priceMinor"] == 0 and item["stock"] == 0
    assert item["clicks"] is None and item["paidOrders"] is None
