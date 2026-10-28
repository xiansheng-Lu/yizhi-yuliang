"""真实日报来源的本期/前期/同比冻结，范围与恢复。"""

import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from yuliang.modules.analysis.repository import execute_analysis
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError

from .test_adq_reports import query
from .test_analysis_admission import (
    analysis_ready as analysis_ready,
)
from .test_analysis_admission import (
    capability as capability,
)
from .test_analysis_admission import (
    configuration_ready as configuration_ready,
)
from .test_analysis_admission import (
    database as database,
)
from .test_analysis_admission import (
    governance as governance,
)
from .test_analysis_admission import (
    legacy as legacy,
)
from .test_analysis_admission import (
    login_api as login_api,
)
from .test_analysis_admission import (
    oauth_ready as oauth_ready,
)
from .test_analysis_admission import (
    reports as reports,
)
from .test_shop_compass import compass as compass

pytestmark = pytest.mark.integration


async def load(d: dict[str, Any], start: str, end: str, cost: int, gmv: int) -> None:
    d["state"].update(cost=cost, gmv=gmv)
    await d["reader"].sync(d["contexts"]["DAILY"], uuid4(), query(start_date=start, end_date=end))


async def periods(d: dict[str, Any]) -> None:
    await load(d, "2026-09-24", "2026-09-30", 100, 200)
    await load(d, "2025-10-01", "2025-10-07", 200, 300)
    await load(d, "2026-10-01", "2026-10-07", 100, 300)


