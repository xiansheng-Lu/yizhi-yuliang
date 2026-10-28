"""Actual PG normalized sources and independent SQL; no invented dashboard HTTP."""

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from yuliang.modules.reporting.overview_models import OverviewRequest
from yuliang.modules.reporting.overview_service import OverviewService
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError

from .overview_scale import build_scale
from .test_resource_queries import capability as capability
from .test_resource_queries import configuration_ready as configuration_ready
from .test_resource_queries import contract, declare, query, request
from .test_resource_queries import database as database
from .test_resource_queries import governance as governance
from .test_resource_queries import legacy as legacy
from .test_resource_queries import login_api as login_api
from .test_resource_queries import oauth_ready as oauth_ready
from .test_resource_queries import reports as reports

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [100, 5000])
async def test_overview_all_normalized_facts_scale_sql(
    reports: dict[str, Any],
    governance: Any,
    count: int,
) -> None:
    d = reports
    value = await build_scale(d, governance, count)
    with business_transaction(d["engine"], d["identity"]) as session:
        rows = (
            session.execute(
                text("""SELECT metric_name,count(*) n,sum(value_num) total
            FROM metric_facts WHERE account_id=ANY(CAST(:accounts AS uuid[]))
            AND fact_date BETWEEN :start AND :end AND granularity='DAY' AND entity_kind='ACCOUNT'
            GROUP BY metric_name"""),
                dict(
                    accounts=[a.account_id for a in value.accounts],
                    start=value.filter.start_date,
                    end=value.filter.end_date,
                ),
            )
            .mappings()
            .all()
        )
    assert sum(r["n"] for r in rows) == count
    expected = {r["metric_name"]: r["total"] for r in rows}
    report = OverviewService(d["engine"], d["identity"]).load(value)
    assert report.totals.spend == expected["spend_minor"] == count // 5 * 100
    assert report.totals.gmv == expected["paid_gmv_minor"] == count // 5 * 300
    assert report.totals.roi == 3 and report.totals.cpm == 5000
    assert len(report.trend["data"]) == (10 if count == 100 else 100)


def selection(d: dict[str, Any]) -> OverviewRequest:
    a = request(d, d["contexts"]["DAILY"], "reports").scope
    return OverviewRequest.model_validate(
        dict(
            filter=dict(
                scope=dict(
                    shop_id=str(a.shop_id),
                    live_session_id="all",
                    connector_id="adq",
                    date=a.end,
                    timezone="Asia/Shanghai",
                ),
                startDate=a.start,
                endDate=a.end,
            ),
            accounts=[a],
        )
    )


@pytest.mark.asyncio
async def test_overview_actual_history_previous_sql_and_source_time(
    reports: dict[str, Any],
) -> None:
    d = reports
    ctx = d["contexts"]["DAILY"]
    contract(d)
    await d["reader"].sync(ctx, uuid4(), query(start_date="2026-10-21", end_date="2026-10-26"))
    declare(d, ctx, "reports")
    value = selection(d)
    result = OverviewService(d["engine"], d["identity"]).load(value)
    with business_transaction(d["engine"], d["identity"]) as s:
        rows = (
            s.execute(
                text("""SELECT metric_name,sum(value_num) n FROM metric_facts
            WHERE account_id=:account AND fact_date BETWEEN '2026-10-24' AND '2026-10-26'
            AND granularity='DAY' AND entity_kind='ACCOUNT'
            AND metric_name IN ('spend_minor','paid_gmv_minor') GROUP BY metric_name"""),
                dict(account=ctx.account_id),
            )
            .mappings()
            .all()
        )
    actual = {r["metric_name"]: int(r["n"]) for r in rows}
    assert result.totals.spend == actual["spend_minor"] == 300
    assert result.totals.gmv == actual["paid_gmv_minor"] == 900
    assert result.totals.roi == 3 and result.totals.cpm == 5000
    assert result.previous is not None and result.previous.spend == 300
    assert len(result.trend["data"]) == 3 and result.updated_at is None
    assert result.lives == [] and result.funnel["status"] == "unavailable"
    assert result.ai["completed"] == 0 and result.ai["successRate"] is None
    assert result.model_dump(mode="json", by_alias=True)["filter"]["scope"]["shop_id"] == str(
        ctx.shop_id
    )
    if target := os.getenv("YULIANG_OVERVIEW_CAPTURE"):
        Path(target).write_text(
            result.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8"
        )


@pytest.mark.asyncio
async def test_overview_missing_day_zero_latest_empty_and_revocation(
    reports: dict[str, Any],
) -> None:
    d = reports
    ctx = d["contexts"]["DAILY"]
    contract(d)
    declare(d, ctx, "reports")
    d["state"].update(cost=0, gmv=0)
    await d["reader"].sync(ctx, uuid4(), query(start_date="2026-10-24", end_date="2026-10-25"))
    service = OverviewService(d["engine"], d["identity"])
    value = selection(d)
    partial = service.load(value)
    assert partial.totals.spend is None and partial.previous is None
    assert partial.trend["data"][0]["spend"] == 0
    await d["reader"].sync(ctx, uuid4(), query(start_date="2026-10-26", end_date="2026-10-26"))
    assert service.load(value).totals.spend == 0
    d["state"]["empty"] = True
    await d["reader"].sync(ctx, uuid4(), query(start_date="2026-10-26", end_date="2026-10-26"))
    assert service.load(value).totals.spend is None
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET status='REVOKED' WHERE id=:id"),
            dict(id=ctx.authorization_id),
        )
    with pytest.raises(ApiError):
        service.load(value)
