"""真实PG/Redis资金与操作观察；上游/口径/主体均合成，不作E01/P验收。"""

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
from yuliang.modules.connectors.adq_finance import AdqFinanceSync
from yuliang.modules.connectors.adq_finance_protocol import (
    FINANCE_ARCHIVE_POLICY,
    FINANCE_MIRROR_POLICY,
    OperationQuery,
)
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.freshness import ResourcePolicy, configure_policy
from yuliang.modules.connectors.governance import PlatformGovernance
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.native_reads import decode_verdict, native_signer
from yuliang.modules.connectors.sync_cursor import CursorCodec
from yuliang.modules.connectors.sync_store import SyncStore
from yuliang.modules.delivery.wallet_proofs import WalletMeasureProof, register_wallet_proof
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_capability_evidence import populate
from .test_configuration_context import configuration_ready as configuration_ready
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_read_route_sources import legacy as legacy

pytestmark = pytest.mark.integration


@pytest.fixture
def finance(legacy: dict[str, Any], governance: PlatformGovernance) -> dict[str, Any]:
    data = legacy
    funds = data["route_binding"].model_copy(
        update=dict(
            id=uuid4(),
            catalog_sha256=data["bundle"].sha256,
            catalog_id="6baebcfe9466fb77197e",
            normalized_path="/v1.3/funds/get",
        )
    )
    logs = funds.model_copy(
        update=dict(
            id=uuid4(),
            catalog_id="e5a1f0279c6e45a91486",
            normalized_path="/v3.0/operation_log_list/get",
        )
    )
    source = ReadRouteSource(
        profile="ADQ_FUNDS_V1_3_READ",
        source_sha256=PROFILES["ADQ_FUNDS_V1_3_READ"].source_sha256,
        checked_by="SYNTHETIC_SOURCE_REFERENCE",
        checked_at=data["now"] - timedelta(minutes=1),
        expires_at=data["now"] + timedelta(hours=1),
    )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_binding(owner, funds, route_source=source)
        register_binding(owner, logs)
        for value in (
            owner.execute(
                text("SELECT * FROM capability_assertions WHERE binding_id=:id"),
                dict(id=data["route_binding"].id),
            )
            .mappings()
            .all()
        ):
            proof = Evidence.model_validate({k: value[k] for k in Evidence.model_fields})
            record_evidence(
                owner,
                funds.id,
                proof.model_copy(
                    update=dict(
                        id=uuid4(),
                        binding_id=funds.id,
                        sequence=0,
                        proof_ref=source.proof_ref
                        if proof.phase.value == "PATH_VERIFIED"
                        else proof.proof_ref,
                    )
                ),
            )
    populate(data, execution=True, target=logs)
    ctx = replace(
        data["ctx"],
        binding_id=funds.id,
        catalog_id=funds.catalog_id,
        catalog_sha256=funds.catalog_sha256,
    )
    logctx = replace(ctx, binding_id=logs.id, catalog_id=logs.catalog_id)
    with business_transaction(data["engine"], data["identity"]) as s:
        app_id = s.execute(
            text("SELECT application_id FROM provider_authorizations WHERE id=:id"),
            dict(id=data["authorization"]),
        ).scalar_one()
    calls: list[httpx.Request] = []
    row = dict(
        operation_object_id=42,
        operation_action="OPERATION_ACTION_TYPE_UPDATE",
        operator_platform="OPERATOR_PLATFORM_MARKETING_API",
        created_time=1720000000,
        fronted_operator="PRIVATE",
        operation_log=["PRIVATE"],
        operation_object_name="PRIVATE",
    )
    state: dict[str, Any] = dict(
        funds=[
            dict(
                fund_type="FUND_TYPE_CASH",
                balance=1000,
                realtime_cost=20,
                fund_status="FUND_STATUS_NORMAL",
            ),
            dict(fund_type="FUND_TYPE_GIFT", balance=0),
            dict(fund_type="FUND_TYPE_CREDIT_ROLL", balance=-50),
        ],
        logs=[dict(row), dict(row), dict(row, operation_object_id=43)],
        fail_page=None,
        mutate=None,
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.params["account_id"] == "12345"
        if request.url.path == "/v1.3/funds/get":
            assert not {"page", "page_size", "pagination_mode"} & set(request.url.params)
            body = dict(code=0, data=dict(list=state["funds"]))
        else:
            assert request.url.path == "/v3.0/operation_log_list/get"
            assert request.url.params["operation_object_type"] == "OPERATION_OBJECT_TYPE_ADGROUP"
            page, size = int(request.url.params["page"]), int(request.url.params["page_size"])
            if state["fail_page"] == page:
                return httpx.Response(200, json=dict(code=11002))
            rows = state["logs"]
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

    async def tokens(_: Any) -> SecretStr:
        return SecretStr("synthetic-token")

    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(native_signer, transport=httpx.MockTransport(upstream)),
            data["archive"],
            policy=FINANCE_ARCHIVE_POLICY,
            decoder=decode_verdict,
        ),
        governance,
        app_id,
    )
    codec = CursorCodec(
        {1: SecretBytes(secrets.token_bytes(32))}, 1, SecretStr(secrets.token_urlsafe(48))
    )
    reader = AdqFinanceSync(
        SyncStore(data["engine"], codec, policy=FINANCE_MIRROR_POLICY),
        client,
        tokens,
        (funds.normalized_path, logs.normalized_path),
        ("read",),
    )
    return data | dict(
        reader=reader,
        fundctx=ctx,
        logctx=logctx,
        funds_binding=funds,
        calls=calls,
        state=state,
        query=OperationQuery(
            operation_object_type="OPERATION_OBJECT_TYPE_ADGROUP",
            start_date="2026-10-01",
            end_date="2026-10-02",
        ),
    )


