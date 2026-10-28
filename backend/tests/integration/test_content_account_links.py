"""真实PG/MinIO账号交接意图，不调用平台或产生上传证据。"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.content.account_links_models import LinkChange
from yuliang.modules.content.account_links_service import AccountLinks
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError

from .test_capability_evidence import capability as capability
from .test_cloud_documents import content_ready as content_ready
from .test_configuration_context import configuration_ready as configuration_ready
from .test_content_assets import assets_ready as assets_ready
from .test_content_uploads import blob as blob
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_shop_product_details import details as details
from .test_shop_products import products as products

pytestmark = pytest.mark.integration


@pytest.fixture
async def links_ready(assets_ready: dict[str, Any], oauth_ready: dict[str, Any]) -> dict[str, Any]:
    from pydantic import SecretStr

    from yuliang.modules.catalog.service import link_authorization

    from .test_oauth_vault import SyntheticExchange

    d = assets_ready
    # 商品fixture把原授权转为WXSHOP；为ADQ目标取得独立合成OAuth，不偷用小店scope。
    oauth = oauth_ready
    started = oauth["service"].start(
        oauth["identity"], oauth["session"], oauth["application"], uuid4()
    )
    await oauth["service"].complete(
        started.state, SecretStr("synthetic-code"), oauth["application"], SyntheticExchange(oauth)
    )
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        link_authorization(
            owner,
            started.authorization_id,
            d["read_binding"].account_id,
            "synthetic/M3-20a/adq-target",
        )
    d = d | dict(target_authorization=started.authorization_id)
    return d | dict(
        links=AccountLinks(d["assets"]),
        linkchange=LinkChange(
            productLine="adq", accountId=str(d["read_binding"].account_id), expectedVersion=0
        ),
    )


def counts(d: dict[str, Any]) -> tuple[int, int]:
    with business_transaction(d["engine"], d["identity"]) as s:
        return (
            s.execute(text("SELECT count(*) FROM content_asset_account_links")).scalar_one(),
            s.execute(text("SELECT count(*) FROM content_asset_account_link_events")).scalar_one(),
        )


async def test_real_fixed_asset_link_idempotence_cancel_relink_history(
    links_ready: dict[str, Any],
) -> None:
    d = links_ready
    service = d["links"]
    one = service.link(d["asset"], d["linkchange"])
    assert one["intent"] == "LINKED" and one["version"] == 1
    assert one["link"]["linkedBy"] == str(d["identity"].user_id)
    assert one["upload"]["status"] == "NOT_INTEGRATED" and one["upload"]["platformAssetId"] is None
    assert one["review"]["status"] == "UNKNOWN"
    assert service.link(d["asset"], d["linkchange"]) == one
    assert service.list(d["asset"]) == [one] and counts(d) == (1, 1)
    cancelled = service.cancel(d["asset"], UUID(one["id"]), 1)
    assert cancelled["intent"] == "CANCELLED" and cancelled["version"] == 2
    assert service.cancel(d["asset"], UUID(one["id"]), 1) == cancelled
    relinked = service.link(d["asset"], d["linkchange"].model_copy(update=dict(expectedVersion=2)))
    assert relinked["id"] == one["id"] and relinked["version"] == 3 and counts(d) == (1, 3)
    with business_transaction(d["engine"], d["identity"]) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM content_asset_references WHERE kind='DELIVERY'")
            ).scalar_one()
            == 0
        )
    with pytest.raises(DBAPIError), business_transaction(d["engine"], d["identity"]) as s:
        s.execute(text("UPDATE content_asset_account_link_events SET action='CANCELLED'"))


async def test_same_key_concurrency_and_stale_version_rejected(links_ready: dict[str, Any]) -> None:
    d = links_ready
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: d["links"].link(d["asset"], d["linkchange"]), range(2)))
    assert replies[0] == replies[1] and counts(d) == (1, 1)
    with pytest.raises(ApiError):
        d["links"].cancel(d["asset"], UUID(replies[0]["id"]), 99)
    assert counts(d) == (1, 1)


async def test_cancel_survives_revoked_oauth_and_head_offline(links_ready: dict[str, Any]) -> None:
    d = links_ready
    saved = d["links"].link(d["asset"], d["linkchange"])
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE provider_authorizations SET status='REVOKED' WHERE id=:id"),
            dict(id=d["target_authorization"]),
        )
    with patch.object(
        d["blob"], "check_version", side_effect=AssertionError("must not HEAD cancel")
    ):
        value = d["links"].cancel(d["asset"], UUID(saved["id"]), 1)
    assert value["intent"] == "CANCELLED" and counts(d) == (1, 2)
    with pytest.raises(ApiError):
        d["links"].link(d["asset"], d["linkchange"].model_copy(update=dict(expectedVersion=2)))


@pytest.mark.parametrize("failure", ["snapshot", "entity", "expired", "unknown_account"])
async def test_current_target_scope_and_authorization_rechecked(
    links_ready: dict[str, Any], failure: str
) -> None:
    d = links_ready
    change = d["linkchange"]
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if failure == "snapshot":
            owner.execute(
                text(
                    "UPDATE authorization_account_links SET native_id_snapshot='wrong' "
                    "WHERE authorization_id=:id"
                ),
                dict(id=d["target_authorization"]),
            )
        elif failure == "entity":
            owner.execute(text("UPDATE shops SET entity_id=NULL WHERE id=:id"), dict(id=d["shop"]))
        elif failure == "expired":
            owner.execute(
                text(
                    "UPDATE provider_authorizations "
                    "SET expires_at=clock_timestamp()-interval '1 second' "
                    "WHERE id=:id"
                ),
                dict(id=d["target_authorization"]),
            )
        else:
            change = change.model_copy(update=dict(accountId=str(uuid4())))
    with pytest.raises(ApiError):
        d["links"].link(d["asset"], change)
    assert counts(d) == (0, 0)


async def test_intent_does_not_prevent_archive_and_cancel_needs_current_management(
    links_ready: dict[str, Any],
) -> None:
    d = links_ready
    saved = d["links"].link(d["asset"], d["linkchange"])
    d["assets"].archive(d["asset"])
    assert d["links"].cancel(d["asset"], UUID(saved["id"]), 1)["intent"] == "CANCELLED"
    with pytest.raises(ApiError):
        d["links"].link(d["asset"], d["linkchange"].model_copy(update=dict(expectedVersion=2)))
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM role_grants WHERE membership_id=:id"),
            dict(id=d["identity"].membership_id),
        )
    with pytest.raises(ApiError):
        d["links"].cancel(d["asset"], UUID(saved["id"]), 2)


async def test_event_failure_rolls_back_intent(links_ready: dict[str, Any]) -> None:
    d = links_ready
    with (
        patch.object(d["links"], "_event", side_effect=RuntimeError("synthetic-event-failure")),
        pytest.raises(RuntimeError),
    ):
        d["links"].link(d["asset"], d["linkchange"])
    assert counts(d) == (0, 0)


async def test_real_http_cookie_csrf_and_frontend_field_fixture(
    links_ready: dict[str, Any],
) -> None:
    import json
    import os
    from pathlib import Path

    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from yuliang.main import create_app

    from .test_infrastructure import local_url
    from .test_password_sessions import challenge, sign_in

    d = links_ready
    settings = d["settings"].model_copy(
        update=dict(
            blob_enabled=True,
            blob_endpoint_url=local_url("YULIANG_TEST_S3_URL"),
            blob_bucket=d["blob"].bucket,
            blob_access_key=SecretStr("yuliang-local"),
            blob_secret_key=SecretStr(os.environ["YULIANG_TEST_S3_SECRET"]),
        )
    )
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        sign_in(d | dict(client=client))
        url = "/v1/content/assets/" + str(d["asset"]) + "/account-links"
        payload = d["linkchange"].model_dump()
        assert (
            client.post(url, json=payload, headers={"X-Shop-Id": str(d["shop"])}).status_code == 403
        )
        headers = {"X-Shop-Id": str(d["shop"]), "X-CSRF-Token": challenge(client)}
        reply = client.post(url, json=payload, headers=headers)
        assert reply.status_code == 200, reply.text
        value = reply.json()["data"]
        assert client.get(url, headers=headers).json()["data"] == [value]
        assert (
            client.post(url, json=payload | dict(linkedBy="forged"), headers=headers).status_code
            == 422
        )
        assert client.get(url + "?tenantId=forged", headers=headers).status_code == 422
        captured = (
            Path(__file__).resolve().parents[3] / ".runtime/content-account-links-fixture.json"
        )
        captured.parent.mkdir(exist_ok=True)
        captured.write_text(
            json.dumps(
                dict(
                    value=value,
                    asset=d["assets"].get(d["asset"]),
                    owner=dict(
                        mode="API",
                        tenantId=str(d["tenant"]),
                        userId=str(d["identity"].user_id),
                        shopId=str(d["shop"]),
                    ),
                )
            ),
            encoding="utf-8",
        )
        cancelled = client.post(
            url + "/" + value["id"] + "/cancel", json=dict(expectedVersion=1), headers=headers
        )
        assert cancelled.status_code == 200 and cancelled.json()["data"]["intent"] == "CANCELLED"


@pytest.mark.parametrize("scope", ["tenant", "shop", "actor"])
async def test_cross_scope_direct_service_cannot_create_intent(
    links_ready: dict[str, Any], scope: str
) -> None:
    from yuliang.modules.content.assets import AssetService
    from yuliang.platform.db import VerifiedIdentity

    d = links_ready
    identity = d["identity"]
    shop = d["shop"]
    if scope == "tenant":
        identity = VerifiedIdentity(d["tenant"] + 9999, identity.membership_id, identity.user_id)
    elif scope == "shop":
        shop = uuid4()
    else:
        identity = VerifiedIdentity(d["tenant"], identity.membership_id, uuid4())
    service = AccountLinks(AssetService(d["engine"], identity, shop, d["blob"]))
    with pytest.raises(ApiError):
        service.link(d["asset"], d["linkchange"])
    assert counts(d) == (0, 0)


async def test_missing_fixed_version_cannot_add_link(links_ready: dict[str, Any]) -> None:
    from yuliang.infrastructure.blob import BlobError

    d = links_ready
    with patch.object(d["blob"], "check_version", side_effect=BlobError("synthetic-unavailable")):
        with pytest.raises(ApiError):
            d["links"].link(d["asset"], d["linkchange"])
    assert counts(d) == (0, 0)
