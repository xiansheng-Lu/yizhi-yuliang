"""真实PG/Redis、会话HTTP与权限函数；供应商授权/HTTP仍为合成输入。"""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.capability.models import Binding
from yuliang.modules.catalog.service import ConnectionService, link_authorization
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_capability_evidence import capability as capability
from .test_capability_evidence import populate
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import challenge
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_platform_payloads import decoder, parameters, signer
from .test_resource_freshness import health_ready as health_ready
from .test_resource_freshness import send
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


@pytest.fixture
def connected(health_ready: dict[str, Any], oauth_ready: dict[str, Any]) -> dict[str, Any]:
    data, oauth = health_ready, oauth_ready
    populate(data, execution=True)
    ctx = data["ctx"]
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        link_authorization(owner, ctx.authorization_id, ctx.account_id, "synthetic/M1-17/discovery")
    service = ConnectionService(
        data["engine"],
        ctx.identity,
        oauth["session"],
        uuid4(),
        SecretStr(oauth["settings"].auth_audit_secret.get_secret_value()),
        implemented_reads=frozenset({"adq"}),
    )
    return data | {"management": service, "http": oauth["client"], "oauth": oauth}


def path(data: dict[str, Any]) -> str:
    return f"/v1/connections/{data['ctx'].authorization_id}"


def remove(data: dict[str, Any], digest: str, *, csrf: bool = True) -> httpx.Response:
    return cast(
        httpx.Response,
        data["http"].request(
            "DELETE",
            path(data),
            json={"impactDigest": digest},
            headers={"X-CSRF-Token": challenge(data["http"])} if csrf else {},
        ),
    )


def test_http_projection_actual_scopes_unknown_checks_and_no_secrets(
    connected: dict[str, Any],
) -> None:
    data = connected
    response = data["http"].get("/v1/connections")
    assert response.status_code == 200, response.text
    view = response.json()["data"]
    assert len(view["connectors"]) == 8
    assert all(c["status"] == "UNKNOWN" and c["checkedAt"] is None for c in view["connectors"])
    grant = view["connections"][0]
    assert grant["status"] == "ACTIVE" and grant["scopes"] == ["read", "write"]
    assert grant["accountIds"] == [str(data["ctx"].account_id)] and grant["operations"] == []
    assert not grant["reauthorizationAvailable"]
    assert "token" not in response.text.lower() and "synthetic-private" not in response.text
    assert response.headers["cache-control"] == "no-store"
    refresh = data["http"].post(
        path(data) + "/refresh", headers={"X-CSRF-Token": challenge(data["http"])}
    )
    assert (
        refresh.status_code == 422 and refresh.json()["error"]["code"] == "CAPABILITY_UNAVAILABLE"
    )


