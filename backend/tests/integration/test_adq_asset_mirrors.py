"""真实PG/Redis分页镜像；供应商HTTP、scope、关系依据均合成，非P验收。"""

import json
import os
import secrets
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretBytes, SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.capability.service import register_binding
from yuliang.modules.catalog.adq_mirrors import (
    list_assets,
    register_campaign_shop,
    resolve_campaign,
)
from yuliang.modules.catalog.service import link_authorization
from yuliang.modules.connectors.adq_assets import MIRROR_POLICY, AdqAssetSync
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.governance import PlatformGovernance
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.native_reads import decode_verdict, native_signer
from yuliang.modules.connectors.sync_cursor import CursorCodec
from yuliang.modules.connectors.sync_store import SyncConflict, SyncStore
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
def assets(legacy: dict[str, Any], governance: PlatformGovernance) -> dict[str, Any]:
    data = legacy
    calls: list[httpx.Request] = []
    state: dict[str, Any] = dict(
        rows=[
            dict(
                campaign_id=9007199254740993,
                campaign_name="合成计划甲",
                configured_status="AD_STATUS_NORMAL",
                daily_budget=9007199254740997,
            ),
            dict(
                campaign_id=42,
                campaign_name="合成计划乙",
                configured_status="AD_STATUS_SUSPEND",
                daily_budget=0,
            ),
            dict(
                campaign_id=43,
                campaign_name="合成计划丙",
                configured_status="AD_STATUS_NORMAL",
                daily_budget=300,
            ),
        ],
        fail_page=None,
        mutate=None,
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        page, size = int(request.url.params["page"]), int(request.url.params["page_size"])
        assert request.url.params["account_id"] in {"12345", "67890"}
        assert not any(k in request.url.params for k in ("sync_cursor", "shop_id", "tenant_id"))
        if state["fail_page"] == page:
            return httpx.Response(200, json={"code": 11002, "data": None})
        if request.url.path == "/v3.0/advertiser/get":
            rows = [
                dict(
                    account_id=int(request.url.params["account_id"]),
                    business_alias="合成广告主",
                    system_status="CUSTOMER_STATUS_NORMAL",
                    daily_budget=1000,
                )
            ]
        else:
            assert (
                request.url.path == "/v1.3/campaigns/get"
                and "pagination_mode" not in request.url.params
            )
            rows = state["rows"]
        info = dict(
            page=page,
            page_size=size,
            total_number=len(rows),
            total_page=(len(rows) + size - 1) // size,
        )
        body = dict(code=0, data=dict(list=rows[(page - 1) * size : page * size], page_info=info))
        if state["mutate"] is not None:
            state["mutate"](body, page)
        return httpx.Response(200, json=body)

    async def tokens(ctx: Any) -> SecretStr:
        return SecretStr("synthetic-token")

    with business_transaction(data["engine"], data["identity"]) as session:
        app_id = session.execute(
            text("SELECT application_id FROM provider_authorizations WHERE id=:id"),
            dict(id=data["authorization"]),
        ).scalar_one()
    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(native_signer, transport=httpx.MockTransport(upstream)),
            data["archive"],
            decoder=decode_verdict,
        ),
        governance,
        app_id,
    )
    codec = CursorCodec(
        {1: SecretBytes(secrets.token_bytes(32))}, 1, SecretStr(secrets.token_urlsafe(48))
    )
    reader = AdqAssetSync(
        SyncStore(data["engine"], codec, policy=MIRROR_POLICY),
        client,
        tokens,
        ("/v1.3/campaigns/get", "/v3.0/advertiser/get"),
        ("read",),
    )
    return data | dict(reader=reader, calls=calls, state=state)


