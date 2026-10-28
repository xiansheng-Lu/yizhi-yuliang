"""真实PG/Redis推广资产来源切片，HTTP及适用证据均合成。"""

import json
from dataclasses import replace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.modules.capability.service import register_binding
from yuliang.modules.configuration.lookup_service import LookupService
from yuliang.modules.configuration.lookup_sources import lookup_source, register_lookup_source
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.lookup_protocol import TARGET_ARCHIVE, TARGET_PATH, TARGET_POLICY
from yuliang.modules.connectors.lookup_sync import TargetLookupSync
from yuliang.modules.connectors.native_reads import decode_verdict, native_signer
from yuliang.modules.connectors.sync_store import SyncStore
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import populate
from .test_resource_lookups import capability as capability
from .test_resource_lookups import configuration_ready as configuration_ready
from .test_resource_lookups import database as database
from .test_resource_lookups import governance as governance
from .test_resource_lookups import login_api as login_api
from .test_resource_lookups import lookups as lookups
from .test_resource_lookups import oauth_ready as oauth_ready

pytestmark = pytest.mark.integration


@pytest.fixture
async def targets(lookups: dict[str, Any]) -> dict[str, Any]:
    d = lookups
    entry = next(r for r in d["bundle"].records if r["normalizedPath"] == TARGET_PATH)
    binding = d["read_binding"].model_copy(
        update=dict(id=uuid4(), catalog_id=entry["id"], normalized_path=TARGET_PATH)
    )
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_binding(owner, binding)
    populate(d, execution=True, target=binding)
    ctx = replace(d["ctx"], binding_id=binding.id, catalog_id=binding.catalog_id)
    proof = d["proof"].model_copy(
        update=dict(
            id=uuid4(),
            binding_id=binding.id,
            kind="targets",
            eligibility="UNDELETED_ASSET_VERIFIED_FOR_EXACT_CONTEXT",
            marketing_target_type="MARKETING_TARGET_TYPE_TRAFFIC",
            marketing_asset_types=("MARKETING_ASSET_TYPE_TRAFFIC_CAR_MODEL",),
        )
    )
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_lookup_source(owner, d["tenant"], proof)
    state: dict[str, Any] = dict(fail=False, wrongType=False)
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == TARGET_PATH
        assert request.url.params["marketing_target_type"] == proof.marketing_target_type
        assert request.url.params["account_id"] == ctx.native_account_id
        assert "organization_id" not in request.url.params
        if state["fail"]:
            return httpx.Response(200, json=dict(code=11002))
        number, size = int(request.url.params["page"]), int(request.url.params["page_size"])
        rows = [
            dict(
                marketing_asset_id=i,
                marketing_asset_name="实际资产" + str(i),
                marketing_asset_type="MARKETING_ASSET_TYPE_TRAFFIC_CAR_MODEL",
                is_deleted=deleted,
                properties=[dict(private="DO_NOT_COLLECT")],
            )
            for i, deleted in ((1, False), (2, True), (3, None))
        ]
        if state["wrongType"]:
            rows[0]["marketing_asset_type"] = "MARKETING_ASSET_TYPE_UNKNOWN"
        return httpx.Response(
            200,
            json=dict(
                code=0,
                data=dict(
                    list=rows[(number - 1) * size : number * size],
                    page_info=dict(
                        page=number,
                        page_size=size,
                        total_number=3,
                        total_page=(3 + size - 1) // size,
                    ),
                ),
            ),
        )

    old = d["reader"].client
    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(native_signer, transport=httpx.MockTransport(upstream)),
            old.client.archive,
            policy=TARGET_ARCHIVE,
            decoder=decode_verdict,
        ),
        old.governance,
        old.application_id,
    )
    reader = TargetLookupSync(
        SyncStore(d["engine"], d["reader"].store.codec, policy=TARGET_POLICY),
        client,
        d["reader"].tokens,
        (TARGET_PATH,),
    )
    return d | dict(
        targetctx=ctx,
        targetproof=proof,
        targetreader=reader,
        targetlookup=LookupService(reader, SecretStr("synthetic-target-cursor-key-at-least-32")),
        targetcalls=calls,
        targetstate=state,
    )


