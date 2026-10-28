"""真实PG/Redis商品ID分页纵切；上游、主体与scope均为合成。"""

import json
import secrets
from dataclasses import replace
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
from yuliang.modules.connectors.shop_order_sync import ShopOrderSync
from yuliang.modules.connectors.shop_orders import ORDER_ARCHIVE_POLICY, ORDER_POLICY
from yuliang.modules.connectors.sync_cursor import CursorCodec
from yuliang.modules.connectors.sync_store import SyncStore
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
async def orders(configuration_ready: dict[str, Any], governance: Any) -> dict[str, Any]:
    data = configuration_ready
    base_ctx, _, _, archive = setup(data)
    entry = next(
        r for r in data["bundle"].records if r["normalizedPath"] == "/channels/ec/order/get"
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
            200,
            json=dict(
                errcode=0,
                order=dict(
                    order_id=body["order_id"],
                    status=20,
                    create_time=100,
                    update_time=state.get("time", 200),
                    receiver_name="PRIVATE",
                    phone="PRIVATE",
                    address="PRIVATE",
                    order_detail=dict(price_info=dict(order_price=state.get("price", 100))),
                ),
            ),
        )

    async def tokens(_: Any) -> SecretStr:
        return SecretStr("synthetic-token")

    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(native_signer, transport=httpx.MockTransport(upstream)),
            archive,
            policy=ORDER_ARCHIVE_POLICY,
            decoder=decode_verdict,
        ),
        governance,
        app,
    )
    reader = ShopOrderSync(
        SyncStore(
            data["engine"],
            CursorCodec(
                {1: SecretBytes(secrets.token_bytes(32))}, 1, SecretStr(secrets.token_urlsafe(48))
            ),
            policy=ORDER_POLICY,
        ),
        client,
        tokens,
        (binding.normalized_path,),
        ("read",),
    )
    return data | dict(productctx=ctx, reader=reader, calls=calls, state=state)


@pytest.mark.asyncio
async def test_order_observations_idempotent_private_and_unknown(orders: dict[str, Any]) -> None:
    d = orders
    ctx = d["productctx"]
    run = uuid4()
    result = await d["reader"].sync(ctx, run, kind="ORDER", order_id="123")
    assert result["state"] == "SUCCESS"
    await d["reader"].sync(ctx, run, kind="ORDER", order_id="123")
    assert len(d["calls"]) == 1
    d["state"].update(price=50, time=199)
    await d["reader"].sync(ctx, uuid4(), kind="ORDER", order_id="123")
    with business_transaction(d["engine"], d["identity"]) as s:
        observations = s.execute(text("SELECT values FROM shop_order_observations")).scalars().all()
        assert len(observations) == 2
        assert all(o["unit_status"] == "UNCONFIRMED" for o in observations)
        assert all(o["refund_status"] == "NOT_PROVIDED" for o in observations)
        assert "PRIVATE" not in json.dumps(observations)
        raw = s.execute(text("SELECT payload FROM platform_raw_payloads")).scalars().all()
        assert "PRIVATE" not in json.dumps(raw, default=str)
        assert s.execute(text("SELECT count(*) FROM metric_facts")).scalar_one() == 0


@pytest.mark.asyncio
async def test_order_revoked_scope_no_network(orders: dict[str, Any]) -> None:
    d = orders
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET scopes='{}' WHERE id=:id"),
            dict(id=d["authorization"]),
        )
    with pytest.raises(ApiError):
        await d["reader"].sync(d["productctx"], uuid4(), kind="ORDER", order_id="123")
    assert not d["calls"]


@pytest.fixture
async def settlements(configuration_ready: dict[str, Any], governance: Any) -> dict[str, Any]:
    data = configuration_ready
    base_ctx, _, _, archive = setup(data)
    entry = next(
        r for r in data["bundle"].records if r["normalizedPath"] == "/channels/ec/funds/getbalance"
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
        return httpx.Response(200, json=dict(errcode=0, available_amount=100, pending_amount=0))

    async def tokens(_: Any) -> SecretStr:
        return SecretStr("synthetic-token")

    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(native_signer, transport=httpx.MockTransport(upstream)),
            archive,
            policy=ORDER_ARCHIVE_POLICY,
            decoder=decode_verdict,
        ),
        governance,
        app,
    )
    reader = ShopOrderSync(
        SyncStore(
            data["engine"],
            CursorCodec(
                {1: SecretBytes(secrets.token_bytes(32))}, 1, SecretStr(secrets.token_urlsafe(48))
            ),
            policy=ORDER_POLICY,
        ),
        client,
        tokens,
        (binding.normalized_path,),
        ("read",),
    )
    return data | dict(productctx=ctx, reader=reader, calls=calls, state=state)


