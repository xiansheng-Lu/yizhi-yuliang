"""真实PG/Redis自定义人群来源分页；平台和上下文依据均为合成。"""

import json
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretBytes, SecretStr
from sqlalchemy import text

from yuliang.modules.capability.service import register_binding
from yuliang.modules.configuration.lookup_service import LookupService
from yuliang.modules.configuration.lookup_sources import LookupSourceProof, register_lookup_source
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.lookup_protocol import LOOKUP_ARCHIVE, LOOKUP_PATH, LOOKUP_POLICY
from yuliang.modules.connectors.lookup_sync import AudienceLookupSync
from yuliang.modules.connectors.native_reads import decode_verdict, native_signer
from yuliang.modules.connectors.sync_cursor import CursorCodec
from yuliang.modules.connectors.sync_store import SyncConflict, SyncStore
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_capability_evidence import populate
from .test_configuration_context import configuration_ready as configuration_ready
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_platform_payloads import setup

pytestmark = pytest.mark.integration


@pytest.fixture
async def lookups(configuration_ready: dict[str, Any], governance: Any) -> dict[str, Any]:
    d = configuration_ready
    base, _, _, archive = setup(d)
    entry = next(r for r in d["bundle"].records if r["normalizedPath"] == LOOKUP_PATH)
    binding = d["read_binding"].model_copy(
        update=dict(id=uuid4(), catalog_id=entry["id"], normalized_path=LOOKUP_PATH)
    )
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE ad_accounts SET native_id='12345' WHERE id=:id"),
            dict(id=binding.account_id),
        )
        owner.execute(
            text(
                "UPDATE authorization_account_links SET native_id_snapshot='12345' "
                "WHERE account_id=:id"
            ),
            dict(id=binding.account_id),
        )
        register_binding(owner, binding)
        app = owner.execute(
            text("SELECT application_id FROM provider_authorizations WHERE id=:id"),
            dict(id=d["authorization"]),
        ).scalar_one()
    populate(d, execution=True, target=binding)
    ctx = replace(
        base,
        authorization_id=d["authorization"],
        native_account_id="12345",
        binding_id=binding.id,
        catalog_id=binding.catalog_id,
    )
    now = datetime.now(UTC)
    proof = LookupSourceProof(
        context=d["context"],
        binding_id=binding.id,
        binding_version=binding.revision,
        authorization_id=ctx.authorization_id,
        authorization_version=1,
        required_scopes=("read",),
        eligibility="ONLINE_SUCCESS_VERIFIED_FOR_EXACT_CONTEXT",
        max_age_seconds=3600,
        proof_ref="synthetic/M3-08a/applicability",
        source_sha256="a" * 64,
        checked_by="SYNTHETIC",
        checked_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(hours=1),
    )
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_lookup_source(owner, d["tenant"], proof)
    state: dict[str, Any] = dict(fail=False, empty=False)
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if state["fail"]:
            return httpx.Response(200, json=dict(code=11002))
        number, size = int(request.url.params["page"]), int(request.url.params["page_size"])
        rows = (
            []
            if state["empty"]
            else [
                dict(
                    audience_id=i,
                    account_id=int(ctx.native_account_id),
                    name="真实资源" + str(i),
                    status="SUCCESS",
                    online_status="OFFLINE" if i == 2 else "ONLINE",
                    description="PRIVATE",
                )
                for i in (1, 2, 3)
            ]
        )
        if number > 1 and state.get("duplicate"):
            rows[-1]["audience_id"] = 1
        if number > 1 and state.get("drift"):
            rows.append(
                dict(
                    audience_id=4,
                    account_id=int(ctx.native_account_id),
                    name="real4",
                    status="SUCCESS",
                    online_status="ONLINE",
                )
            )
        total = len(rows)
        return httpx.Response(
            200,
            json=dict(
                code=0,
                data=dict(
                    list=rows[(number - 1) * size : number * size],
                    page_info=dict(
                        page=number,
                        page_size=size,
                        total_number=total,
                        total_page=max(1, (total + size - 1) // size),
                    ),
                ),
            ),
        )

    async def tokens(_: Any) -> SecretStr:
        return SecretStr("synthetic-token")

    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(native_signer, transport=httpx.MockTransport(upstream)),
            archive,
            policy=LOOKUP_ARCHIVE,
            decoder=decode_verdict,
        ),
        governance,
        app,
    )
    reader = AudienceLookupSync(
        SyncStore(
            d["engine"],
            CursorCodec(
                {1: SecretBytes(secrets.token_bytes(32))}, 1, SecretStr(secrets.token_urlsafe(48))
            ),
            policy=LOOKUP_POLICY,
        ),
        client,
        tokens,
        (LOOKUP_PATH,),
    )
    service = LookupService(reader, SecretStr(secrets.token_urlsafe(48)))
    return d | dict(reader=reader, lookup=service, ctx=ctx, proof=proof, calls=calls, state=state)


