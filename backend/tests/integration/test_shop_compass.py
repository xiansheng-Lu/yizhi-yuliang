"""真实PG/Redis商品ID分页纵切；上游、主体与scope均为合成。"""

import json
import secrets
from dataclasses import replace
from datetime import date
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretBytes, SecretStr
from sqlalchemy import text

from yuliang.modules.capability.service import register_binding
from yuliang.modules.catalog.service import link_authorization
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.native_reads import decode_verdict, native_signer
from yuliang.modules.connectors.shop_compass_protocol import COMPASS_ARCHIVE_POLICY, COMPASS_POLICY
from yuliang.modules.connectors.shop_compass_sync import ShopCompassSync
from yuliang.modules.connectors.sync_cursor import CursorCodec
from yuliang.modules.connectors.sync_store import SyncStore
from yuliang.modules.reporting.service import FactWriter
from yuliang.platform.db import business_transaction
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
async def compass(configuration_ready: dict[str, Any], governance: Any) -> dict[str, Any]:
    data = configuration_ready
    base_ctx, _, _, archive = setup(data)
    entry = next(
        r
        for r in data["bundle"].records
        if r["normalizedPath"] == "/channels/ec/compass/shop/overall/get"
    )
    account = uuid4()
    binding = data["read_binding"].model_copy(
        update=dict(
            id=uuid4(),
            account_id=account,
            connector_id="wxshop",
            provider="wxshop",
            product_line="wxshop",
            http_method="POST",
            normalized_path=entry["normalizedPath"],
            catalog_id=entry["id"],
        )
    )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO ad_accounts(id,tenant_id,provider,product_line,native_id,name,status) "
                "VALUES(:id,:tenant,'wxshop','wxshop','12345','Synthetic','AUTHORIZED')"
            ),
            dict(id=account, tenant=data["tenant"]),
        )
        owner.execute(
            text(
                "INSERT INTO entity_links(tenant_id,shop_id,account_id) VALUES(:tenant,:shop,:id)"
            ),
            dict(tenant=data["tenant"], shop=data["shop"], id=account),
        )
        owner.execute(
            text("DELETE FROM authorization_account_links WHERE authorization_id=:id"),
            dict(id=data["authorization"]),
        )
        owner.execute(
            text(
                "UPDATE provider_authorizations SET connector_id='wxshop',"
                "provider='wxshop',product_line='wxshop' WHERE id=:id"
            ),
            dict(id=data["authorization"]),
        )
        link_authorization(owner, data["authorization"], account, "synthetic/M1-11/link")
        register_binding(owner, binding)
        app = owner.execute(
            text("SELECT application_id FROM provider_authorizations WHERE id=:id"),
            dict(id=data["authorization"]),
        ).scalar_one()
    populate(data, execution=True, target=binding)
    ctx = replace(
        base_ctx,
        authorization_id=data["authorization"],
        account_id=account,
        native_account_id="12345",
        connector_id="wxshop",
        provider="wxshop",
        product_line="wxshop",
        binding_id=binding.id,
        catalog_id=binding.catalog_id,
    )
    calls: list[Any] = []
    state: dict[str, Any] = dict(fail=False)

    def upstream(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append(body)
        if state["fail"]:
            return httpx.Response(200, json=dict(errcode=1))
        return httpx.Response(
            200, json=dict(errcode=0, data=dict(pay_gmv="0", pay_order_cnt="2", phone="PRIVATE"))
        )

    async def tokens(_: Any) -> SecretStr:
        return SecretStr("synthetic-token")

    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(native_signer, transport=httpx.MockTransport(upstream)),
            archive,
            policy=COMPASS_ARCHIVE_POLICY,
            decoder=decode_verdict,
        ),
        governance,
        app,
    )
    reader = ShopCompassSync(
        SyncStore(
            data["engine"],
            CursorCodec(
                {1: SecretBytes(secrets.token_bytes(32))}, 1, SecretStr(secrets.token_urlsafe(48))
            ),
            policy=COMPASS_POLICY,
        ),
        client,
        tokens,
        (binding.normalized_path,),
        ("read",),
        FactWriter(data["engine"], SecretStr(secrets.token_urlsafe(48))),
    )
    return data | dict(productctx=ctx, reader=reader, calls=calls, state=state)