async def test_all_pages_exact_ids_replay_versions_and_raw_page_provenance(
    assets: dict[str, Any],
) -> None:
    data = assets
    run = uuid4()
    result = await data["reader"].sync(data["ctx"], "CAMPAIGN", run, page_size=2)
    assert (
        result["state"] == "SUCCESS" and result["confirmedPages"] == 2 and result["itemsCount"] == 3
    )
    assert len(data["calls"]) == 2
    assert await data["reader"].sync(data["ctx"], "CAMPAIGN", run, page_size=2) == result
    assert len(data["calls"]) == 2
    rows = list_assets(data["engine"], data["ctx"], "CAMPAIGN")
    big = next(r for r in rows if r["native_id"] == "9007199254740993")
    assert big["config"]["native_daily_budget"] == "9007199254740997"
    assert big["config"]["label"] == "合成计划甲" and big["config"]["budget_minor"] is None
    assert big["config"]["target_binding"] == "UNCONFIRMED" and not big["executable"]
    assert big["bound"] and big["authorization"] == "AUTHORIZED"
    await data["reader"].sync(data["ctx"], "CAMPAIGN", uuid4(), page_size=2)
    with business_transaction(data["engine"], data["identity"]) as s:
        assert s.execute(text("SELECT count(*) FROM adq_asset_versions")).scalar_one() == 3
        assert (
            s.execute(
                text("""SELECT count(*) FROM adq_asset_observations o
          JOIN sync_pages p ON (p.tenant_id,p.run_id,p.page_no,p.outbound_id)=
          (o.tenant_id,o.run_id,o.page_no,o.outbound_id)
          JOIN platform_raw_payloads r ON r.tenant_id=p.tenant_id AND r.outbound_id=p.outbound_id
          WHERE r.phase='RESPONSE' AND r.external_outcome='READ_SUCCEEDED'""")
            ).scalar_one()
            == 6
        )
        with pytest.raises(ApiError):
            resolve_campaign(s, data["ctx"], big["id"])
    data["state"]["rows"][0]["daily_budget"] = 350
    await data["reader"].sync(data["ctx"], "CAMPAIGN", uuid4(), page_size=2)
    with business_transaction(data["engine"], data["identity"]) as s:
        version = (
            s.execute(
                text("SELECT * FROM adq_asset_versions WHERE asset_id=:id AND version=2"),
                dict(id=big["id"]),
            )
            .mappings()
            .one()
        )
        assert version["before_json"]["native_daily_budget"] == "9007199254740997"
        assert version["after_json"]["native_daily_budget"] == "350"
        capture = os.environ.get("YULIANG_M108_CAPTURE_PATH")
        if capture:
            traces = (
                s.execute(
                    text("""SELECT p.request_id,p.id AS outbound_id,
              s.run_id,s.page_no,p.catalog_id,p.normalized_path
              FROM platform_requests p JOIN sync_pages s ON s.outbound_id=p.id
              WHERE p.account_id=:account ORDER BY p.created_at"""),
                    dict(account=data["ctx"].account_id),
                )
                .mappings()
                .all()
            )
            Path(capture).write_text(
                json.dumps(
                    dict(
                        source="ACTUAL_LOCAL_PG_GOVERNED_HTTP_MOCK_SYNTHETIC_IDENTITIES",
                        livePlatformCalls=0,
                        paidCalls=0,
                        firstRunId=str(run),
                        firstResult=result,
                        traceIds=[dict(t) for t in traces],
                        exampleMirror=big,
                        configurationRevision=dict(version),
                    ),
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
                + "\n",
                encoding="utf-8",
            )


async def test_partial_keeps_old_mirrors_and_resumes_original_run(assets: dict[str, Any]) -> None:
    data = assets
    await data["reader"].sync(data["ctx"], "CAMPAIGN", uuid4(), page_size=2)
    run = uuid4()
    data["state"]["fail_page"] = 2
    with pytest.raises(ApiError):
        await data["reader"].sync(data["ctx"], "CAMPAIGN", run, page_size=2)
    assert len(list_assets(data["engine"], data["ctx"], "CAMPAIGN")) == 3
    with business_transaction(data["engine"], data["identity"]) as s:
        row = s.execute(
            text("SELECT state,page_no FROM sync_runs WHERE id=:id"), dict(id=run)
        ).one()
        assert tuple(row) == ("PARTIAL", 1)
    data["state"]["fail_page"] = None
    before = len(data["calls"])
    result = await data["reader"].sync(data["ctx"], "CAMPAIGN", run, page_size=2)
    assert result["state"] == "SUCCESS" and result["confirmedPages"] == 2
    assert len(data["calls"]) == before + 1 and data["calls"][-1].url.params["page"] == "2"


@pytest.mark.parametrize("failure", ["duplicate", "totals", "missing", "wrong_account", "float_id"])
async def test_malformed_page_cannot_advance_checkpoint_or_delete_assets(
    assets: dict[str, Any], failure: str
) -> None:
    data = assets

    def mutate(body: dict[str, Any], page: int) -> None:
        if page != 2:
            return
        payload = body["data"]
        if failure == "duplicate":
            payload["list"][0]["campaign_id"] = 42
        elif failure == "totals":
            payload["page_info"].update(total_number=4)
            payload["list"].append(dict(payload["list"][0], campaign_id=44))
        elif failure == "missing":
            payload["list"].clear()
        elif failure == "wrong_account":
            payload["list"][0]["account_id"] = 67890
        else:
            payload["list"][0]["campaign_id"] = 43.0

    data["state"]["mutate"] = mutate
    run = uuid4()
    with pytest.raises((ValueError, SyncConflict)):
        await data["reader"].sync(data["ctx"], "CAMPAIGN", run, page_size=2)
    with business_transaction(data["engine"], data["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM sync_pages WHERE run_id=:id"), dict(id=run)
            ).scalar_one()
            == 1
        )
        assert s.execute(text("SELECT count(*) FROM adq_asset_mirrors")).scalar_one() == 2