def result(d: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    task = d["analysis"].create(d["input"])
    assert execute_analysis(d["engine"], d["identity"], task.id)
    done = d["analysis"].get(task.id)
    return task, d["analysis"].results(done.result_id)[0]


@pytest.mark.asyncio
async def test_actual_previous_and_year_ago_values_points_and_frozen_source(
    analysis_ready: dict[str, Any],
) -> None:
    d = analysis_ready
    await periods(d)
    task, value = result(d)
    metrics = {m["id"]: m for m in value["metrics"]}
    assert (
        metrics["ads.roi"]["value"],
        metrics["ads.roi"]["previous"],
        metrics["ads.roi"]["yearAgo"],
    ) == (3, 2, 1.5)
    assert (metrics["ads.spend"]["previous"], metrics["ads.spend"]["yearAgo"]) == (700, 1400)
    assert value["comparison"]["previous"] == dict(startDate="2026-09-24", endDate="2026-09-30")
    assert len(value["previousPoints"]) == len(value["yearAgoPoints"]) == 7
    assert all("2026-09" not in i["evidence"] for i in value["insights"])
    with business_transaction(d["engine"], d["identity"]) as s:
        frozen = s.execute(
            text("SELECT comparisons_snapshot FROM analysis_executions WHERE task_id=:id"),
            dict(id=task.id),
        ).scalar_one()
        assert all(
            part["status"] == "complete" and len(part["facts"]) == 28 for part in frozen.values()
        )
        main_rows = s.execute(
            text("SELECT snapshot FROM analysis_executions WHERE task_id=:id"), dict(id=task.id)
        ).scalar_one()
        main_ids = {int(r["id"]) for r in main_rows}
        compared_ids = {int(r["id"]) for part in frozen.values() for r in part["facts"]}
        cited_ids = {
            int(key)
            for insight in value["insights"]
            for key in re.findall(r"fact:(\d+)@r", insight["evidence"])
        }
        assert cited_ids and cited_ids <= main_ids and cited_ids.isdisjoint(compared_ids)
    if destination := os.getenv("YULIANG_COMPARISON_CAPTURE"):
        Path(destination).write_text(
            json.dumps(dict(result=value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    await load(d, "2026-09-24", "2026-09-30", 999, 999)
    assert d["analysis"].results()[0] == value


@pytest.mark.asyncio
async def test_missing_one_day_or_metric_is_null_with_actual_reason(
    analysis_ready: dict[str, Any],
) -> None:
    d = analysis_ready
    await load(d, "2026-09-24", "2026-09-29", 100, 200)
    _, value = result(d)
    assert value["comparison"]["previous"] is None and value["previousPoints"] == []
    assert all(m["previous"] is None and "2026-09-30" in m["reason"] for m in value["metrics"])
    assert "尚未实现" not in value["coverage"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["unit", "attribution", "refund"])
async def test_cross_period_semantics_not_mixed(
    analysis_ready: dict[str, Any], change: str
) -> None:
    d = analysis_ready
    await periods(d)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        queries = {
            "unit": """UPDATE metric_facts SET unit='COUNT',currency=NULL
                WHERE fact_date BETWEEN '2026-09-24' AND '2026-09-30'
                AND metric_name='spend_minor'""",
            "attribution": """UPDATE metric_facts SET attribution_window='DIFFERENT_ATTRIBUTION'
                WHERE fact_date BETWEEN '2026-09-24' AND '2026-09-30'
                AND metric_name='spend_minor'""",
            "refund": """UPDATE metric_facts SET refund_policy='NET_OF_REFUND'
                WHERE fact_date BETWEEN '2026-09-24' AND '2026-09-30'
                AND metric_name='spend_minor'""",
        }
        owner.execute(text(queries[change]))
    _, value = result(d)
    assert value["comparison"]["previous"] is None and value["comparison"]["yearAgo"] is not None
    assert any("前期" in m["reason"] for m in value["metrics"])


@pytest.mark.asyncio
async def test_new_visible_account_does_not_expand_original_task_scope(
    analysis_ready: dict[str, Any],
) -> None:
    d = analysis_ready
    await periods(d)
    task = d["analysis"].create(d["input"])
    another = uuid4()
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO ad_accounts
            (id,tenant_id,provider,product_line,native_id,name,status)
            VALUES(:id,:tenant,'tencent','adq','987654','Synthetic new account','AUTHORIZED')"""),
            dict(id=another, tenant=d["tenant"]),
        )
        owner.execute(
            text(
                "INSERT INTO entity_links(tenant_id,shop_id,account_id) VALUES(:tenant,:shop,:id)"
            ),
            dict(tenant=d["tenant"], shop=d["shop"], id=another),
        )
    assert execute_analysis(d["engine"], d["identity"], task.id)
    with business_transaction(d["engine"], d["identity"]) as s:
        row = s.execute(
            text("SELECT snapshot,comparisons_snapshot FROM analysis_executions WHERE task_id=:id"),
            dict(id=task.id),
        ).one()
        assert all(str(another) != r["account_id"] for r in row.snapshot)
        assert all(
            str(another) != r["account_id"]
            for part in row.comparisons_snapshot.values()
            for r in part["facts"]
        )


@pytest.mark.asyncio
async def test_scope_revocation_before_freeze_refuses_original_task(
    analysis_ready: dict[str, Any],
) -> None:
    d = analysis_ready
    task = d["analysis"].create(d["input"])
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:id"),
            dict(id=d["identity"].membership_id),
        )
    with pytest.raises(ApiError):
        execute_analysis(d["engine"], d["identity"], task.id)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert (
            owner.execute(
                text("SELECT count(*) FROM analysis_executions WHERE task_id=:id"), dict(id=task.id)
            ).scalar_one()
            == 0
        )


@pytest.mark.asyncio
async def test_comparison_snapshots_survive_crash_new_values_and_contract_expiry(
    analysis_ready: dict[str, Any], monkeypatch: Any
) -> None:
    from yuliang.modules.analysis import repository

    d = analysis_ready
    await periods(d)
    task = d["analysis"].create(d["input"])
    actual = repository.calculate

    def crash(*args: Any, **kwargs: Any) -> Any:
        raise SystemExit("synthetic worker loss")

    monkeypatch.setattr(repository, "calculate", crash)
    with pytest.raises(SystemExit):
        repository.execute_analysis(d["engine"], d["identity"], task.id)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE analysis_executions SET lease_until=clock_timestamp()-interval '1 second'")
        )
        owner.execute(text("UPDATE adq_report_contracts SET active=false"))
        owner.execute(text("UPDATE metric_facts SET value_num=999 WHERE unit='MINOR'"))
    monkeypatch.setattr(repository, "calculate", actual)
    assert repository.execute_analysis(d["engine"], d["identity"], task.id)
    value = d["analysis"].results()[0]
    roi = next(m for m in value["metrics"] if m["id"] == "ads.roi")
    assert (roi["value"], roi["previous"], roi["yearAgo"]) == (3, 2, 1.5)


@pytest.mark.asyncio
async def test_missing_required_dimension_metric_is_not_zero(
    analysis_ready: dict[str, Any],
) -> None:
    d = analysis_ready
    await periods(d)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""UPDATE metric_facts SET metric_name='unconfirmed_clicks'
            WHERE metric_name='clicks' AND fact_date='2026-09-28'""")
        )
    _, value = result(d)
    assert value["comparison"]["previous"] is None
    assert all(m["previous"] is None and "clicks" in m["reason"] for m in value["metrics"])
    assert value["comparison"]["yearAgo"] is not None


@pytest.mark.asyncio
async def test_legacy_snapshot_explicitly_does_not_invent_comparisons(
    analysis_ready: dict[str, Any], monkeypatch: Any
) -> None:
    from yuliang.modules.analysis import repository

    d = analysis_ready
    await periods(d)
    task = d["analysis"].create(d["input"])
    actual = repository.calculate

    def crash(*args: Any, **kwargs: Any) -> Any:
        raise SystemExit("synthetic old worker")

    monkeypatch.setattr(repository, "calculate", crash)
    with pytest.raises(SystemExit):
        repository.execute_analysis(d["engine"], d["identity"], task.id)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""UPDATE analysis_executions SET comparisons_snapshot=NULL,
            lease_until=clock_timestamp()-interval '1 second'""")
        )
        owner.execute(text("UPDATE adq_report_contracts SET active=false"))
        owner.execute(text("UPDATE metric_facts SET value_num=999 WHERE unit='MINOR'"))
    monkeypatch.setattr(repository, "calculate", actual)
    assert repository.execute_analysis(d["engine"], d["identity"], task.id)
    value = d["analysis"].results()[0]
    roi = next(m for m in value["metrics"] if m["id"] == "ads.roi")
    assert roi["value"] == 3 and roi["previous"] is None and roi["yearAgo"] is None
    assert "旧在途快照" in value["coverage"]
    with business_transaction(d["engine"], d["identity"]) as s:
        frozen = s.execute(
            text("SELECT comparisons_snapshot FROM analysis_executions WHERE task_id=:id"),
            dict(id=task.id),
        ).scalar_one()
        assert all(
            p["status"] == "NOT_CAPTURED_LEGACY" and p["facts"] == [] for p in frozen.values()
        )


@pytest.mark.asyncio
async def test_stage40_uses_one_repeatable_read_for_all_three_periods(
    analysis_ready: dict[str, Any], monkeypatch: Any
) -> None:
    from yuliang.modules.analysis import comparisons

    d = analysis_ready
    await periods(d)
    actual = comparisons.comparison_preflight

    def change_after_preflight(*args: Any, **kwargs: Any) -> Any:
        verified = actual(*args, **kwargs)
        with d["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(text("UPDATE metric_facts SET value_num=999 WHERE unit='MINOR'"))
        return verified

    monkeypatch.setattr(comparisons, "comparison_preflight", change_after_preflight)
    _, value = result(d)
    metric = next(m for m in value["metrics"] if m["id"] == "ads.roi")
    assert (metric["value"], metric["previous"], metric["yearAgo"]) == (3, 2, 1.5)
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT min(value_num) FROM metric_facts WHERE unit='MINOR'")
            ).scalar_one()
            == 999
        )


@pytest.mark.asyncio
async def test_actual_sql_and_python_leap_day_windows_agree(analysis_ready: dict[str, Any]) -> None:
    from yuliang.modules.analysis.models import AnalysisCreate

    d = analysis_ready
    await load(d, "2024-02-28", "2024-02-28", 100, 200)
    await load(d, "2023-02-28", "2023-02-28", 200, 300)
    await load(d, "2024-02-29", "2024-02-29", 100, 300)
    d["input"] = AnalysisCreate.model_validate(
        d["input"].model_dump()
        | dict(
            query=d["input"].query.model_dump()
            | dict(start_date="2024-02-29", end_date="2024-02-29")
        )
    )
    _, value = result(d)
    assert value["comparison"]["previous"] == dict(startDate="2024-02-28", endDate="2024-02-28")
    assert value["comparison"]["yearAgo"] == dict(startDate="2023-02-28", endDate="2023-02-28")
    roi = next(m for m in value["metrics"] if m["id"] == "ads.roi")
    assert (roi["value"], roi["previous"], roi["yearAgo"]) == (3, 2, 1.5)


@pytest.mark.asyncio
async def test_actual_compass_business_metrics_have_real_comparisons(
    compass: dict[str, Any],
) -> None:
    from datetime import UTC, date, datetime, timedelta

    import httpx

    from yuliang.modules.analysis.models import AnalysisCreate
    from yuliang.modules.analysis.service import AnalysisService
    from yuliang.modules.commerce.compass_contracts import (
        CompassContract,
        register_compass_contract,
    )

    d = compass
    ctx = d["productctx"]
    now = datetime.now(UTC)
    proof = CompassContract(
        account_id=ctx.account_id,
        native_account_id=ctx.native_account_id,
        binding_id=ctx.binding_id,
        shop_id=ctx.shop_id,
        attribution="VERIFIED_WHOLE_SHOP",
        timezone="Asia/Shanghai",
        money_unit="CNY_MINOR",
        refund_policy="GROSS",
        dedup_policy="PLATFORM_DEDUPLICATED_ORDERS",
        definition_key="synthetic.business.daily",
        pay_gmv_is_business_gmv=True,
        proof_ref="synthetic/comparison/contract",
        source_sha256="a" * 64,
        checked_by="SYNTHETIC_TEST",
        checked_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(hours=1),
    )
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_compass_contract(owner, d["tenant"], proof)
        owner.execute(text("UPDATE shops SET platform='wxshop' WHERE id=:id"), dict(id=d["shop"]))

    def response(request: Any) -> Any:
        ds = json.loads(request.content)["ds"]
        gmv = {"20261001": 1000, "20260930": 600, "20251001": 500}[ds]
        return httpx.Response(
            200, json=dict(errcode=0, data=dict(pay_gmv=gmv, pay_order_cnt=2, pay_refund_gmv=100))
        )

    d["reader"].client.client.client.transport = httpx.MockTransport(response)
    for day in (date(2026, 10, 1), date(2026, 9, 30), date(2025, 10, 1)):
        await d["reader"].sync(ctx, uuid4(), day=day)
    service = AnalysisService(d["engine"], d["identity"])
    value = AnalysisCreate.model_validate(
        dict(
            query=dict(
                shopId=str(d["shop"]),
                platformId="wxshop",
                startDate="2026-10-01",
                endDate="2026-10-01",
                dimensions=["shop"],
                timezone="Asia/Shanghai",
            ),
            idempotencyKey=str(uuid4()),
        )
    )
    task = service.create(value)
    assert execute_analysis(d["engine"], d["identity"], task.id)
    done = service.get(task.id)
    result = service.results(done.result_id)[0]
    metrics = {m["id"]: m for m in result["metrics"]}
    assert (
        metrics["shop.gmv"]["value"],
        metrics["shop.gmv"]["previous"],
        metrics["shop.gmv"]["yearAgo"],
    ) == (1000, 600, 500)
    assert (
        metrics["shop.aov"]["value"],
        metrics["shop.aov"]["previous"],
        metrics["shop.aov"]["yearAgo"],
    ) == (500, 300, 250)
    assert result["completeness"] == "complete" and all(
        m["dimension"] == "shop" for m in result["metrics"]
    )