@pytest.mark.asyncio
async def test_compass_atomic_observations_idempotent_and_no_zero_fill(
    compass: dict[str, Any],
) -> None:
    d = compass
    run = uuid4()
    result = await d["reader"].sync(d["productctx"], run, day=date(2026, 10, 20))
    assert result["state"] == "SUCCESS"
    await d["reader"].sync(d["productctx"], run, day=date(2026, 10, 20))
    assert len(d["calls"]) == 1 and d["calls"][0] == {"ds": "20261020"}
    with business_transaction(d["engine"], d["identity"]) as s:
        rows = (
            s.execute(
                text("SELECT observation FROM shop_compass_observations WHERE run_id=:id"),
                dict(id=run),
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0]["values"]["pay_gmv"] == "0"
        assert rows[0]["values"]["pay_refund_gmv"] is None
        assert "PRIVATE" not in json.dumps(rows)
        assert "TIMEZONE_UNCONFIRMED" in rows[0]["gaps"]


@pytest.mark.asyncio
async def test_compass_failure_keeps_prior_observations(compass: dict[str, Any]) -> None:
    d = compass
    await d["reader"].sync(d["productctx"], uuid4(), day=date(2026, 10, 20))
    d["state"]["fail"] = True
    with pytest.raises((ApiError, ValueError)):
        await d["reader"].sync(d["productctx"], uuid4(), day=date(2026, 10, 20))
    with business_transaction(d["engine"], d["identity"]) as s:
        assert s.execute(text("SELECT count(*) FROM shop_compass_observations")).scalar_one() == 1


@pytest.mark.asyncio
async def test_compass_verified_contract_projects_separate_business_facts(
    compass: dict[str, Any],
) -> None:
    from datetime import UTC, datetime, timedelta

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
        proof_ref="synthetic/M1-12/contract",
        source_sha256="a" * 64,
        checked_by="SYNTHETIC_TEST",
        checked_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(hours=1),
    )
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_compass_contract(owner, d["tenant"], proof)
    await d["reader"].sync(ctx, uuid4(), day=date(2026, 10, 20))
    with business_transaction(d["engine"], d["identity"]) as s:
        rows = s.execute(
            text(
                "SELECT product_line,metric_name,value_num FROM metric_facts WHERE account_id=:id"
            ),
            dict(id=ctx.account_id),
        ).all()
        assert {r.product_line for r in rows} == {"wxshop"}
        assert {r.metric_name: r.value_num for r in rows} == {
            "paid_gmv_minor": 0,
            "paid_order_count": 2,
        }
        assert (
            s.execute(text("SELECT contract_id FROM shop_compass_observations")).scalar_one()
            == proof.id
        )


@pytest.mark.asyncio
async def test_runtime_cannot_self_grant_compass_contract(compass: dict[str, Any]) -> None:
    from sqlalchemy.exc import DBAPIError

    d = compass
    with pytest.raises(DBAPIError):
        with business_transaction(d["engine"], d["identity"]) as s:
            s.execute(text("UPDATE shop_compass_contracts SET active=true"))


@pytest.mark.asyncio
async def test_compass_rotation_requires_new_run_and_preserves_logical_source(
    compass: dict[str, Any],
) -> None:
    from yuliang.modules.connectors.sync_store import SyncConflict

    d = compass
    ctx = d["productctx"]
    run = uuid4()
    first = await d["reader"].sync(ctx, run, day=date(2026, 10, 20))
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET version=version+1 WHERE id=:id"),
            dict(id=ctx.authorization_id),
        )
    with pytest.raises(SyncConflict):
        await d["reader"].sync(ctx, run, day=date(2026, 10, 20))
    second = await d["reader"].sync(ctx, uuid4(), day=date(2026, 10, 20))
    assert first["sourceId"] == second["sourceId"]
    assert len(d["calls"]) == 2


@pytest.mark.asyncio
async def test_compass_failed_projection_rolls_back_page_and_mirror(
    compass: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import yuliang.modules.connectors.shop_compass_sync as module

    d = compass

    def fail(*args: Any) -> None:
        raise ValueError("SYNTHETIC_PROJECT_FAILURE")

    monkeypatch.setattr(module, "save_compass", fail)
    with pytest.raises(ValueError):
        await d["reader"].sync(d["productctx"], uuid4(), day=date(2026, 10, 20))
    with business_transaction(d["engine"], d["identity"]) as s:
        assert s.execute(text("SELECT count(*) FROM shop_compass_observations")).scalar_one() == 0
        assert s.execute(text("SELECT count(*) FROM sync_pages")).scalar_one() == 0


@pytest.mark.asyncio
async def test_compass_status_empty_zero_error_and_scope_revocation(
    compass: dict[str, Any],
) -> None:
    d = compass
    ctx = d["productctx"]
    assert d["reader"].observations(ctx, day=date(2026, 10, 20))["state"] == "NOT_SYNCED"
    await d["reader"].sync(ctx, uuid4(), day=date(2026, 10, 20))
    view = d["reader"].observations(ctx, day=date(2026, 10, 20))
    assert view["state"] == "OBSERVED" and view["sourceUpdatedAt"] is None
    d["state"]["fail"] = True
    with pytest.raises((ApiError, ValueError)):
        await d["reader"].sync(ctx, uuid4(), day=date(2026, 10, 20))
    assert d["reader"].observations(ctx, day=date(2026, 10, 20))["state"] == "UPSTREAM_ERROR"
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET scopes='{}' WHERE id=:id"),
            dict(id=ctx.authorization_id),
        )
    with pytest.raises(ApiError):
        d["reader"].observations(ctx, day=date(2026, 10, 20))
