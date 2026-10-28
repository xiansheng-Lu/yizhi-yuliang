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
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.connectors.shop_product_sync import ShopProductIdSync
from yuliang.modules.connectors.shop_products import PRODUCT_ID_POLICY
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
async def products(configuration_ready: dict[str, Any], governance: Any) -> dict[str, Any]:
    data = configuration_ready
    base_ctx, _, _, archive = setup(data)
    entry = next(
        r for r in data["bundle"].records if r["normalizedPath"] == "/channels/ec/product/list/get"
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
        if body["next_key"] and state["fail"]:
            return httpx.Response(200, json=dict(errcode=1))
        return httpx.Response(
            200,
            json=dict(
                errcode=0,
                product_ids=["18446744073709551615"] if body["next_key"] else ["1"],
                next_key="" if body["next_key"] else "opaque-next",
            ),
        )

    async def tokens(_: Any) -> SecretStr:
        return SecretStr("synthetic-token")

    client = GovernedPlatformClient(
        RecordedPlatformClient(
            PlatformHttpClient(native_signer, transport=httpx.MockTransport(upstream)),
            archive,
            policy=ArchivePolicy(fields=frozenset({"errcode", "product_ids", "next_key"})),
            decoder=decode_verdict,
        ),
        governance,
        app,
    )
    reader = ShopProductIdSync(
        SyncStore(
            data["engine"],
            CursorCodec(
                {1: SecretBytes(secrets.token_bytes(32))}, 1, SecretStr(secrets.token_urlsafe(48))
            ),
            policy=PRODUCT_ID_POLICY,
        ),
        client,
        tokens,
        (binding.normalized_path,),
        ("read",),
    )
    return data | dict(productctx=ctx, reader=reader, calls=calls, state=state)


@pytest.mark.asyncio
async def test_product_ids_atomic_pages_and_completed_run_no_resend(
    products: dict[str, Any],
) -> None:
    d = products
    run = uuid4()
    result = await d["reader"].sync(d["productctx"], run, page_size=1)
    assert result["state"] == "SUCCESS"
    assert len(d["calls"]) == 2
    await d["reader"].sync(d["productctx"], run, page_size=1)
    assert len(d["calls"]) == 2
    with business_transaction(d["engine"], d["identity"]) as s:
        mirrors = s.execute(
            text(
                "SELECT native_id,projection FROM sync_resource_mirrors "
                "WHERE source_id=:id ORDER BY native_id"
            ),
            dict(id=result["sourceId"]),
        ).all()
        assert [m.native_id for m in mirrors] == ["1", "18446744073709551615"]
        assert all(set(m.projection["data"]) == {"product_id", "visibility"} for m in mirrors)
        assert (
            s.execute(
                text("SELECT count(*) FROM sync_pages WHERE run_id=:run"), dict(run=run)
            ).scalar_one()
            == 2
        )


@pytest.mark.asyncio
async def test_failed_page_resumes_and_revoked_scope_blocks(products: dict[str, Any]) -> None:
    d = products
    d["state"]["fail"] = True
    run = uuid4()
    with pytest.raises(ApiError):
        await d["reader"].sync(d["productctx"], run, page_size=1)
    d["state"]["fail"] = False
    result = await d["reader"].sync(d["productctx"], run, page_size=1)
    assert result["state"] == "SUCCESS"
    assert [c["next_key"] for c in d["calls"]] == ["", "opaque-next", "opaque-next"]
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET status='REVOKED' WHERE id=:id"),
            dict(id=d["authorization"]),
        )
    with pytest.raises(ApiError):
        await d["reader"].sync(d["productctx"], uuid4())
    assert len(d["calls"]) == 3


@pytest.mark.asyncio
async def test_new_partial_run_preserves_old_product_observations(products: dict[str, Any]) -> None:
    d = products
    first = await d["reader"].sync(d["productctx"], uuid4(), page_size=1)
    d["state"]["fail"] = True
    with pytest.raises(ApiError):
        await d["reader"].sync(d["productctx"], uuid4(), page_size=1)
    with business_transaction(d["engine"], d["identity"]) as session:
        values = (
            session.execute(
                text("SELECT native_id FROM sync_resource_mirrors WHERE source_id=:id"),
                dict(id=first["sourceId"]),
            )
            .scalars()
            .all()
        )
        assert set(values) == {"1", "18446744073709551615"}


@pytest.mark.asyncio
async def test_missing_scope_blocks_before_network(products: dict[str, Any]) -> None:
    d = products
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET scopes='{}' WHERE id=:id"),
            dict(id=d["authorization"]),
        )
    with pytest.raises(ApiError):
        await d["reader"].sync(d["productctx"], uuid4())
    assert d["calls"] == []