@pytest.mark.asyncio
async def test_complete_paging_search_eligibility_and_no_fake_keyword(
    lookups: dict[str, Any],
) -> None:
    d = lookups
    run = uuid4()
    await d["reader"].sync(d["ctx"], d["context"], run, page_size=2)
    await d["reader"].sync(d["ctx"], d["context"], run, page_size=2)
    assert len(d["calls"]) == 2
    first = d["lookup"].lookup(d["ctx"], d["context"], "audiences", page_size=1)
    assert first["items"][0]["platformId"] == "1" and first["items"][0]["eligible"]
    second = d["lookup"].lookup(
        d["ctx"], d["context"], "audiences", page_size=1, cursor=first["nextCursor"]
    )
    assert second["items"][0]["platformId"] == "2" and not second["items"][0]["eligible"]
    assert d["lookup"].lookup(d["ctx"], d["context"], "audiences", q="任意新关键词")["items"] == []
    assert "PRIVATE" not in json.dumps(first)


@pytest.mark.asyncio
async def test_missing_source_error_and_cursor_query_context_fence(lookups: dict[str, Any]) -> None:
    d = lookups
    with pytest.raises(ApiError):
        d["lookup"].lookup(d["ctx"], d["context"], "audiences")
    await d["reader"].sync(d["ctx"], d["context"], uuid4(), page_size=2)
    page = d["lookup"].lookup(d["ctx"], d["context"], "audiences", page_size=1)
    with pytest.raises(ApiError):
        d["lookup"].lookup(
            d["ctx"], d["context"], "audiences", q="changed", cursor=page["nextCursor"], page_size=1
        )
    with pytest.raises(ApiError):
        d["lookup"].lookup(d["ctx"], d["context"], "keywords", q="new")
    d["state"]["fail"] = True
    with pytest.raises((ApiError, ValueError)):
        await d["reader"].sync(d["ctx"], d["context"], uuid4(), page_size=2)
    with pytest.raises(ApiError):
        d["lookup"].lookup(d["ctx"], d["context"], "audiences")


@pytest.mark.asyncio
async def test_authorized_empty_is_distinct_from_unintegrated(lookups: dict[str, Any]) -> None:
    d = lookups
    d["state"]["empty"] = True
    await d["reader"].sync(d["ctx"], d["context"], uuid4())
    page = d["lookup"].lookup(d["ctx"], d["context"], "audiences")
    assert page["items"] == [] and page["nextCursor"] is None
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(text("UPDATE resource_lookup_contexts SET active=false"))
    with pytest.raises(ApiError):
        d["lookup"].lookup(d["ctx"], d["context"], "audiences")