async def test_account_mirror_and_same_native_campaign_in_different_accounts(
    assets: dict[str, Any],
) -> None:
    data = assets
    binding = data["route_binding"].model_copy(
        update={
            "id": uuid4(),
            "catalog_sha256": data["bundle"].sha256,
            "catalog_id": "43259a31fa001d0eff5a",
            "normalized_path": "/v3.0/advertiser/get",
        }
    )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_binding(owner, binding)
    populate(data, execution=True, target=binding)
    ctx = replace(
        data["ctx"],
        binding_id=binding.id,
        catalog_id=binding.catalog_id,
        catalog_sha256=binding.catalog_sha256,
    )
    result = await data["reader"].sync(ctx, "ACCOUNT", uuid4(), page_size=2)
    assert result["state"] == "SUCCESS" and len(list_assets(data["engine"], ctx, "ACCOUNT")) == 1
    await data["reader"].sync(data["ctx"], "CAMPAIGN", uuid4(), page_size=2)
    account = uuid4()
    other = data["route_binding"].model_copy(update={"id": uuid4(), "account_id": account})
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO ad_accounts(id,tenant_id,entity_id,provider,
             product_line,native_id,name,status)
          VALUES(:id,:tenant,:entity,'tencent','adq','67890','Synthetic B','AUTHORIZED')"""),
            dict(id=account, tenant=data["tenant"], entity=data["context"].business_entity_id),
        )
        owner.execute(
            text(
                "INSERT INTO entity_links(tenant_id,shop_id,account_id) "
                "VALUES(:tenant,:shop,:account)"
            ),
            dict(tenant=data["tenant"], shop=data["shop"], account=account),
        )
        link_authorization(owner, data["authorization"], account, "synthetic/M1-08/account-B")
        register_binding(
            owner, other, route_source=data["route_source"].model_copy(update={"id": uuid4()})
        )
        from yuliang.modules.capability.models import Evidence
        from yuliang.modules.capability.service import record_evidence

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
                other.id,
                proof.model_copy(update={"id": uuid4(), "binding_id": other.id, "sequence": 0}),
            )
    otherctx = replace(
        data["ctx"], account_id=account, native_account_id="67890", binding_id=other.id
    )
    await data["reader"].sync(otherctx, "CAMPAIGN", uuid4(), page_size=2)
    a = list_assets(data["engine"], data["ctx"], "CAMPAIGN")
    b = list_assets(data["engine"], otherctx, "CAMPAIGN")
    assert len(a) == len(b) == 3 and {r["id"] for r in a}.isdisjoint(r["id"] for r in b)


async def test_bound_vs_authorized_shop_mapping_and_revocation(assets: dict[str, Any]) -> None:
    data = assets
    await data["reader"].sync(data["ctx"], "CAMPAIGN", uuid4(), page_size=2)
    asset = list_assets(data["engine"], data["ctx"], "CAMPAIGN")[0]
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        internal = register_campaign_shop(
            owner,
            data["tenant"],
            asset["id"],
            data["shop"],
            "synthetic/M1-08/shop-proof",
            data["now"],
            data["now"] + timedelta(minutes=10),
        )
    with business_transaction(data["engine"], data["identity"]) as s:
        assert resolve_campaign(s, data["ctx"], asset["id"]) == internal
        with pytest.raises(ApiError):
            resolve_campaign(s, replace(data["ctx"], shop_id=uuid4()), asset["id"])
    # 第三方租户不可读取；运行角色不可篡改修订或自签店铺映射。
    with business_transaction(
        data["engine"], replace(data["identity"], tenant_id=data["tenant"] + 999)
    ) as s:
        assert s.execute(text("SELECT count(*) FROM adq_asset_mirrors")).scalar_one() == 0
    for statement in (
        "DELETE FROM adq_asset_versions",
        "UPDATE adq_asset_versions SET version=99",
        "DELETE FROM adq_campaign_shop_links",
    ):
        with pytest.raises(DBAPIError), business_transaction(data["engine"], data["identity"]) as s:
            s.execute(text(statement))
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET status='REVOKED' WHERE id=:id"),
            dict(id=data["authorization"]),
        )
    cached = list_assets(data["engine"], data["ctx"], "CAMPAIGN")
    assert all(r["bound"] and r["authorization"] == "UNAUTHORIZED" for r in cached)
    before = len(data["calls"])
    with pytest.raises(ApiError):
        await data["reader"].sync(data["ctx"], "CAMPAIGN", uuid4(), page_size=2)
    assert len(data["calls"]) == before


async def test_actual_second_shop_cannot_borrow_plan_mapping_or_sync_checkpoint(
    assets: dict[str, Any],
) -> None:
    data = assets
    await data["reader"].sync(data["ctx"], "CAMPAIGN", uuid4(), page_size=2)
    asset = list_assets(data["engine"], data["ctx"], "CAMPAIGN")[0]
    shop = uuid4()
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO shops(id,tenant_id,entity_id,platform,name,native_id)
          VALUES(:id,:tenant,:entity,'wxshop','Synthetic second shop',:native)"""),
            dict(
                id=shop,
                tenant=data["tenant"],
                entity=data["context"].business_entity_id,
                native=str(shop),
            ),
        )
        owner.execute(
            text(
                "INSERT INTO entity_links(tenant_id,shop_id,account_id) "
                "VALUES(:tenant,:shop,:account)"
            ),
            dict(tenant=data["tenant"], shop=shop, account=data["ctx"].account_id),
        )
        register_campaign_shop(
            owner,
            data["tenant"],
            asset["id"],
            data["shop"],
            "synthetic/M1-08/shop-A",
            data["now"],
            data["now"] + timedelta(minutes=10),
        )
    otherctx = replace(data["ctx"], shop_id=shop)
    assert len(list_assets(data["engine"], otherctx, "CAMPAIGN")) == 3
    with business_transaction(data["engine"], data["identity"]) as s, pytest.raises(ApiError):
        resolve_campaign(s, otherctx, asset["id"])
    calls = len(data["calls"])
    with pytest.raises(SyncConflict) as stopped:
        await data["reader"].sync(otherctx, "CAMPAIGN", uuid4(), page_size=2)
    assert stopped.value.reason == "CONTEXT_CHANGED" and len(data["calls"]) == calls


