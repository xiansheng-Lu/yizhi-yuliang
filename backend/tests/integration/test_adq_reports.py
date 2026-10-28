"""真实PG/Redis报表页和事实修订；HTTP/账户/口径依据全部合成，非真实P验收。"""

import json
import os
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretBytes, SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.capability.models import Evidence
from yuliang.modules.capability.route_sources import PROFILES, ReadRouteSource
from yuliang.modules.capability.service import record_evidence, register_binding
from yuliang.modules.connectors.adq_report_protocol import (
    REPORT_ARCHIVE_POLICY,
    REPORT_MIRROR_POLICY,
    ReportQuery,
)
from yuliang.modules.connectors.adq_reports import AdqReportSync
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.governance import PlatformGovernance
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.native_reads import decode_verdict, native_signer
from yuliang.modules.connectors.sync_cursor import CursorCodec
from yuliang.modules.connectors.sync_store import SyncConflict, SyncStore
from yuliang.modules.reporting.adq_contracts import ReportContract, register_report_contract
from yuliang.modules.reporting.service import FactWriter
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_configuration_context import configuration_ready as configuration_ready
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_read_route_sources import legacy as legacy

pytestmark = pytest.mark.integration


@pytest.fixture
def reports(legacy: dict[str, Any], governance: PlatformGovernance) -> dict[str, Any]:
    data = legacy
    bindings = {}
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assertions = (
            owner.execute(
                text("SELECT * FROM capability_assertions WHERE binding_id=:id"),
                dict(id=data["route_binding"].id),
            )
            .mappings()
            .all()
        )
        for kind, catalog in (
            ("DAILY", "e8988defa8af1a327ada"),
            ("HOURLY", "76a7c6d78a3a2fdbd4e4"),
        ):
            profile = (
                "ADQ_DAILY_REPORTS_V1_3_READ"
                if kind == "DAILY"
                else ("ADQ_HOURLY_REPORTS_V1_3_READ")
            )
            source = ReadRouteSource(
                profile=profile,
                source_sha256=PROFILES[profile].source_sha256,
                checked_by="SYNTHETIC_SOURCE_CHECK",
                checked_at=data["now"] - timedelta(minutes=1),
                expires_at=data["now"] + timedelta(hours=1),
            )
            binding = data["route_binding"].model_copy(
                update=dict(
                    id=uuid4(),
                    catalog_id=catalog,
                    catalog_sha256=data["bundle"].sha256,
                    normalized_path=PROFILES[profile].path,
                )
            )
            register_binding(owner, binding, route_source=source)
            for row in assertions:
                proof = Evidence.model_validate({k: row[k] for k in Evidence.model_fields})
                record_evidence(
                    owner,
                    binding.id,
                    proof.model_copy(
                        update=dict(
                            id=uuid4(),
                            binding_id=binding.id,
                            sequence=0,
                            proof_ref=source.proof_ref
                            if proof.phase.value == "PATH_VERIFIED"
                            else proof.proof_ref,
                        )
                    ),
                )
            bindings[kind] = binding
    contexts = {
        kind: replace(
            data["ctx"], binding_id=b.id, catalog_id=b.catalog_id, catalog_sha256=b.catalog_sha256
        )
        for kind, b in bindings.items()
    }
    with business_transaction(data["engine"], data["identity"]) as s:
        app_id = s.execute(
            text("SELECT application_id FROM provider_authorizations WHERE id=:id"),
            dict(id=data["authorization"]),
        ).scalar_one()
    calls: list[httpx.Request] = []
    state: dict[str, Any] = dict(
        cost=100, gmv=300, fail_day=None, fail_page=None, hourly=[0, 1, 2], mutate=None, empty=False
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.params["account_id"] == "12345"
        assert request.url.params["level"] == "REPORT_LEVEL_ADVERTISER"
        assert "group_by" not in request.url.params
        window = json.loads(request.url.params["date_range"])
        day = window["start_date"]
        assert day == window["end_date"]
        page, size = int(request.url.params["page"]), int(request.url.params["page_size"])
        if day == state["fail_day"] or page == state["fail_page"]:
            return httpx.Response(200, json=dict(code=11002))
        base = dict(
            account_id=12345,
            view_count=20,
            valid_click_count=5,
            cost=state["cost"],
            order_amount=state["gmv"],
            roi=999999,
            ad_name="PRIVATE_DO_NOT_ARCHIVE",
        )
        rows = (
            [base | dict(date=day)]
            if "daily_reports" in request.url.path
            else ([base | dict(hour=hour) for hour in state["hourly"]])
        )
        if state["empty"]:
            rows = []
        body = dict(
            code=0,
            data=dict(
                list=rows[(page - 1) * size : page * size],
                page_info=dict(
                    page=page,
                    page_size=size,
                    total_number=len(rows),
                    total_page=(len(rows) + size - 1) // size,
                ),
            ),
        )
        if state["mutate"]:
            state["mutate"](body, request)
        return httpx.Response(200, json=body)

    async def token(_: Any) -> SecretStr:
        return SecretStr("synthetic-report-token")

    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(native_signer, transport=httpx.MockTransport(upstream)),
            data["archive"],
            policy=REPORT_ARCHIVE_POLICY,
            decoder=decode_verdict,
        ),
        governance,
        app_id,
    )
    audit_key = SecretStr(secrets.token_urlsafe(48))
    codec = CursorCodec({1: SecretBytes(secrets.token_bytes(32))}, 1, audit_key)
    writer = FactWriter(data["engine"], audit_key)
    reader = AdqReportSync(
        SyncStore(data["engine"], codec, policy=REPORT_MIRROR_POLICY),
        client,
        token,
        writer,
        tuple(b.normalized_path for b in bindings.values()),
        ("read",),
    )
    return data | dict(
        contexts=contexts, reader=reader, calls=calls, state=state, writer=writer, app_id=app_id
    )


