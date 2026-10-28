"""V9资源queries真实PG/HTTP、真实0与缺口、现有TS消费。"""

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from yuliang.modules.configuration.integration_evidence import register_operation
from yuliang.modules.configuration.integration_models import OperationDeclaration
from yuliang.modules.configuration.integration_service import IntegrationService
from yuliang.modules.reporting.query_models import QueryRequest
from yuliang.modules.reporting.query_service import QueryService
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_adq_reports import (
    capability as capability,
)
from .test_adq_reports import (
    configuration_ready as configuration_ready,
)
from .test_adq_reports import (
    contract,
    query,
)
from .test_adq_reports import (
    database as database,
)
from .test_adq_reports import (
    governance as governance,
)
from .test_adq_reports import (
    legacy as legacy,
)
from .test_adq_reports import (
    login_api as login_api,
)
from .test_adq_reports import (
    oauth_ready as oauth_ready,
)
from .test_adq_reports import (
    reports as reports,
)
from .test_password_sessions import challenge
from .test_shop_compass import compass as compass
from .test_shop_product_details import details as details
from .test_shop_product_details import products as products
from .test_shop_product_details import proof

pytestmark = pytest.mark.integration
PATH = "/v1/delivery/integrations/queries"


@pytest.mark.asyncio
async def test_compass_observation_without_business_contract_stays_unknown(
    compass: dict[str, Any],
) -> None:
    d = compass
    ctx = d["productctx"]
    await d["reader"].sync(ctx, uuid4(), day=date(2026, 10, 25))
    declare(d, ctx, "compass")
    page = QueryService(d["engine"], d["identity"]).query(request(d, ctx, "compass"))
    assert len(page.rows) == 1
    assert page.rows[0].metrics == {"paidGmvMinor": None, "paidOrders": None, "refundMinor": None}
    assert page.rows[0].status == "PARTIAL"


def declare(d: dict[str, Any], ctx: Any, resource: str) -> None:
    now = datetime.now(UTC)
    declaration = OperationDeclaration(
        binding_id=ctx.binding_id,
        binding_version=ctx.capability_version,
        resource=resource,
        authorization_id=ctx.authorization_id,
        authorization_version=1,
        required_scopes=("read",),
        proof_ref="synthetic/M1-19/resource",
        checked_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=10),
    )
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE ad_accounts SET entity_id=:entity WHERE id=:account"),
            dict(entity=d["context"].business_entity_id, account=ctx.account_id),
        )
        register_operation(owner, d["tenant"], declaration)


def request(d: dict[str, Any], ctx: Any, resource: str) -> QueryRequest:
    return QueryRequest.model_validate(
        dict(
            scope=dict(
                tenantId=str(d["tenant"]),
                businessEntityId=str(d["context"].business_entity_id),
                shopId=str(d["shop"]),
                provider=ctx.provider,
                productLine=ctx.product_line,
                accountId=str(ctx.account_id),
                start="2026-10-24",
                end="2026-10-26",
            ),
            resource=resource,
            filters={},
        )
    )