@pytest.mark.asyncio
async def test_options_http_uses_server_context_and_frontend_lookup_page(
    lookups: dict[str, Any],
) -> None:
    from pathlib import Path

    from yuliang.modules.connectors.resources import (
        ApplicationResource,
        ResourceBundle,
        SecretReference,
    )
    from yuliang.modules.connectors.runtime import PlatformRuntime

    from .test_configuration_context import query

    d = lookups
    await d["reader"].sync(d["ctx"], d["context"], uuid4(), page_size=2)
    # 合成应用配置，使用实际runtime选择器、HTTP会话、服务及PG权限链。
    runtime = PlatformRuntime(d["engine"], d["settings"], ResourceBundle(applications=()))
    app_id = d["reader"].client.application_id
    runtime.bundle = ResourceBundle.model_construct(
        applications=(
            ApplicationResource.model_construct(
                id=app_id,
                app_id="synthetic-app",
                mode="ADQ_OAUTH",
                secret=SecretReference(file=Path("/synthetic/not-read.env"), key="SYNTHETIC_KEY"),
                source_root=Path("/synthetic"),
                enabled=True,
                tenant_id=d["tenant"],
                connector_id="adq",
                account_id=d["context"].account_id,
                shop_id=d["shop"],
            ),
        )
    )
    runtime.audience_lookups[app_id] = d["reader"]
    runtime.lookup_key = d["lookup"].key
    d["app"].state.platform_runtime = runtime
    response = d["client"].get(
        "/v1/delivery/configuration/options",
        params=query(d["context"]) | dict(kind="audiences", q="真实资源"),
    )
    assert response.status_code == 200, response.text
    page = response.json()["data"]
    assert page["contractVersion"] == "advertising-v9.0" and page["source"] == "API"
    assert page["tenantId"] == str(d["tenant"]) and page["contextKey"] == d["context"].context_key
    assert page["kind"] == "audiences" and len(page["items"]) == 3
    assert all(isinstance(item["eligible"], bool) for item in page["items"])
    assert page["nextCursor"] is None and page["reason"]
    assert (
        d["client"]
        .get(
            "/v1/delivery/configuration/options",
            params=query(d["context"])
            | dict(kind="audiences", bindingId=str(d["proof"].binding_id)),
        )
        .status_code
        == 422
    )
    d["client"].cookies.clear()
    assert (
        d["client"]
        .get(
            "/v1/delivery/configuration/options",
            params=query(d["context"]) | dict(kind="audiences"),
        )
        .status_code
        == 401
    )
    d["app"].state.platform_runtime = None


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["duplicate", "drift"])
async def test_cross_page_duplicate_and_total_drift_fail_closed(
    lookups: dict[str, Any], fault: str
) -> None:
    d = lookups
    d["state"][fault] = True
    with pytest.raises(SyncConflict) as error:
        await d["reader"].sync(d["ctx"], d["context"], uuid4(), page_size=2)
    assert getattr(error.value, "reason", None) in {
        "LOOKUP_DUPLICATE_RESOURCE",
        "LOOKUP_PAGINATION_CHANGED",
    }
    with pytest.raises(ApiError):
        d["lookup"].lookup(d["ctx"], d["context"], "audiences")


@pytest.mark.asyncio
async def test_cursor_context_and_signature_cannot_be_replayed(lookups: dict[str, Any]) -> None:
    d = lookups
    await d["reader"].sync(d["ctx"], d["context"], uuid4(), page_size=2)
    first = d["lookup"].lookup(d["ctx"], d["context"], "audiences", page_size=1)
    cursor = first["nextCursor"]
    with pytest.raises(ApiError):
        d["lookup"].lookup(d["ctx"], d["context"], "audiences", page_size=1, cursor=cursor + "0")
    changed = d["context"].model_copy(update=dict(objective_id="changed"))
    with pytest.raises(ApiError):
        d["lookup"].lookup(d["ctx"], changed, "audiences", page_size=1, cursor=cursor)
    # 相同成员权限上下文里签名token仍绑定真实user_id，不接受另一用户重放。
    foreign = replace(d["ctx"], identity=replace(d["ctx"].identity, user_id=uuid4()))
    with pytest.raises(ApiError):
        d["lookup"].lookup(foreign, d["context"], "audiences", page_size=1, cursor=cursor)