def query(**changes: Any) -> ReportQuery:
    return ReportQuery.model_validate(
        dict(start_date="2026-10-24", end_date="2026-10-26", time_line="REQUEST_TIME") | changes
    )


def contract(data: dict[str, Any], kind: str = "DAILY", **changes: Any) -> ReportContract:
    ctx = data["contexts"][kind]
    proof = ReportContract.model_validate(
        dict(
            account_id=ctx.account_id,
            native_account_id=ctx.native_account_id,
            binding_id=ctx.binding_id,
            shop_id=ctx.shop_id,
            kind=kind,
            time_line="REQUEST_TIME",
            timezone="Asia/Shanghai",
            attribution="VERIFIED_WHOLE_ACCOUNT_EXCLUSIVE_SHOP",
            attribution_window="7d_click",
            refund_policy="GROSS",
            cost_unit="CNY_MINOR",
            order_amount_unit="CNY_MINOR",
            order_amount_is_gmv=True,
            hour_encoding="HOUR_OF_DAY" if kind == "HOURLY" else None,
            proof_ref="synthetic/M1-10/NOT_REAL_REPORT_CONTRACT",
            source_sha256="b" * 64,
            checked_by="SYNTHETIC_OWNER",
            checked_at=datetime.now(UTC) - timedelta(seconds=1),
            expires_at=datetime.now(UTC) + timedelta(minutes=20),
        )
        | changes
    )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert register_report_contract(owner, data["tenant"], proof) == proof.id
    return proof


def rows(data: dict[str, Any], sql: str, **params: Any) -> list[Any]:
    with business_transaction(data["engine"], data["identity"]) as s:
        return list(s.execute(text(sql), params).mappings())


