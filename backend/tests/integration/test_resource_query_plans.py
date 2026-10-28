"""V9广告组真实PG/会话HTTP，平台端仅内存合成；不改SDK曝光。"""

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from yuliang.modules.capability.models import Evidence, Phase
from yuliang.modules.capability.route_sources import PROFILES, ReadRouteSource
from yuliang.modules.capability.service import record_evidence, register_binding
from yuliang.modules.configuration.integration_evidence import register_operation
from yuliang.modules.configuration.integration_models import OperationDeclaration
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.native_reads import decode_verdict, native_signer
from yuliang.modules.connectors.plan_protocol import ARCHIVE, POLICY
from yuliang.modules.connectors.plan_sync import AdqPlanSync
from yuliang.modules.connectors.sync_store import SyncConflict, SyncStore
from yuliang.modules.delivery.plan_proofs import PlanMeasureProof
from yuliang.modules.delivery.plan_reads import register_plan_proof
from yuliang.modules.reporting.query_service import QueryService
from yuliang.platform.errors import ApiError, ErrorCode

from .test_adq_asset_mirrors import assets as assets
from .test_capability_evidence import capability as capability
from .test_configuration_context import configuration_ready as configuration_ready
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import challenge
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_read_route_sources import legacy as legacy
from .test_resource_queries import request

pytestmark = pytest.mark.integration
PATH = "/v1/delivery/integrations/queries"


