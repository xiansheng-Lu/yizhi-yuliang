"""真实PG/Redis+合成HTTP，非真实平台或G1验收。"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from yuliang.modules.connectors.plan_protocol import POLICY
from yuliang.modules.connectors.plan_sync import AdqPlanSync
from yuliang.modules.connectors.sync_store import SyncConflict, SyncStore
from yuliang.modules.delivery.plan_proofs import PlanMeasureProof
from yuliang.modules.delivery.plan_reads import read_plan_observations, register_plan_proof
from yuliang.platform.db import business_transaction

from .test_adq_asset_mirrors import assets as assets
from .test_capability_evidence import capability as capability
from .test_configuration_context import configuration_ready as configuration_ready
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_read_route_sources import legacy as legacy

pytestmark = pytest.mark.integration


@pytest.fixture
def plans(assets: dict[str, Any]) -> dict[str, Any]:
    d = assets
    original = d["reader"]
    d["reader"] = AdqPlanSync(
        SyncStore(d["engine"], original.store.codec, policy=POLICY),
        original.client,
        original.tokens,
        ("/v1.3/campaigns/get",),
        ("read",),
    )
    for row in d["state"]["rows"]:
        row.update(total_budget=0, created_time=1, last_modified_time=1, is_deleted=False)
    return d


def read(d: dict[str, Any]) -> dict[str, Any]:
    with business_transaction(d["engine"], d["identity"]) as session:
        return read_plan_observations(
            session, d["ctx"], "CAMPAIGN", d["reader"].client.application_id, frozenset({"read"})
        )


async def test_complete_pages_observations_no_proof_no_fake_values(plans: dict[str, Any]) -> None:
    d = plans
    assert read(d)["status"] == "not_connected"
    run = uuid4()
    result = await d["reader"].sync(d["ctx"], "CAMPAIGN", run, page_size=2)
    assert result["state"] == "SUCCESS" and len(d["calls"]) == 2
    await d["reader"].sync(d["ctx"], "CAMPAIGN", run, page_size=2)
    assert len(d["calls"]) == 2
    view = read(d)
    assert view["status"] == "coverage_error" and view["normalized"] == []
    assert view["gaps"] == ["PLAN_MEASURE_PROOF_MISSING"]
    assert len(view["observations"]) == 3


async def test_campaign_proof_cannot_invent_bid(plans: dict[str, Any]) -> None:
    d = plans
    await d["reader"].sync(d["ctx"], "CAMPAIGN", uuid4(), page_size=2)
    now = datetime.now(UTC)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        source = owner.execute(
            text("""SELECT id FROM sync_sources WHERE resource_kind='adq.plans.campaign'
                    AND account_id=:account"""),
            dict(account=d["ctx"].account_id),
        ).scalar_one()
        evidence = dict(ref="synthetic-plan-proof", sha256="a" * 64)
        proof = PlanMeasureProof(
            source_id=source,
            binding_version=d["ctx"].capability_version,
            version=1,
            level="CAMPAIGN",
            currency="CNY",
            budget_field="daily_budget",
            budget_unit="CNY_MINOR",
            bid_unit=None,
            roi_scale=None,
            source_epoch="SECONDS",
            source_timezone="UTC",
            status_map={"AD_STATUS_NORMAL": "RUNNING", "AD_STATUS_SUSPEND": "PAUSED"},
            evidence={k: evidence for k in ("budget", "currency", "source_time", "status")},
            checked_by="SYNTHETIC",
            checked_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(hours=1),
            max_age_seconds=3600,
        )
        register_plan_proof(owner, d["tenant"], proof)
        assert register_plan_proof(owner, d["tenant"], proof) == proof.id
    view = read(d)
    assert view["normalized"] == []
    assert "CAMPAIGN_HAS_NO_OFFICIAL_BID_FIELD_FOR_FRONTEND_REQUIRED_BID" in view["gaps"]


@pytest.mark.parametrize("failure", ["drift", "duplicate", "platform"])
async def test_partial_run_never_returned_as_complete(plans: dict[str, Any], failure: str) -> None:
    d = plans

    def mutate(body: dict[str, Any], page: int) -> None:
        if page == 2:
            if failure == "drift":
                body["data"]["page_info"]["total_number"] = 4
                body["data"]["list"].append(dict(d["state"]["rows"][-1], campaign_id=44))
            elif failure == "duplicate":
                body["data"]["list"][0]["campaign_id"] = d["state"]["rows"][0]["campaign_id"]

    d["state"]["mutate"] = mutate
    if failure == "platform":
        d["state"]["fail_page"] = 2
    with pytest.raises((ValueError, SyncConflict)):
        await d["reader"].sync(d["ctx"], "CAMPAIGN", uuid4(), page_size=2)
    assert read(d)["status"] == "error"


async def test_confirmed_empty_is_distinct(plans: dict[str, Any]) -> None:
    d = plans
    d["state"]["rows"] = []
    await d["reader"].sync(d["ctx"], "CAMPAIGN", uuid4(), page_size=2)
    assert read(d) == dict(status="empty", observations=[], normalized=[], gaps=[])


@pytest.fixture
def adgroups(plans: dict[str, Any]) -> dict[str, Any]:
    from yuliang.modules.capability.route_sources import PROFILES, ReadRouteSource
    from yuliang.modules.capability.sdk_catalog import import_sdk_read_catalog
    from yuliang.modules.capability.service import register_binding
    from yuliang.modules.connectors.archive import RecordedPlatformClient
    from yuliang.modules.connectors.governed import GovernedPlatformClient
    from yuliang.modules.connectors.http import PlatformHttpClient
    from yuliang.modules.connectors.native_reads import decode_verdict, native_signer
    from yuliang.modules.connectors.plan_protocol import ARCHIVE

    d = plans
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        catalog = import_sdk_read_catalog(owner, "ADQ_ADGROUPS_V1_3_READ")
        binding = d["route_binding"].model_copy(
            update=dict(
                id=uuid4(),
                catalog_id=catalog["catalogId"],
                catalog_sha256=catalog["catalogSha256"],
                normalized_path="/v1.3/adgroups/get",
            )
        )
        source = ReadRouteSource(
            profile="ADQ_ADGROUPS_V1_3_READ",
            source_sha256=PROFILES["ADQ_ADGROUPS_V1_3_READ"].source_sha256,
            checked_by="SYNTHETIC",
            checked_at=d["now"] - timedelta(minutes=1),
            expires_at=d["now"] + timedelta(hours=1),
        )
        register_binding(owner, binding, route_source=source)
    # PATH阶段须保留正式SDK source证明；其余阶段为合成测试，不是平台验收。
    from yuliang.modules.capability.models import Evidence, Phase
    from yuliang.modules.capability.service import record_evidence

    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        kinds = (
            "DOCUMENTATION",
            "SCOPE_GRANT",
            "ACCOUNT_AUTH",
            "ADAPTER_INTEGRATION",
            "READ_SCHEMA",
            "READ_RESULT",
        )
        for index, phase in enumerate(Phase):
            record_evidence(
                owner,
                binding.id,
                Evidence(
                    id=uuid4(),
                    binding_id=binding.id,
                    phase=phase,
                    result="PASS",
                    proof_kind=kinds[index],
                    proof_ref=source.proof_ref if phase == Phase.PATH else "synthetic/plan-read",
                    checked_by="SYNTHETIC",
                    checked_at=d["now"],
                    expires_at=source.expires_at,
                ),
            )
    ctx = replace(
        d["ctx"],
        binding_id=binding.id,
        catalog_id=binding.catalog_id,
        catalog_sha256=binding.catalog_sha256,
    )
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/v1.3/adgroups/get"
        size, page = int(request.url.params["page_size"]), int(request.url.params["page"])
        rows = [
            dict(
                campaign_id=10,
                adgroup_id=i,
                adgroup_name="合成广告组",
                configured_status="AD_STATUS_NORMAL",
                daily_budget=0,
                total_budget=500,
                bid_amount=0,
                deep_conversion_worth_rate="123.45",
                created_time=1,
                last_modified_time=1,
                is_deleted=False,
                billing_event="BILLINGEVENT_CLICK",
                optimization_goal="OPTIMIZATIONGOAL_CLICK",
            )
            for i in (11, 12, 13)
        ]
        return httpx.Response(
            200,
            json=dict(
                code=0,
                data=dict(
                    list=rows[(page - 1) * size : page * size],
                    page_info=dict(
                        page=page, page_size=size, total_number=3, total_page=(3 + size - 1) // size
                    ),
                ),
            ),
        )

    original = d["reader"]
    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(native_signer, transport=httpx.MockTransport(upstream)),
            d["archive"],
            policy=ARCHIVE,
            decoder=decode_verdict,
        ),
        original.client.governance,
        original.client.application_id,
    )
    reader = AdqPlanSync(
        original.store, client, original.tokens, ("/v1.3/adgroups/get",), ("read",)
    )
    return d | dict(
        ctx=ctx, reader=reader, calls=calls, adgroup_binding=binding, adgroup_source=source
    )


async def test_adgroup_full_pages_independent_proof_and_level(adgroups: dict[str, Any]) -> None:
    d = adgroups
    await d["reader"].sync(d["ctx"], "ADGROUP", uuid4(), page_size=2)

    def view() -> dict[str, Any]:
        with business_transaction(d["engine"], d["identity"]) as session:
            return read_plan_observations(
                session, d["ctx"], "ADGROUP", d["reader"].client.application_id, frozenset({"read"})
            )

    assert view()["gaps"] == ["PLAN_MEASURE_PROOF_MISSING"]
    now = datetime.now(UTC)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        source = owner.execute(
            text("""SELECT id FROM sync_sources WHERE resource_kind='adq.plans.adgroup'
                    AND account_id=:account"""),
            dict(account=d["ctx"].account_id),
        ).scalar_one()
        evidence = dict(ref="synthetic-only-plan-units", sha256="a" * 64)
        proof = PlanMeasureProof(
            source_id=source,
            binding_version=d["ctx"].capability_version,
            version=1,
            level="ADGROUP",
            currency="CNY",
            budget_field="daily_budget",
            budget_unit="CNY_MINOR",
            bid_unit="CNY_MINOR",
            roi_scale="PERCENT",
            roi_attribution="synthetic-attribution-only",
            source_epoch="SECONDS",
            source_timezone="UTC",
            billing_events=("BILLINGEVENT_CLICK",),
            optimization_goals=("OPTIMIZATIONGOAL_CLICK",),
            status_map={"AD_STATUS_NORMAL": "RUNNING"},
            evidence={
                k: evidence for k in ("budget", "bid", "currency", "roi", "source_time", "status")
            },
            checked_by="SYNTHETIC",
            checked_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(hours=1),
            max_age_seconds=3600,
        )
        register_plan_proof(owner, d["tenant"], proof)
    result = view()
    assert result["status"] == "ready" and len(result["normalized"]) == 3
    item = result["normalized"][0]
    assert item["level"] == "ADGROUP" and item["values"]["budgetMinor"] == 0
    assert item["values"]["bidMinor"] == 0 and item["values"]["targetRoi"] == 1.2345
    assert item["proofId"] == str(proof.id) and len(d["calls"]) == 2


@pytest.mark.parametrize("invalid", ["expired", "metadata", "wrong_path"])
async def test_adgroup_official_source_fails_closed(adgroups: dict[str, Any], invalid: str) -> None:
    from yuliang.modules.capability.service import register_binding
    from yuliang.platform.errors import ApiError

    d = adgroups
    source = d["adgroup_source"]
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if invalid == "wrong_path":
            wrong = d["adgroup_binding"].model_copy(
                update=dict(id=uuid4(), normalized_path="/v1.3/campaigns/get")
            )
            with pytest.raises(ValueError):
                register_binding(
                    owner, wrong, route_source=source.model_copy(update=dict(id=uuid4()))
                )
            return
        if invalid == "expired":
            expired = source.model_copy(
                update=dict(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            owner.execute(
                text("""UPDATE capability_route_sources SET expires_at=:expires,
                payload=CAST(:payload AS jsonb) WHERE binding_id=:binding"""),
                dict(
                    expires=expired.expires_at,
                    payload=expired.model_dump_json(),
                    binding=d["ctx"].binding_id,
                ),
            )
        else:
            owner.execute(
                text("""UPDATE capability_route_sources
                SET expires_at=expires_at-interval '1 minute'
                WHERE binding_id=:binding"""),
                dict(binding=d["ctx"].binding_id),
            )
    with pytest.raises((ValueError, ApiError)):
        await d["reader"].sync(d["ctx"], "ADGROUP", uuid4(), page_size=2)
    assert d["calls"] == []