async def test_daily_actual_page_fact_replay_and_overlapping_historical_revision(
    reports: dict[str, Any],
) -> None:
    d = reports
    contract(d)
    ctx, reader = d["contexts"]["DAILY"], d["reader"]
    batch = uuid4()
    result = await reader.sync(ctx, batch, query())
    assert result["state"] == "SUCCESS" and result["confirmedReadThrough"] == "2026-10-26"
    assert result["observations"] == 3 and result["observationsWithFactGaps"] == 0
    assert await reader.sync(ctx, batch, query()) == result
    assert len(d["calls"]) == 3
    facts = rows(d, "SELECT * FROM metric_facts")
    assert len(facts) == 15 and all(f["source_updated_at"] is None for f in facts)
    assert all(f["source_version"] is None for f in facts)
    assert {f["value_num"] for f in facts if f["metric_name"] == "roi"} == {3}
    assert len(rows(d, "SELECT * FROM metric_fact_revisions")) == 15
    d["state"]["gmv"] = 200
    revised = await reader.sync(ctx, uuid4(), query(start_date="2026-10-25"))
    assert revised["days"][0]["sourceId"] == result["days"][1]["sourceId"]
    facts = rows(d, "SELECT * FROM metric_facts ORDER BY fact_date,metric_name")
    assert len(facts) == 15
    roi = [f for f in facts if f["metric_name"] == "roi"]
    assert [f["value_num"] for f in roi] == [3, 2, 2]
    assert [f["revision_number"] for f in roi] == [1, 2, 2]
    trace = rows(
        d,
        """SELECT p.request_id,r.outbound_id,r.run_id,r.report_date,r.rows_digest
        FROM adq_report_pages r JOIN platform_requests p ON p.id=r.outbound_id""",
    )
    assert len(trace) == 5
    observed = reader.observations(ctx, UUID(revised["batchId"]))
    assert observed["rows"][0]["measures"]["roi"]["value"] == "2.000000"
    assert not rows(d, "SELECT * FROM metric_fact_revisions WHERE decision='CONFLICT'")
    if capture := os.environ.get("YULIANG_M110_CAPTURE_PATH"):
        Path(capture).write_text(
            json.dumps(
                dict(
                    source="SYNTHETIC_HTTP_REAL_PG_AND_REDIS",
                    livePlatformCalls=0,
                    paidCalls=0,
                    initial=result,
                    revised=revised,
                    facts=facts,
                    trace=trace,
                ),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )


async def test_missing_contract_retains_observations_and_no_fabricated_facts(
    reports: dict[str, Any],
) -> None:
    d = reports
    result = await d["reader"].sync(d["contexts"]["DAILY"], uuid4(), query())
    assert result["observationsWithFactGaps"] == 3
    assert not rows(d, "SELECT * FROM metric_facts")
    obs = rows(d, "SELECT * FROM adq_report_observations")
    assert obs[0]["raw_values"]["cost"] == "100"
    assert obs[0]["gaps"] == ["REPORT_CONTRACT_UNCONFIRMED"]
    assert (
        d["reader"].observations(d["contexts"]["DAILY"], UUID(result["batchId"]))["rows"][0][
            "measures"
        ]["roi"]["value"]
        is None
    )
    raw = rows(d, "SELECT payload FROM platform_raw_payloads WHERE phase='RESPONSE'")
    assert "PRIVATE" not in json.dumps(raw, default=str) and "999999" not in json.dumps(
        raw, default=str
    )


@pytest.mark.parametrize(
    "change",
    [
        dict(cost_unit=None),
        dict(order_amount_is_gmv=False),
        dict(order_amount=None),
        dict(cost=0),
        dict(cost=-1),
    ],
)
async def test_unknown_units_missing_gmv_and_nonpositive_cost_never_compute_roi(
    reports: dict[str, Any],
    change: dict[str, Any],
) -> None:
    d = reports
    contract_changes = {
        k: v for k, v in change.items() if k in {"cost_unit", "order_amount_is_gmv"}
    }
    contract(d, **contract_changes)
    if "order_amount" in change:
        d["state"]["gmv"] = None
    if "cost" in change:
        d["state"]["cost"] = change["cost"]
    result = await d["reader"].sync(d["contexts"]["DAILY"], uuid4(), query())
    assert result["observationsWithFactGaps"] == 3
    assert not rows(d, "SELECT * FROM metric_facts WHERE metric_name='roi'")
    revisions = rows(d, "SELECT * FROM metric_fact_revisions WHERE key_json->>'metric_name'='roi'")
    assert len(revisions) == 3 and all(r["decision"] == "MISSING" for r in revisions)
    observed = d["reader"].observations(d["contexts"]["DAILY"], UUID(result["batchId"]))
    assert observed["rows"][0]["measures"]["roi"]["value"] is None


async def test_attribution_refund_and_time_line_remain_distinct(reports: dict[str, Any]) -> None:
    d = reports
    ctx = d["contexts"]["DAILY"]
    contract(d)
    await d["reader"].sync(ctx, uuid4(), query())
    contract(d, attribution_window="1d_click", refund_policy="NET_OF_REFUND")
    await d["reader"].sync(ctx, uuid4(), query())
    contract(d, time_line="ACTIVE_TIME")
    await d["reader"].sync(ctx, uuid4(), query(time_line="ACTIVE_TIME"))
    assert len(rows(d, "SELECT * FROM metric_facts")) == 45
    keys = rows(d, "SELECT DISTINCT comparable_key FROM metric_facts")
    assert len(keys) == 9  # COUNT/MINOR/RATIO各口径；计数两指标共享可比键。


async def test_partial_watermark_resume_and_auth_rotation_restart(reports: dict[str, Any]) -> None:
    d = reports
    ctx, reader = d["contexts"]["HOURLY"], d["reader"]
    contract(d, "HOURLY")
    batch = uuid4()
    q = query(kind="HOURLY", end_date="2026-10-24")
    d["state"]["fail_page"] = 2
    with pytest.raises(ApiError):
        await reader.sync(ctx, batch, q, page_size=2)
    assert rows(d, "SELECT * FROM adq_report_batches")[0]["confirmed_through"] is None
    assert len(rows(d, "SELECT * FROM adq_report_pages")) == 1
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET version=version+1 WHERE id=:id"),
            dict(id=ctx.authorization_id),
        )
    before = len(d["calls"])
    with pytest.raises(SyncConflict, match="同步状态") as error:
        await reader.sync(ctx, batch, q, page_size=2)
    assert error.value.reason == "REPORT_BATCH_CONTEXT_CHANGED" and len(d["calls"]) == before
    d["state"]["fail_page"] = None
    d["state"]["gmv"] = 200
    result = await reader.sync(ctx, uuid4(), q, page_size=2)
    assert [r.url.params["page"] for r in d["calls"][-2:]] == ["1", "2"]
    assert result["state"] == "SUCCESS" and result["observations"] == 3
    assert len(rows(d, "SELECT * FROM sync_sources")) == 1
    assert len(rows(d, "SELECT * FROM metric_facts")) == 15
    assert {
        r["value_num"] for r in rows(d, "SELECT * FROM metric_facts WHERE metric_name='roi'")
    } == {2}