def test_verified_read_projection_and_real_stale_health(connected: dict[str, Any]) -> None:
    data = connected
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        row = (
            owner.execute(
                text("SELECT * FROM capability_bindings WHERE id=:id"),
                {"id": data["readctx"].binding_id},
            )
            .mappings()
            .one()
        )
        binding = Binding.model_validate({k: row[k] for k in Binding.model_fields})
    populate(data, execution=True, target=binding)
    view = data["management"].listing()
    assert view["connectors"][0]["status"] == "READ_ONLY"
    assert all(o["kind"] == "READ" for o in view["connections"][0]["operations"])
    resource = view["connections"][0]["resources"][0]
    assert resource["status"] == "FRESH" and resource["sourceUpdatedAt"] < resource["queriedAt"]
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""UPDATE sync_pages SET source_updated_at=source_updated_at-interval '1 hour',
        fetched_at=fetched_at-interval '1 hour' WHERE run_id=:id"""),
            {"id": data["good_lease"].run_id},
        )
    assert data["management"].listing()["connectors"][0]["status"] == "DEGRADED"


async def test_exact_impact_freeze_history_and_old_key_replay(connected: dict[str, Any]) -> None:
    data = connected
    outbound = uuid4()
    await send(data, outbound=outbound)
    preview = data["http"].get(path(data) + "/disconnect-impact").json()["data"]
    assert preview["accountIds"] == [str(data["ctx"].account_id)]
    assert set(preview["bindingIds"]) == {
        str(data["ctx"].binding_id),
        str(data["readctx"].binding_id),
    }
    assert preview["sourceIds"] == [str(data["source"])]
    assert (
        preview["inFlightReadbackIds"] == [str(outbound)] and preview["preservedRequestCount"] == 2
    )
    response = remove(data, preview["impactDigest"])
    assert response.status_code == 200, response.text
    actual = response.json()["data"]
    assert actual["status"] == "REVOKED" and actual["version"] == 2
    assert actual["providerRevocation"] == "NOT_REQUESTED"
    assert actual["appliedImpact"] == {k: v for k, v in preview.items() if k != "impactDigest"}
    if evidence_path := os.getenv("YULIANG_CONNECTION_EVIDENCE"):
        Path(evidence_path).write_text(
            json.dumps(
                {
                    "scope": "synthetic identities and platform HTTP; actual PG/Redis and API",
                    "disconnectRequestId": response.headers["X-Request-Id"],
                    "authorizationId": actual["authorizationId"],
                    "writeOutboundId": str(outbound),
                    "accountIds": actual["appliedImpact"]["accountIds"],
                    "bindingIds": actual["appliedImpact"]["bindingIds"],
                    "sourceIds": actual["appliedImpact"]["sourceIds"],
                    "inFlightReadbackIds": actual["appliedImpact"]["inFlightReadbackIds"],
                    "platformCalls": 0,
                    "paidCalls": 0,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    assert remove(data, preview["impactDigest"]).json()["data"]["replayed"] is True
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM capability_bindings WHERE active")
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text("SELECT enabled FROM sync_sources WHERE id=:id"), {"id": data["source"]}
            ).scalar_one()
            is False
        )
        assert session.execute(text("SELECT count(*) FROM platform_requests")).scalar_one() == 2
        assert session.execute(text("SELECT count(*) FROM platform_raw_payloads")).scalar_one() == 4
        assert session.execute(text("SELECT count(*) FROM sync_resource_mirrors")).scalar_one() == 1
        assert session.execute(text("SELECT count(*) FROM capability_assertions")).scalar_one() > 0
        assert (
            session.execute(
                text("SELECT count(*) FROM audit_logs WHERE action='connection.disconnect'")
            ).scalar_one()
            == 1
        )
        assert session.execute(
            text("SELECT octet_length(access_token_enc)>28 FROM provider_authorizations")
        ).scalar_one()
    assert (await send(data, outbound=outbound)).metadata["externalOutcome"] == "ACCEPTED"
    with pytest.raises(ApiError):
        await send(data)
    assert len(data["writes"]) == 1
    with pytest.raises(ApiError):
        data["store"].begin(
            data["readctx"], data["readspec"], "plan.state", uuid4(), parameters(data["readctx"])
        )


def test_csrf_tamper_and_strict_body_no_state_change(connected: dict[str, Any]) -> None:
    data = connected
    view = data["management"].preview(data["ctx"].authorization_id)
    assert remove(data, view["impactDigest"], csrf=False).status_code == 403
    assert remove(data, "0" * 64).status_code == 409
    wrong = data["http"].request(
        "DELETE", path(data), json={"impactDigest": view["impactDigest"], "tenantId": "other"}
    )
    assert wrong.status_code == 422 and "other" not in wrong.text
    assert (
        data["management"].preview(data["ctx"].authorization_id)["authorizationStatus"] == "ACTIVE"
    )


@pytest.mark.parametrize("change", ["role", "scope", "session"])
def test_permission_revocation_between_preview_and_delete(
    connected: dict[str, Any], change: str
) -> None:
    data = connected
    preview = data["management"].preview(data["ctx"].authorization_id)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if change == "role":
            owner.execute(
                text(
                    "DELETE FROM role_grants WHERE membership_id=:member "
                    "AND role_id IN (SELECT id FROM roles WHERE code='OWNER')"
                ),
                {"member": data["member"]},
            )
        elif change == "scope":
            owner.execute(
                text("DELETE FROM data_scopes WHERE membership_id=:member"),
                {"member": data["member"]},
            )
        else:
            owner.execute(
                text("UPDATE sessions SET revoked_at=clock_timestamp() WHERE id=:id"),
                {"id": data["oauth"]["session"]},
            )
    response = remove(data, preview["impactDigest"])
    assert response.status_code in {401, 403}
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert (
            owner.execute(
                text("SELECT status FROM provider_authorizations WHERE id=:id"),
                {"id": data["ctx"].authorization_id},
            ).scalar_one()
            == "ACTIVE"
        )


def test_new_request_invalidates_preview_and_other_tenant_is_hidden(
    connected: dict[str, Any],
) -> None:
    data = connected
    preview = data["management"].preview(data["ctx"].authorization_id)
    data["archive"].begin(
        data["readctx"],
        data["readspec"],
        uuid4(),
        parameters(data["readctx"]),
        ArchivePolicy(),
        None,
        None,
        None,
    )
    assert remove(data, preview["impactDigest"]).status_code == 409
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        other = owner.execute(
            text("INSERT INTO tenants(name) VALUES('Other') RETURNING id")
        ).scalar_one()
    fake = ConnectionService(
        data["engine"],
        VerifiedIdentity(other, data["member"], data["user"]),
        data["oauth"]["session"],
        uuid4(),
        SecretStr("x" * 48),
    )
    with pytest.raises(ApiError) as denied:
        fake.preview(data["ctx"].authorization_id)
    assert denied.value.code == ErrorCode.FORBIDDEN
    assert data["http"].get(f"/v1/connections/{uuid4()}/disconnect-impact").status_code == 404


def test_runtime_cannot_link_revoke_or_impersonate(connected: dict[str, Any]) -> None:
    data = connected
    for sql in (
        "UPDATE provider_authorizations SET status='REVOKED'",
        "UPDATE capability_bindings SET active=false",
        "INSERT INTO authorization_account_links SELECT * FROM authorization_account_links",
    ):
        with (
            pytest.raises(DBAPIError),
            business_transaction(data["engine"], data["identity"]) as session,
        ):
            session.execute(text(sql))
    with (
        pytest.raises(DBAPIError) as denied,
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(
            text(
                "SELECT access.connection_impact(:member,:user,:session,:auth,"
                "false,NULL,NULL,:request)"
            ),
            {
                "member": data["member"],
                "user": data["approver"],
                "session": data["oauth"]["session"],
                "auth": data["ctx"].authorization_id,
                "request": uuid4(),
            },
        )
    assert denied.value.orig.sqlstate == "42501"  # type: ignore[union-attr]
    with pytest.raises(ValueError), data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        other = uuid4()
        owner.execute(
            text("""INSERT INTO ad_accounts(id,tenant_id,provider,product_line,
        native_id,name,status)
        VALUES(:id,:tenant,'tencent','shop',:native,'Other product','AUTHORIZED')"""),
            {"id": other, "tenant": data["tenant"], "native": str(other)},
        )
        link_authorization(owner, data["ctx"].authorization_id, other, "synthetic/mismatch")


def test_audit_failure_rolls_back_entire_disconnect(
    connected: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = connected
    digest = data["management"].preview(data["ctx"].authorization_id)["impactDigest"]

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr("yuliang.modules.catalog.service.record", fail)
    with pytest.raises(RuntimeError):
        data["management"].disconnect(data["ctx"].authorization_id, digest)
    assert data["management"].preview(data["ctx"].authorization_id)["impactDigest"] == digest
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(text("SELECT count(*) FROM connection_disconnections")).scalar_one()
            == 0
        )


def test_private_other_actor_impact_only_exposes_ids(connected: dict[str, Any]) -> None:
    data = connected
    other = replace(
        data["readctx"],
        identity=VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"]),
    )
    outbound = uuid4()
    data["archive"].begin(
        other, data["readspec"], outbound, parameters(other), ArchivePolicy(), None, None, None
    )
    preview = data["management"].preview(data["ctx"].authorization_id)
    assert preview["inFlightReadbackIds"] == [str(outbound)]
    assert preview["preservedRequestCount"] == 2 and "payload" not in str(preview)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM platform_requests WHERE id=:id"), {"id": outbound}
            ).scalar_one()
            == 0
        )


async def test_in_flight_response_still_persists_after_disconnect(
    connected: dict[str, Any],
) -> None:
    data = connected

    async def upstream(request: httpx.Request) -> httpx.Response:
        impact = data["management"].preview(data["ctx"].authorization_id)
        assert len(impact["inFlightReadbackIds"]) == 1
        data["management"].disconnect(data["ctx"].authorization_id, impact["impactDigest"])
        return httpx.Response(200, json={"code": 0})

    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(upstream)),
        data["archive"],
        decoder=decoder,
    )
    result = await client.send(
        data["readctx"],
        data["readspec"],
        uuid4(),
        parameters(data["readctx"]),
        SecretStr("synthetic"),
        target=data["target"],
    )
    assert result.metadata["externalOutcome"] == "READ_SUCCEEDED"
    with pytest.raises(ApiError):
        await client.send(
            data["readctx"],
            data["readspec"],
            uuid4(),
            parameters(data["readctx"]),
            SecretStr("synthetic"),
            target=data["target"],
        )


async def test_disconnect_waits_for_new_intent_transaction(connected: dict[str, Any]) -> None:
    data = connected
    old = data["management"].preview(data["ctx"].authorization_id)
    entered, release = Event(), Event()

    def hold(session: Any) -> None:
        entered.set()
        assert release.wait(5)

    def create() -> None:
        data["archive"].begin(
            data["readctx"],
            data["readspec"],
            uuid4(),
            parameters(data["readctx"]),
            ArchivePolicy(),
            None,
            None,
            None,
            before_persist=hold,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        creating = pool.submit(create)
        assert entered.wait(3)
        disconnecting = pool.submit(
            data["management"].disconnect, data["ctx"].authorization_id, old["impactDigest"]
        )
        await asyncio.sleep(0.05)
        assert not disconnecting.done()
        release.set()
        creating.result(timeout=5)
        with pytest.raises(ApiError) as conflict:
            disconnecting.result(timeout=5)
        assert conflict.value.code == ErrorCode.VERSION_CONFLICT
    current = data["management"].preview(data["ctx"].authorization_id)
    assert current["preservedRequestCount"] == 2
    assert (
        data["management"].disconnect(data["ctx"].authorization_id, current["impactDigest"])[
            "status"
        ]
        == "REVOKED"
    )


def test_oversized_impact_is_rejected_without_truncation(connected: dict[str, Any]) -> None:
    data = connected
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO command_jobs(tenant_id,created_by,shop_id,account_id)
        SELECT :tenant,:user,:shop,:account FROM generate_series(1,1001)"""),
            {
                "tenant": data["tenant"],
                "user": data["user"],
                "shop": data["shop"],
                "account": data["ctx"].account_id,
            },
        )
    response = data["http"].get(path(data) + "/disconnect-impact")
    assert (
        response.status_code == 422
        and response.json()["error"]["code"] == "DATA_COVERAGE_INSUFFICIENT"
    )