@pytest.mark.asyncio
async def test_target_sources_do_not_replace_audiences_and_deleted_unselectable(
    targets: dict[str, Any],
) -> None:
    d = targets
    with business_transaction(d["engine"], d["identity"]) as s:
        audience = lookup_source(s, d["context"], "audiences")
        assert audience is not None and audience.id == d["proof"].id
        target = lookup_source(s, d["context"], "targets")
        assert target is not None and target.id == d["targetproof"].id
    run = uuid4()
    await d["targetreader"].sync(d["targetctx"], d["context"], run, page_size=2)
    await d["targetreader"].sync(d["targetctx"], d["context"], run, page_size=2)
    assert len(d["targetcalls"]) == 2
    page = d["targetlookup"].lookup(d["targetctx"], d["context"], "targets")
    assert [i["eligible"] for i in page["items"]] == [True, False, False]
    assert "DO_NOT_COLLECT" not in json.dumps(page)
    with business_transaction(d["engine"], d["identity"]) as s:
        rows = (
            s.execute(
                text("SELECT items FROM resource_lookup_pages WHERE proof_id=:id"),
                dict(id=d["targetproof"].id),
            )
            .scalars()
            .all()
        )
        assert "properties" not in json.dumps(rows)
    assert (
        d["targetlookup"].lookup(d["targetctx"], d["context"], "targets", q="用户随意输入")["items"]
        == []
    )


@pytest.mark.asyncio
async def test_unconfirmed_asset_type_and_authorization_revision_fail_closed(
    targets: dict[str, Any],
) -> None:
    d = targets
    d["targetstate"]["wrongType"] = True
    await d["targetreader"].sync(d["targetctx"], d["context"], uuid4())
    assert not d["targetlookup"].lookup(d["targetctx"], d["context"], "targets")["items"][0][
        "eligible"
    ]
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET version=version+1 WHERE id=:id"),
            dict(id=d["authorization"]),
        )
    with pytest.raises(ApiError):
        d["targetlookup"].lookup(d["targetctx"], d["context"], "targets")


@pytest.mark.asyncio
async def test_target_source_failure_cannot_return_false_empty_success(
    targets: dict[str, Any],
) -> None:
    d = targets
    d["targetstate"]["fail"] = True
    with pytest.raises((ApiError, ValueError)):
        await d["targetreader"].sync(d["targetctx"], d["context"], uuid4())
    with pytest.raises(ApiError):
        d["targetlookup"].lookup(d["targetctx"], d["context"], "targets")


@pytest.mark.asyncio
async def test_targets_http_frontend_contract_uses_actual_runtime_kind_dispatch(
    targets: dict[str, Any],
) -> None:
    from pathlib import Path

    from yuliang.modules.connectors.resources import (
        ApplicationResource,
        ResourceBundle,
        SecretReference,
    )
    from yuliang.modules.connectors.runtime import PlatformRuntime

    from .test_configuration_context import query

    d = targets
    await d["targetreader"].sync(d["targetctx"], d["context"], uuid4(), page_size=2)
    runtime = PlatformRuntime(d["engine"], d["settings"], ResourceBundle(applications=()))
    appid = d["targetreader"].client.application_id
    runtime.bundle = ResourceBundle.model_construct(
        applications=(
            ApplicationResource.model_construct(
                id=appid,
                enabled=True,
                app_id="synthetic",
                mode="ADQ_OAUTH",
                secret=SecretReference(file=Path("/synthetic/not-read.env"), key="SYNTHETIC_KEY"),
                source_root=Path("/synthetic"),
                tenant_id=d["tenant"],
                connector_id="adq",
                account_id=d["context"].account_id,
                shop_id=d["shop"],
            ),
        )
    )
    runtime.target_lookups[appid] = d["targetreader"]
    runtime.lookup_key = d["targetlookup"].key
    d["app"].state.platform_runtime = runtime
    response = d["client"].get(
        "/v1/delivery/configuration/options", params=query(d["context"]) | dict(kind="targets")
    )
    assert response.status_code == 200, response.text
    page = response.json()["data"]
    assert page["contractVersion"] == "advertising-v9.0" and page["kind"] == "targets"
    assert page["contextKey"] == d["context"].context_key
    assert [i["eligible"] for i in page["items"]] == [True, False, False]
    assert all(i["platformId"] == i["id"] for i in page["items"])
    d["app"].state.platform_runtime = None