async def test_partial_batch_keeps_contiguous_watermark_then_resumes(
    reports: dict[str, Any],
) -> None:
    d = reports
    contract(d)
    ctx, batch = d["contexts"]["DAILY"], uuid4()
    d["state"]["fail_day"] = "2026-10-25"
    with pytest.raises(ApiError):
        await d["reader"].sync(ctx, batch, query())
    stored = rows(d, "SELECT * FROM adq_report_batches")[0]
    assert str(stored["confirmed_through"]) == "2026-10-24" and stored["state"] == "PARTIAL"
    d["state"]["fail_day"] = None
    result = await d["reader"].sync(ctx, batch, query())
    assert result["state"] == "SUCCESS" and len(d["calls"]) == 4
    assert len(rows(d, "SELECT * FROM adq_report_observations")) == 3


@pytest.mark.parametrize("mutation", ["account", "date", "float", "total", "duplicate"])
async def test_malformed_response_cannot_advance_page_or_facts(
    reports: dict[str, Any],
    mutation: str,
) -> None:
    d = reports
    contract(d)

    def mutate(body: Any, _: Any) -> None:
        row = body["data"]["list"][0]
        if mutation == "account":
            row["account_id"] = 999
        elif mutation == "date":
            row["date"] = "2026-10-23"
        elif mutation == "float":
            row["cost"] = 1.5
        elif mutation == "total":
            body["data"]["page_info"]["total_number"] = 2
        else:
            body["data"]["list"].append(dict(row))
            body["data"]["page_info"]["total_number"] = 2

    d["state"]["mutate"] = mutate
    with pytest.raises(ValueError):
        await d["reader"].sync(d["contexts"]["DAILY"], uuid4(), query())
    assert not rows(d, "SELECT * FROM adq_report_pages")
    assert not rows(d, "SELECT * FROM metric_facts")
    assert rows(d, "SELECT * FROM sync_runs")[0]["page_no"] == 0


async def test_unconfirmed_hour_encoding_and_empty_list_preserve_missing(
    reports: dict[str, Any],
) -> None:
    d = reports
    contract(d, "HOURLY", hour_encoding=None)
    result = await d["reader"].sync(d["contexts"]["HOURLY"], uuid4(), query(kind="HOURLY"))
    assert result["observationsWithFactGaps"] == 9 and not rows(d, "SELECT * FROM metric_facts")
    d["state"]["empty"] = True
    result = await d["reader"].sync(d["contexts"]["DAILY"], uuid4(), query())
    assert result["observations"] == 0 and result["coverageKind"] == "CONFIRMED_READ_PAGES_ONLY"


async def test_application_guard_and_proof_privileges(reports: dict[str, Any]) -> None:
    d = reports
    ctx = d["contexts"]["DAILY"]
    proof = contract(d)
    with pytest.raises(DBAPIError), business_transaction(d["engine"], d["identity"]) as s:
        s.execute(
            text("UPDATE adq_report_contracts SET active=false WHERE id=:id"), dict(id=proof.id)
        )
    d["reader"].client.application_id = uuid4()
    with pytest.raises(ApiError):
        await d["reader"].sync(ctx, uuid4(), query())
    assert not d["calls"]