async def test_projection_failure_rolls_back_whole_page_and_can_resume(
    assets: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yuliang.modules.catalog import adq_mirrors

    data = assets
    original = adq_mirrors.project_assets

    def broken(*args: Any) -> None:
        original(*args)
        raise ValueError("synthetic failure after mirror/version writes")

    monkeypatch.setattr(adq_mirrors, "project_assets", broken)
    run = uuid4()
    with pytest.raises(ValueError):
        await data["reader"].sync(data["ctx"], "CAMPAIGN", run, page_size=2)
    with business_transaction(data["engine"], data["identity"]) as s:
        for query in (
            "SELECT count(*) FROM adq_asset_mirrors",
            "SELECT count(*) FROM adq_asset_versions",
            "SELECT count(*) FROM sync_pages",
        ):
            assert s.execute(text(query)).scalar_one() == 0
        assert (
            s.execute(text("SELECT page_no FROM sync_runs WHERE id=:id"), dict(id=run)).scalar_one()
            == 0
        )
    monkeypatch.setattr(adq_mirrors, "project_assets", original)
    assert (await data["reader"].sync(data["ctx"], "CAMPAIGN", run, page_size=2))[
        "state"
    ] == "SUCCESS"


async def test_authorization_version_change_cannot_resume_old_source(
    assets: dict[str, Any],
) -> None:
    data = assets
    data["state"]["fail_page"] = 2
    run = uuid4()
    with pytest.raises(ApiError):
        await data["reader"].sync(data["ctx"], "CAMPAIGN", run, page_size=2)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET version=version+1 WHERE id=:id"),
            dict(id=data["authorization"]),
        )
    calls = len(data["calls"])
    data["state"]["fail_page"] = None
    with pytest.raises(SyncConflict):
        await data["reader"].sync(data["ctx"], "CAMPAIGN", run, page_size=2)
    assert len(data["calls"]) == calls