def unit_proof(data: dict[str, Any], source: UUID, **changes: Any) -> WalletMeasureProof:
    value = WalletMeasureProof(
        source_id=source,
        metric="BALANCE",
        source_field="native_balance",
        fund_types=("FUND_TYPE_CASH", "FUND_TYPE_GIFT"),
        currency="CNY",
        native_unit="CNY_MINOR",
        aggregation="SUM_VERIFIED_DISTINCT_FUND_TYPES",
        proof_ref="synthetic/M1-09/NOT_REAL_FINANCIAL_MAPPING",
        source_sha256="a" * 64,
        checked_by="SYNTHETIC_TEST",
        checked_at=datetime.now(UTC) - timedelta(seconds=1),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    return WalletMeasureProof.model_validate(value.model_dump() | changes)


def install(data: dict[str, Any], proof: WalletMeasureProof, *, policy: bool = True) -> None:
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if policy:
            configure_policy(
                owner,
                proof.source_id,
                ResourcePolicy(
                    resource_key="adq.wallet",
                    max_age_seconds=3600,
                    refresh_seconds=1800,
                    clock_kind="FETCHED",
                    target_mode="ACCOUNT",
                    required_fields=(("fund_type", "CODE"),),
                ),
                "synthetic/M1-09/freshness",
            )
        assert register_wallet_proof(owner, data["tenant"], proof) == proof.id


async def test_real_page_wallet_provenance_and_independent_measure_units(
    finance: dict[str, Any],
) -> None:
    data = finance
    result = await data["reader"].sync_funds(data["fundctx"], uuid4())
    assert result["state"] == "SUCCESS" and result["itemsCount"] == 3 and len(data["calls"]) == 1
    source = UUID(result["sourceId"])
    unknown = data["reader"].wallet(data["fundctx"], source)
    assert all(v["valueMinor"] is None for v in unknown["measures"].values())
    assert unknown["rawFunds"][0]["native_balance"] == "1000" and not unknown["budgetUsable"]
    install(data, unit_proof(data, source))
    wallet = data["reader"].wallet(data["fundctx"], source)
    assert wallet["measures"]["BALANCE"]["valueMinor"] == 1000
    for metric in ("IN_TRANSIT", "UNSETTLED", "CREDIT", "REALTIME_COST"):
        assert wallet["measures"][metric]["valueMinor"] is None
    rt = unit_proof(
        data,
        source,
        metric="REALTIME_COST",
        source_field="native_realtime_cost",
        fund_types=("FUND_TYPE_CASH",),
    )
    install(data, rt)
    assert (
        data["reader"].wallet(data["fundctx"], source)["measures"]["REALTIME_COST"]["valueMinor"]
        == 20
    )
    assert await data["reader"].sync_funds(data["fundctx"], UUID(result["runId"])) == result
    assert len(data["calls"]) == 1
    with business_transaction(data["engine"], data["identity"]) as s:
        trace = (
            s.execute(
                text("""SELECT p.request_id,f.outbound_id,f.run_id,f.rows_digest
          FROM adq_finance_pages f JOIN platform_requests p ON p.id=f.outbound_id""")
            )
            .mappings()
            .one()
        )
        assert trace["request_id"] == data["ctx"].request_id
    if capture := os.environ.get("YULIANG_M109_CAPTURE_PATH"):
        Path(capture).write_text(
            json.dumps(
                dict(
                    source="SYNTHETIC_HTTP_REAL_PG_SCOPE_AND_MEASURES",
                    livePlatformCalls=0,
                    paidCalls=0,
                    result=result,
                    beforeProof=unknown,
                    afterProof=data["reader"].wallet(data["fundctx"], source),
                    trace=dict(trace),
                ),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )


async def test_missing_fund_field_never_becomes_zero(finance: dict[str, Any]) -> None:
    data = finance
    result = await data["reader"].sync_funds(data["fundctx"], uuid4())
    source = UUID(result["sourceId"])
    install(
        data,
        unit_proof(
            data,
            source,
            metric="REALTIME_COST",
            source_field="native_realtime_cost",
            fund_types=("FUND_TYPE_CASH", "FUND_TYPE_GIFT"),
        ),
    )
    measure = data["reader"].wallet(data["fundctx"], source)["measures"]["REALTIME_COST"]
    assert measure["valueMinor"] is None and measure["reason"] == "REQUIRED_FUND_OR_FIELD_MISSING"


@pytest.mark.parametrize(
    "mode", ["expired", "revoked", "missing_policy", "precision", "yuan_integer"]
)
async def test_normalization_requires_current_safe_source_evidence(
    finance: dict[str, Any], mode: str
) -> None:
    data = finance
    if mode == "precision":
        data["state"]["funds"][0]["balance"] = 9007199254740993
    result = await data["reader"].sync_funds(data["fundctx"], uuid4())
    source = UUID(result["sourceId"])
    proof = unit_proof(
        data, source, native_unit="CNY_YUAN_INTEGER" if mode == "yuan_integer" else "CNY_MINOR"
    )
    install(data, proof, policy=mode != "missing_policy")
    if mode in {"expired", "revoked"}:
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            query = (
                "UPDATE adq_wallet_measure_proofs "
                "SET expires_at=clock_timestamp()-interval '1 second'"
                if mode == "expired"
                else "UPDATE adq_wallet_measure_proofs SET active=false"
            )
            owner.execute(text(query))
    measure = data["reader"].wallet(data["fundctx"], source)["measures"]["BALANCE"]
    assert (
        measure["valueMinor"] == 100000 if mode == "yuan_integer" else measure["valueMinor"] is None
    )


async def test_operation_pages_preserve_distinct_observations_and_exclude_pii(
    finance: dict[str, Any],
) -> None:
    data = finance
    run = uuid4()
    result = await data["reader"].sync_operations(data["logctx"], run, data["query"], page_size=2)
    assert (
        result["state"] == "SUCCESS" and result["itemsCount"] == 3 and result["confirmedPages"] == 2
    )
    view = data["reader"].operations(data["logctx"], UUID(result["sourceId"]), limit=2)
    assert view["complete"] and len(view["rows"]) == 2 and view["nextOffset"] == 2
    assert len({r["observation_id"] for r in view["rows"]}) == 2
    assert all(r["native_log_id"] is None and r["time_unit"] == "UNCONFIRMED" for r in view["rows"])
    assert "PRIVATE" not in json.dumps(view, default=str)
    with business_transaction(data["engine"], data["identity"]) as s:
        assert s.execute(text("SELECT count(*) FROM adq_operation_observations")).scalar_one() == 3
        assert "PRIVATE" not in str(
            s.execute(text("SELECT payload FROM platform_raw_payloads")).all()
        )
    calls = len(data["calls"])
    assert (
        await data["reader"].sync_operations(data["logctx"], run, data["query"], page_size=2)
        == result
    )
    assert len(data["calls"]) == calls


async def test_operation_failure_is_partial_then_resumes_original_run(
    finance: dict[str, Any],
) -> None:
    data = finance
    data["state"]["fail_page"] = 2
    run = uuid4()
    with pytest.raises(ApiError):
        await data["reader"].sync_operations(data["logctx"], run, data["query"], page_size=2)
    with business_transaction(data["engine"], data["identity"]) as s:
        assert (
            s.execute(text("SELECT state FROM sync_runs WHERE id=:run"), dict(run=run)).scalar_one()
            == "PARTIAL"
        )
        source = s.execute(
            text("SELECT source_id FROM sync_runs WHERE id=:run"), dict(run=run)
        ).scalar_one()
    view = data["reader"].operations(data["logctx"], source)
    assert not view["complete"] and len(view["rows"]) == 2
    data["state"]["fail_page"] = None
    before = len(data["calls"])
    assert (await data["reader"].sync_operations(data["logctx"], run, data["query"], page_size=2))[
        "state"
    ] == "SUCCESS"
    assert len(data["calls"]) == before + 1


async def test_wrong_context_revocation_and_runtime_cannot_self_grant_units(
    finance: dict[str, Any],
) -> None:
    data = finance
    result = await data["reader"].sync_funds(data["fundctx"], uuid4())
    source = UUID(result["sourceId"])
    with pytest.raises(ApiError):
        data["reader"].wallet(replace(data["fundctx"], shop_id=uuid4()), source)
    for statement in (
        "DELETE FROM adq_fund_observations",
        "UPDATE adq_operation_observations SET native_created_time='0'",
        "DELETE FROM adq_wallet_measure_proofs",
    ):
        with pytest.raises(DBAPIError), business_transaction(data["engine"], data["identity"]) as s:
            s.execute(text(statement))
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET status='REVOKED' WHERE id=:id"),
            dict(id=data["authorization"]),
        )
    calls = len(data["calls"])
    with pytest.raises(ApiError):
        data["reader"].wallet(data["fundctx"], source)
    with pytest.raises(ApiError):
        await data["reader"].sync_funds(data["fundctx"], uuid4())
    assert len(data["calls"]) == calls


@pytest.mark.parametrize(
    "fault", ["missing_list", "foreign_account", "float_amount", "duplicate_fund"]
)
async def test_failed_funds_keeps_previous_observations(
    finance: dict[str, Any], fault: str
) -> None:
    data = finance
    original = await data["reader"].sync_funds(data["fundctx"], uuid4())

    def mutate(body: dict[str, Any], request: httpx.Request) -> None:
        payload = body["data"]
        if fault == "missing_list":
            payload.pop("list")
        elif fault == "foreign_account":
            payload["list"][0]["account_id"] = 67890
        elif fault == "float_amount":
            payload["list"][0]["balance"] = 1.0
        else:
            payload["list"].append(dict(payload["list"][0]))

    data["state"]["mutate"] = mutate
    with pytest.raises(ValueError):
        await data["reader"].sync_funds(data["fundctx"], uuid4())
    view = data["reader"].wallet(data["fundctx"], UUID(original["sourceId"]))
    assert len(view["rawFunds"]) == 3 and view["rawFunds"][0]["native_balance"] == "1000"
    assert view["freshness"]["status"] == "DEGRADED"


async def test_projection_failure_is_atomic_and_resume_retains_provenance(
    finance: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yuliang.modules.connectors import adq_finance
    from yuliang.modules.delivery.finance_reads import project_finance as original

    data = finance

    def broken(*args: Any) -> None:
        original(*args)
        raise ValueError("synthetic failure after finance projection")

    monkeypatch.setattr(adq_finance, "project_finance", broken)
    run = uuid4()
    with pytest.raises(ValueError):
        await data["reader"].sync_funds(data["fundctx"], run)
    with business_transaction(data["engine"], data["identity"]) as s:
        for query in (
            "SELECT count(*) FROM adq_finance_pages",
            "SELECT count(*) FROM adq_fund_observations",
            "SELECT count(*) FROM sync_pages",
        ):
            assert s.execute(text(query)).scalar_one() == 0
    monkeypatch.setattr(adq_finance, "project_finance", original)
    assert (await data["reader"].sync_funds(data["fundctx"], run))["state"] == "SUCCESS"


async def test_proof_immutability_wrong_context_and_product_isolation(
    finance: dict[str, Any],
) -> None:
    data = finance
    result = await data["reader"].sync_funds(data["fundctx"], uuid4())
    proof = unit_proof(data, UUID(result["sourceId"]))
    install(data, proof)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert register_wallet_proof(owner, data["tenant"], proof) == proof.id
    with pytest.raises(ValueError), data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_wallet_proof(
            owner, data["tenant"], proof.model_copy(update={"native_unit": "CNY_YUAN_INTEGER"})
        )
    with pytest.raises(ApiError):
        data["reader"].wallet(data["logctx"], UUID(result["sourceId"]))
    with pytest.raises(ValueError):
        WalletMeasureProof.model_validate(proof.model_dump() | dict(currency="BEAN"))
    with business_transaction(
        data["engine"], replace(data["identity"], tenant_id=data["tenant"] + 999)
    ) as s:
        assert s.execute(text("SELECT count(*) FROM adq_fund_observations")).scalar_one() == 0