async def test_multiple_shop_links_do_not_duplicate_whole_account_facts(
    reports: dict[str, Any],
) -> None:
    d = reports
    contract(d)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        another = owner.execute(
            text("""INSERT INTO shops(tenant_id,platform,native_id,name)
            VALUES(:tenant,'wxshop',:native,'Synthetic other shop') RETURNING id"""),
            dict(tenant=d["tenant"], native=uuid4().hex),
        ).scalar_one()
        owner.execute(
            text(
                "INSERT INTO entity_links(tenant_id,shop_id,account_id) "
                "VALUES(:tenant,:shop,:account)"
            ),
            dict(tenant=d["tenant"], shop=another, account=d["ctx"].account_id),
        )
    result = await d["reader"].sync(d["contexts"]["DAILY"], uuid4(), query())
    assert result["observationsWithFactGaps"] == 3
    assert not rows(d, "SELECT * FROM metric_facts")
    assert rows(d, "SELECT * FROM adq_report_observations")[0]["gaps"] == [
        "WHOLE_ACCOUNT_SHOP_ATTRIBUTION_UNCONFIRMED"
    ]


async def test_fact_failure_rolls_back_page_observation_and_checkpoint(
    reports: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d = reports
    contract(d)
    original = d["writer"].apply

    def broken(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise ValueError("synthetic projection rollback")

    monkeypatch.setattr(d["writer"], "apply", broken)
    with pytest.raises(ValueError, match="synthetic projection"):
        await d["reader"].sync(d["contexts"]["DAILY"], uuid4(), query())
    for sql in (
        "SELECT * FROM sync_pages",
        "SELECT * FROM adq_report_pages",
        "SELECT * FROM adq_report_observations",
        "SELECT * FROM metric_facts",
        "SELECT * FROM metric_fact_revisions",
    ):
        assert not rows(d, sql)
    assert rows(d, "SELECT * FROM sync_runs")[0]["page_no"] == 0
    # 出站响应独立保留，不把已发生读取伪装成未发送。
    assert len(rows(d, "SELECT * FROM platform_raw_payloads WHERE phase='RESPONSE'")) == 1


async def test_token_rotation_after_response_cannot_commit_old_version(
    reports: dict[str, Any],
) -> None:
    d = reports
    contract(d)

    def rotate(_: Any, __: Any) -> None:
        with d["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(
                text("UPDATE provider_authorizations SET version=version+1 WHERE id=:id"),
                dict(id=d["contexts"]["DAILY"].authorization_id),
            )

    d["state"]["mutate"] = rotate
    with pytest.raises(SyncConflict) as error:
        await d["reader"].sync(d["contexts"]["DAILY"], uuid4(), query())
    assert error.value.reason == "AUTHORIZATION_CHANGED"
    assert not rows(d, "SELECT * FROM adq_report_pages")
    assert not rows(d, "SELECT * FROM metric_facts")


async def test_cross_page_total_drift_keeps_prior_page_and_no_second_cursor(
    reports: dict[str, Any],
) -> None:
    d = reports
    contract(d, "HOURLY")

    def drift(body: Any, request: Any) -> None:
        if request.url.params["page"] == "2":
            body["data"]["list"].append(dict(body["data"]["list"][0], hour=3))
            body["data"]["page_info"]["total_number"] = 4

    d["state"]["mutate"] = drift
    with pytest.raises(SyncConflict) as error:
        await d["reader"].sync(
            d["contexts"]["HOURLY"],
            uuid4(),
            query(kind="HOURLY", end_date="2026-10-24"),
            page_size=2,
        )
    assert error.value.reason == "PAGINATION_CHANGED"
    assert len(rows(d, "SELECT * FROM adq_report_pages")) == 1
    assert rows(d, "SELECT * FROM sync_runs")[0]["page_no"] == 1


@pytest.mark.parametrize("invalidate", ["expiry", "revoke"])
async def test_latest_contract_invalid_does_not_fall_back(
    reports: dict[str, Any],
    invalidate: str,
) -> None:
    d = reports
    contract(d)
    latest = contract(d)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if invalidate == "revoke":
            owner.execute(
                text("UPDATE adq_report_contracts SET active=false WHERE id=:id"),
                dict(id=latest.id),
            )
        else:
            owner.execute(
                text("""UPDATE adq_report_contracts SET expires_at=clock_timestamp()
                -interval '1 second' WHERE id=:id"""),
                dict(id=latest.id),
            )
    result = await d["reader"].sync(d["contexts"]["DAILY"], uuid4(), query())
    assert result["observationsWithFactGaps"] == 3
    assert not rows(d, "SELECT * FROM metric_facts")