@pytest.fixture
def query_plans(assets: dict[str, Any]) -> dict[str, Any]:
    d = assets
    now = datetime.now(UTC)
    # 原冻结前端目录行，独立SDK证明仅校准实际1.3读取路径，不改目录或head。
    binding = d["read_binding"].model_copy(
        update=dict(
            id=uuid4(),
            catalog_sha256=d["bundle"].sha256,
            catalog_id="90b086afd649f5dc0600",
            normalized_path="/v1.3/adgroups/get",
        )
    )
    source = ReadRouteSource(
        profile="ADQ_ADGROUPS_V1_3_READ",
        source_sha256=PROFILES["ADQ_ADGROUPS_V1_3_READ"].source_sha256,
        checked_by="SYNTHETIC",
        checked_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(hours=1),
    )
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_binding(owner, binding, route_source=source)
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
                    proof_ref=source.proof_ref
                    if phase == Phase.PATH
                    else "synthetic/adgroup-query",
                    checked_by="SYNTHETIC",
                    checked_at=now,
                    expires_at=source.expires_at,
                ),
            )
        register_operation(
            owner,
            d["tenant"],
            OperationDeclaration(
                binding_id=binding.id,
                binding_version=binding.revision,
                resource="adgroups",
                authorization_id=d["authorization"],
                authorization_version=1,
                required_scopes=("read",),
                proof_ref="synthetic/M1-19/adgroup-query",
                checked_at=now,
                expires_at=source.expires_at,
            ),
        )
    ctx = replace(
        d["ctx"],
        binding_id=binding.id,
        catalog_id=binding.catalog_id,
        catalog_sha256=binding.catalog_sha256,
    )
    state: dict[str, Any] = dict(
        rows=[
            dict(
                campaign_id=10 if i != 13 else 20,
                adgroup_id=i,
                adgroup_name=f"合成广告组{i}",
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
        ],
        fail=False,
    )
    calls = []

    def upstream(wire: httpx.Request) -> httpx.Response:
        calls.append(wire)
        assert wire.url.path == "/v1.3/adgroups/get"
        assert wire.url.params["account_id"] == "12345"
        if state["fail"]:
            return httpx.Response(200, json=dict(code=11002))
        size, page = int(wire.url.params["page_size"]), int(wire.url.params["page"])
        count = len(state["rows"])
        return httpx.Response(
            200,
            json=dict(
                code=0,
                data=dict(
                    list=state["rows"][(page - 1) * size : page * size],
                    page_info=dict(
                        page=page,
                        page_size=size,
                        total_number=count,
                        total_page=(count + size - 1) // size,
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
        SyncStore(d["engine"], original.store.codec, policy=POLICY),
        client,
        original.tokens,
        ("/v1.3/adgroups/get",),
        ("read",),
    )
    return d | dict(ctx=ctx, reader=reader, state=state, calls=calls, query_binding=binding)


def http(d: dict[str, Any], value: Any) -> Any:
    return d["client"].post(
        PATH,
        json=value.model_dump(mode="json", by_alias=True),
        headers={"X-CSRF-Token": challenge(d["client"])},
    )


def proof(d: dict[str, Any]) -> None:
    now = datetime.now(UTC)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        source = owner.execute(
            text("""SELECT id FROM sync_sources
          WHERE binding_id=:binding AND resource_kind='adq.plans.adgroup'"""),
            dict(binding=d["ctx"].binding_id),
        ).scalar_one()
        evidence = dict(ref="synthetic-only-not-production-proof", sha256="a" * 64)
        register_plan_proof(
            owner,
            d["tenant"],
            PlanMeasureProof(
                source_id=source,
                binding_version=1,
                version=1,
                level="ADGROUP",
                currency="CNY",
                budget_field="daily_budget",
                budget_unit="CNY_MINOR",
                bid_unit="CNY_MINOR",
                billing_events=("BILLINGEVENT_CLICK",),
                optimization_goals=("OPTIMIZATIONGOAL_CLICK",),
                roi_scale="PERCENT",
                roi_attribution="SYNTHETIC_TARGET_ONLY",
                source_epoch="SECONDS",
                source_timezone="UTC",
                status_map={"AD_STATUS_NORMAL": "RUNNING"},
                evidence={
                    k: evidence
                    for k in ("budget", "bid", "currency", "roi", "source_time", "status")
                },
                checked_by="SYNTHETIC",
                checked_at=now - timedelta(seconds=1),
                expires_at=now + timedelta(hours=1),
                max_age_seconds=3600,
            ),
        )


async def test_http_current_head_real_consumer_capture(query_plans: dict[str, Any]) -> None:
    d = query_plans
    await d["reader"].sync(d["ctx"], "ADGROUP", uuid4(), page_size=2)
    value = request(d, d["ctx"], "adgroups")
    response = http(d, value)
    assert response.status_code == 200, response.text
    page = response.json()["data"]
    assert len(page["rows"]) == 3 and all(r["metrics"]["budgetMinor"] is None for r in page["rows"])
    assert all(r["updatedAt"] == "源更新时间未提供" and r["version"] is None for r in page["rows"])
    context_response = d["client"].get("/v1/delivery/integrations/context?productLine=adq")
    assert context_response.status_code == 200
    context = context_response.json()["data"]
    operation = next(
        o
        for o in context["operations"]
        if o["catalogId"] == "90b086afd649f5dc0600"
        and o["kind"] == "READ"
        and o["evidence"]["backend"]["verified"]
    )
    assert all(item["verified"] for item in operation["evidence"].values())
    parent_input = value.model_copy(
        update=dict(filters=value.filters.model_copy(update=dict(native_parent_id="10")))
    )
    parent_page = http(d, parent_input).json()["data"]
    assert {r["nativeId"] for r in parent_page["rows"]} == {"11", "12"}
    proof(d)
    known_page = http(d, value).json()["data"]
    assert all(r["metrics"] == {"budgetMinor": 0} for r in known_page["rows"])
    assert all(r["moneyType"] == "AD_BUDGET" for r in known_page["rows"])
    assert len(d["calls"]) == 2  # 查询全部只读本地投影，不新增供应商请求。
    if destination := os.getenv("YULIANG_ADGROUP_QUERY_CAPTURE"):
        Path(destination).write_text(
            json.dumps(
                dict(
                    page=page,
                    input=value.model_dump(mode="json", by_alias=True),
                    context=context,
                    identity=d["client"].get("/v1/auth/session").json()["data"],
                    parentPage=parent_page,
                    parentInput=parent_input.model_dump(mode="json", by_alias=True),
                    cases=[
                        dict(
                            label="known-zero",
                            input=value.model_dump(mode="json", by_alias=True),
                            page=known_page,
                        )
                    ],
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


async def test_not_connected_not_empty_then_actual_empty(query_plans: dict[str, Any]) -> None:
    d = query_plans
    value = request(d, d["ctx"], "adgroups")
    assert http(d, value).status_code == 422
    d["state"]["rows"] = []
    await d["reader"].sync(d["ctx"], "ADGROUP", uuid4(), page_size=2)
    result = http(d, value)
    assert result.status_code == 200, result.text
    assert result.json()["data"]["rows"] == [] and result.json()["data"]["syncedAt"]


@pytest.mark.parametrize("change", ["revoked", "scope", "expired_source", "head", "phase"])
async def test_current_authorization_and_sources_rechecked(
    query_plans: dict[str, Any], change: str
) -> None:
    d = query_plans
    await d["reader"].sync(d["ctx"], "ADGROUP", uuid4(), page_size=2)
    statements = {
        "revoked": "UPDATE provider_authorizations SET status='REVOKED' WHERE id=:auth",
        "scope": "UPDATE provider_authorizations SET scopes=ARRAY['other'] WHERE id=:auth",
        "expired_source": "UPDATE capability_route_sources SET expires_at=clock_timestamp() "
        "-interval '1 second' WHERE binding_id=:binding",
        "head": "UPDATE catalog_head SET catalog_sha256=:old WHERE singleton",
        "phase": "UPDATE capability_assertions SET checked_at=clock_timestamp()-interval '1 hour',"
        "expires_at=clock_timestamp()"
        "-interval '1 second' "
        "WHERE binding_id=:binding",
    }
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(statements[change]),
            dict(
                auth=d["authorization"],
                binding=d["ctx"].binding_id,
                old=d["route_binding"].catalog_sha256,
            ),
        )
    try:
        assert http(d, request(d, d["ctx"], "adgroups")).status_code == 422
    finally:
        if change == "head":
            with d["maintenance"].begin() as owner:
                owner.execute(text("SET LOCAL ROLE app_owner"))
                owner.execute(
                    text("UPDATE catalog_head SET catalog_sha256=:sha WHERE singleton"),
                    dict(sha=d["bundle"].sha256),
                )


async def test_scope_and_campaign_closed_and_cursor_rejected(query_plans: dict[str, Any]) -> None:
    d = query_plans
    value = request(d, d["ctx"], "adgroups")
    service = QueryService(d["engine"], d["identity"])
    for field, changed in (
        ("tenant_id", "999999999"),
        ("shop_id", uuid4()),
        ("account_id", str(uuid4())),
        ("business_entity_id", uuid4()),
    ):
        with pytest.raises(ApiError) as error:
            service.query(
                value.model_copy(update=dict(scope=value.scope.model_copy(update={field: changed})))
            )
        assert error.value.code == ErrorCode.FORBIDDEN
    assert http(d, value.model_copy(update=dict(resource="campaigns"))).status_code == 422
    now = datetime.now(UTC)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_operation(
            owner,
            d["tenant"],
            OperationDeclaration(
                binding_id=d["ctx"].binding_id,
                binding_version=1,
                resource="campaigns",
                authorization_id=d["authorization"],
                authorization_version=1,
                required_scopes=("read",),
                proof_ref="synthetic/campaign-resource-not-implemented",
                checked_at=now - timedelta(seconds=1),
                expires_at=now + timedelta(hours=1),
            ),
        )
    context = d["client"].get("/v1/delivery/integrations/context?productLine=adq").json()["data"]
    campaign = next(o for o in context["operations"] if o["resource"] == "campaigns")
    assert not campaign["evidence"]["backend"]["verified"]
    assert (
        http(
            d,
            value.model_copy(
                update=dict(filters=value.filters.model_copy(update=dict(cursor="not-supported")))
            ),
        ).status_code
        == 422
    )


async def test_latest_failed_run_does_not_reuse_old_success(query_plans: dict[str, Any]) -> None:
    d = query_plans
    await d["reader"].sync(d["ctx"], "ADGROUP", uuid4(), page_size=2)
    d["state"]["fail"] = True
    with pytest.raises((ValueError, ApiError, SyncConflict)):
        await d["reader"].sync(d["ctx"], "ADGROUP", uuid4(), page_size=2)
    assert http(d, request(d, d["ctx"], "adgroups")).status_code == 422


async def test_http_identity_and_csrf_cannot_be_overridden(query_plans: dict[str, Any]) -> None:
    d = query_plans
    value = request(d, d["ctx"], "adgroups").model_dump(mode="json", by_alias=True)
    assert d["client"].post(PATH, json=value).status_code == 403
    assert (
        d["client"]
        .post(PATH + "?tenantId=999", json=value, headers={"X-CSRF-Token": challenge(d["client"])})
        .status_code
        == 422
    )


async def test_optional_account_resolves_stable_id_and_search(query_plans: dict[str, Any]) -> None:
    d = query_plans
    await d["reader"].sync(d["ctx"], "ADGROUP", uuid4(), page_size=2)
    value = request(d, d["ctx"], "adgroups")
    explicit = http(d, value).json()["data"]
    blank = value.model_copy(update=dict(scope=value.scope.model_copy(update=dict(account_id=""))))
    response = http(d, blank)
    assert response.status_code == 200, response.text
    page = response.json()["data"]
    assert [r["id"] for r in page["rows"]] == [r["id"] for r in explicit["rows"]]
    assert page["scopeKey"] == blank.scope.key()
    selected = value.model_copy(
        update=dict(filters=value.filters.model_copy(update=dict(search="广告组12")))
    )
    assert [r["nativeId"] for r in http(d, selected).json()["data"]["rows"]] == ["12"]
    invalid = value.model_copy(
        update=dict(filters=value.filters.model_copy(update=dict(native_parent_id=str(uuid4()))))
    )
    assert http(d, invalid).status_code == 422


async def test_full_result_limit_is_explicit_and_parent_can_narrow(
    query_plans: dict[str, Any],
) -> None:
    d = query_plans
    base = d["state"]["rows"][0]
    d["state"]["rows"] = [
        dict(base, adgroup_id=i, campaign_id=10 if i <= 1000 else 20) for i in range(1, 1002)
    ]
    await d["reader"].sync(d["ctx"], "ADGROUP", uuid4(), page_size=100)
    value = request(d, d["ctx"], "adgroups")
    result = http(d, value)
    assert result.status_code == 422 and "1000" in result.text
    selected = value.model_copy(
        update=dict(filters=value.filters.model_copy(update=dict(native_parent_id="10")))
    )
    result = http(d, selected)
    assert result.status_code == 200, result.text
    assert len(result.json()["data"]["rows"]) == 1000
    assert result.json()["data"]["nextCursor"] is None


@pytest.mark.parametrize("change", ["proof_expired", "auth_version"])
async def test_measure_or_authorization_expiry_never_invents_values(
    query_plans: dict[str, Any],
    change: str,
) -> None:
    d = query_plans
    await d["reader"].sync(d["ctx"], "ADGROUP", uuid4(), page_size=2)
    proof(d)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if change == "proof_expired":
            owner.execute(
                text("""UPDATE adq_plan_measure_proofs SET payload=jsonb_set(jsonb_set(
                payload,'{checked_at}',to_jsonb(clock_timestamp()-interval '1 hour')),
                '{expires_at}',to_jsonb(clock_timestamp()-interval '1 second')),
                checked_at=clock_timestamp()-interval '1 hour',
                expires_at=clock_timestamp()-interval '1 second'
                WHERE source_id IN (SELECT id FROM sync_sources WHERE binding_id=:binding)"""),
                dict(binding=d["ctx"].binding_id),
            )
        else:
            owner.execute(
                text("UPDATE provider_authorizations SET version=version+1 WHERE id=:id"),
                dict(id=d["authorization"]),
            )
    response = http(d, request(d, d["ctx"], "adgroups"))
    if change == "auth_version":
        assert response.status_code == 422
    else:
        assert response.status_code == 200, response.text
        assert all(r["metrics"]["budgetMinor"] is None for r in response.json()["data"]["rows"])
        assert "MEASURE_PROOF_NOT_CURRENT" in response.json()["data"]["warnings"]