@pytest.mark.asyncio
async def test_settlement_proof_read_permission_and_expiry(settlements: dict[str, Any]) -> None:
    from yuliang.modules.connectors.shop_settlement import settlement_view

    d = settlements
    ctx = d["productctx"]
    run = uuid4()
    await d["reader"].sync(ctx, run, kind="SETTLEMENT")

    def view() -> dict[str, Any]:
        with business_transaction(d["engine"], d["identity"]) as session:
            return settlement_view(
                session, ctx, d["reader"].client.application_id, frozenset({"read"}), run
            )

    assert view()["availableMinor"] is None
    proof = uuid4()
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO shop_settlement_proofs
            (id,tenant_id,account_id,shop_id,binding_id,currency,source_sha256,checked_at,
             unit,proof_ref,expires_at)
            VALUES(:id,:tenant,:account,:shop,:binding,'CNY',:digest,clock_timestamp(),
                   'CNY_YUAN_INTEGER','SYNTHETIC',
                   clock_timestamp()+interval '1 hour')"""),
            dict(
                id=proof,
                tenant=d["tenant"],
                account=ctx.account_id,
                shop=ctx.shop_id,
                binding=ctx.binding_id,
                digest=ctx.catalog_sha256,
            ),
        )
    assert view()["availableMinor"] == 10000 and view()["pendingMinor"] == 0
    assert view()["budgetUsable"] is False
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE shop_settlement_proofs SET active=false WHERE id=:id"), dict(id=proof)
        )
    assert view()["availableMinor"] is None
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET status='REVOKED' WHERE id=:id"),
            dict(id=d["authorization"]),
        )
    with pytest.raises(ApiError):
        view()


@pytest.mark.asyncio
async def test_observation_failure_rolls_back_whole_page(
    orders: dict[str, Any], monkeypatch: Any
) -> None:
    d = orders
    original = d["reader"].store.commit_page

    def fail(*args: Any, **kwargs: Any) -> Any:
        project = kwargs["project_facts"]

        def abort(session: Any) -> None:
            project(session)
            raise ValueError("synthetic rollback")

        kwargs["project_facts"] = abort
        return original(*args, **kwargs)

    monkeypatch.setattr(d["reader"].store, "commit_page", fail)
    with pytest.raises(ValueError, match="synthetic rollback"):
        await d["reader"].sync(d["productctx"], uuid4(), kind="ORDER", order_id="123")
    with business_transaction(d["engine"], d["identity"]) as s:
        assert s.execute(text("SELECT count(*) FROM shop_order_observations")).scalar_one() == 0
        assert s.execute(text("SELECT count(*) FROM sync_pages")).scalar_one() == 0


@pytest.mark.asyncio
async def test_wrong_shop_cannot_read_financial_observation(settlements: dict[str, Any]) -> None:
    from yuliang.modules.connectors.shop_settlement import settlement_view

    d = settlements
    ctx = d["productctx"]
    run = uuid4()
    await d["reader"].sync(ctx, run, kind="SETTLEMENT")
    with business_transaction(d["engine"], d["identity"]) as s:
        with pytest.raises(ApiError):
            settlement_view(
                s,
                replace(ctx, shop_id=uuid4()),
                d["reader"].client.application_id,
                frozenset({"read"}),
                run,
            )


@pytest.fixture(params=["ORDER_LIST", "AFTERSALE", "AFTERSALE_LIST"])
async def more_orders(
    configuration_ready: dict[str, Any], governance: Any, request: Any
) -> dict[str, Any]:
    data = configuration_ready
    from yuliang.modules.connectors.shop_orders import SPECS

    kind = request.param
    base_ctx, _, _, archive = setup(data)
    entry = next(r for r in data["bundle"].records if r["normalizedPath"] == SPECS[kind][0])
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
        if kind == "AFTERSALE":
            return httpx.Response(
                200,
                json=dict(
                    errcode=0,
                    after_sale_order=dict(
                        after_sale_order_id=body["after_sale_order_id"],
                        order_id="123",
                        status="SUCCESS",
                        create_time=100,
                        update_time=state.get("time", 200),
                        refund_info=dict(amount=state.get("amount", 50)),
                        details=dict(tel_number="PRIVATE"),
                    ),
                ),
            )
        key = "order_id_list" if kind == "ORDER_LIST" else "after_sale_order_id_list"
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                key: ["2"] if body["next_key"] else ["1"],
                "has_more": not bool(body["next_key"]),
                "next_key": "" if body["next_key"] else "opaque",
            },
        )

    async def tokens(_: Any) -> SecretStr:
        return SecretStr("synthetic-token")

    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(native_signer, transport=httpx.MockTransport(upstream)),
            archive,
            policy=ORDER_ARCHIVE_POLICY,
            decoder=decode_verdict,
        ),
        governance,
        app,
    )
    reader = ShopOrderSync(
        SyncStore(
            data["engine"],
            CursorCodec(
                {1: SecretBytes(secrets.token_bytes(32))}, 1, SecretStr(secrets.token_urlsafe(48))
            ),
            policy=ORDER_POLICY,
        ),
        client,
        tokens,
        (binding.normalized_path,),
        ("read",),
    )
    return data | dict(productctx=ctx, reader=reader, calls=calls, state=state, kind=kind)


@pytest.mark.asyncio
async def test_fixed_sdk_lists_and_aftersale_replay(more_orders: dict[str, Any]) -> None:
    d = more_orders
    kind = d["kind"]
    args = dict(kind=kind, order_id="1")
    if kind == "ORDER_LIST":
        args["parameters_input"] = dict(update_time_range=dict(start_time=100, end_time=200))
    elif kind == "AFTERSALE_LIST":
        args["parameters_input"] = dict(begin_create_time=100, end_create_time=200)
    run = uuid4()
    await d["reader"].sync(d["productctx"], run, **args)
    expected = 1 if kind == "AFTERSALE" else 2
    assert len(d["calls"]) == expected
    await d["reader"].sync(d["productctx"], run, **args)
    assert len(d["calls"]) == expected
    with business_transaction(d["engine"], d["identity"]) as s:
        rows = s.execute(text("SELECT values FROM shop_order_observations")).scalars().all()
        assert len(rows) == expected and "PRIVATE" not in json.dumps(rows)
        if kind == "AFTERSALE":
            assert rows[0]["refund_amount"] == "50" and rows[0]["refund_status"] == "SUCCESS"
            assert rows[0]["unit_status"] == "UNCONFIRMED"