@pytest.mark.asyncio
async def test_reports_http_real_zero_unknown_and_independent_sql(reports: dict[str, Any]) -> None:
    d = reports
    ctx = d["contexts"]["DAILY"]
    contract(d)
    d["state"].update(cost=0, gmv=0)
    await d["reader"].sync(ctx, uuid4(), query())
    declare(d, ctx, "reports")
    value = request(d, ctx, "reports")
    response = d["client"].post(
        PATH,
        json=value.model_dump(mode="json", by_alias=True),
        headers={"X-CSRF-Token": challenge(d["client"])},
    )
    assert response.status_code == 200, response.text
    page = response.json()["data"]
    assert len(page["rows"]) == 3 and page["scopeKey"] == value.scope.key()
    assert all(
        r["metrics"]["spendMinor"] == 0
        and r["metrics"]["attributedGmvMinor"] == 0
        and r["metrics"]["roi"] is None
        for r in page["rows"]
    )
    assert all(r["updatedAt"] == "源更新时间未提供" and r["version"] is None for r in page["rows"])
    with business_transaction(d["engine"], d["identity"]) as s:
        raw = s.execute(
            text(
                "SELECT metric_name,sum(value_num) FROM metric_facts "
                "WHERE account_id=:id AND unit='MINOR' GROUP BY metric_name"
            ),
            dict(id=ctx.account_id),
        ).all()
        totals = {r[0]: r[1] for r in raw}
    assert sum(r["metrics"]["spendMinor"] for r in page["rows"]) == totals["spend_minor"]
    if destination := os.getenv("YULIANG_RESOURCE_QUERY_CAPTURE"):
        Path(destination).write_text(
            json.dumps(
                dict(
                    page=page,
                    input=value.model_dump(mode="json", by_alias=True),
                    context=IntegrationService(d["engine"], d["identity"])
                    .context("adq")
                    .model_dump(mode="json", by_alias=True),
                    identity=d["client"].get("/v1/auth/session").json()["data"],
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


@pytest.mark.asyncio
async def test_reports_unknown_contract_not_zero_and_read_scope_rechecks(
    reports: dict[str, Any],
) -> None:
    d = reports
    ctx = d["contexts"]["DAILY"]
    await d["reader"].sync(ctx, uuid4(), query())
    declare(d, ctx, "reports")
    service = QueryService(d["engine"], d["identity"])
    value = request(d, ctx, "reports")
    page = service.query(value)
    assert all(r.metrics["spendMinor"] is None for r in page.rows)
    assert page.warnings
    for field, new in [
        ("tenant_id", "999999999"),
        ("shop_id", uuid4()),
        ("account_id", str(uuid4())),
        ("business_entity_id", uuid4()),
    ]:
        changed = value.model_copy(update=dict(scope=value.scope.model_copy(update={field: new})))
        with pytest.raises(ApiError) as error:
            service.query(changed)
        assert error.value.code == ErrorCode.FORBIDDEN
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET status='REVOKED' WHERE id=:id"),
            dict(id=ctx.authorization_id),
        )
    with pytest.raises(ApiError):
        service.query(value)


@pytest.mark.asyncio
async def test_missing_observation_is_unavailable_not_empty_success(
    reports: dict[str, Any],
) -> None:
    d = reports
    ctx = d["contexts"]["DAILY"]
    declare(d, ctx, "reports")
    with pytest.raises(ApiError) as error:
        QueryService(d["engine"], d["identity"]).query(request(d, ctx, "reports"))
    assert error.value.code == ErrorCode.CAPABILITY_UNAVAILABLE


@pytest.mark.asyncio
async def test_products_and_sku_minimum_fields_need_independent_proof(
    details: dict[str, Any],
) -> None:
    d = details
    ctx = d["detailctx"]
    await d["detailreader"].sync(ctx, uuid4(), "1")
    declare(d, ctx, "products")
    declare(d, ctx, "stock")
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO commerce_products(id,tenant_id,shop_id,account_id,native_id) "
                "VALUES(:id,:tenant,:shop,:account,'999')"
            ),
            dict(id=uuid4(), tenant=d["tenant"], shop=ctx.shop_id, account=ctx.account_id),
        )
    service = QueryService(d["engine"], d["identity"])
    products_page = service.query(request(d, ctx, "products"))
    assert len(products_page.rows) == 1
    assert any("999:DETAIL_NOT_OBSERVED" in warning for warning in products_page.warnings)
    assert products_page.rows[0].metrics["stock"] is None
    assert products_page.rows[0].metrics["itemCount"] == 2
    proof(d)
    stocks = service.query(request(d, ctx, "stock"))
    assert stocks.rows[0].metrics["stock"] == 0 and stocks.rows[1].metrics["stock"] is None
    assert all(r.updated_at == "源更新时间未提供" and r.version is None for r in stocks.rows)
    assert all(set(r.metrics) == {"stock"} for r in stocks.rows)
    assert "PRIVATE" not in stocks.model_dump_json()
    other_shop = uuid4()
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO shops(id,tenant_id,name,entity_id,platform,native_id) "
                "VALUES(:id,:tenant,'Other',:entity,'wxshop','other-shop')"
            ),
            dict(id=other_shop, tenant=d["tenant"], entity=d["context"].business_entity_id),
        )
        owner.execute(
            text("INSERT INTO entity_links(tenant_id,shop_id,account_id) VALUES(:tenant,:shop,:a)"),
            dict(tenant=d["tenant"], shop=other_shop, a=ctx.account_id),
        )
    altered = request(d, ctx, "products")
    altered = altered.model_copy(
        update={"scope": altered.scope.model_copy(update={"shop_id": other_shop})}
    )
    with pytest.raises(ApiError):
        service.query(altered)


@pytest.mark.asyncio
async def test_latest_empty_report_does_not_backfill_old_confirmed_values(
    reports: dict[str, Any],
) -> None:
    d = reports
    ctx = d["contexts"]["DAILY"]
    contract(d)
    await d["reader"].sync(ctx, uuid4(), query())
    declare(d, ctx, "reports")
    actor = QueryService(d["engine"], d["identity"])
    value = request(d, ctx, "reports")
    assert actor.query(value).rows[0].metrics["attributedGmvMinor"] == 300
    d["state"]["empty"] = True
    await d["reader"].sync(ctx, uuid4(), query())
    page = actor.query(value)
    assert all(
        r.metrics["attributedGmvMinor"] is None and r.metrics["spendMinor"] is None
        for r in page.rows
    )
    assert page.warnings
