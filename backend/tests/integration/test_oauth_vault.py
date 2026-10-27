"""真实PG一次性state/原会话/原子加密落库；换码端口为合成实现。"""

import asyncio
import secrets
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import SecretBytes, SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import QueuePool

from yuliang.modules.connectors.base import TokenSet
from yuliang.modules.connectors.oauth import (
    CodeExchangeFailure,
    OAuthApplication,
    OAuthExchangeContext,
    OAuthRejected,
    OAuthService,
)
from yuliang.modules.connectors.token_cipher import TokenCipher
from yuliang.modules.identity.service import digest
from yuliang.platform.db import VerifiedIdentity, business_transaction

from .test_identity_database import database as database
from .test_password_sessions import login_api as login_api
from .test_password_sessions import sign_in

pytestmark = pytest.mark.integration


@pytest.fixture
def oauth_ready(login_api: dict[str, Any]) -> Iterator[dict[str, Any]]:
    api = login_api
    owner = create_engine(
        api["database"]["migration"].replace("postgresql://", "postgresql+psycopg://"),
        hide_parameters=True,
    )
    with owner.begin() as connection:
        connection.execute(text("SET LOCAL ROLE app_owner"))
        connection.execute(
            text("""INSERT INTO role_grants(tenant_id,membership_id,role_id)
            SELECT :tenant,:member,id FROM roles WHERE tenant_id=:tenant AND code='OWNER' """),
            {"tenant": api["tenant"], "member": api["member"]},
        )
    token = sign_in(api)
    with owner.begin() as connection:
        connection.execute(text("SET LOCAL ROLE app_owner"))
        session_id = connection.execute(
            text("SELECT id FROM sessions WHERE token_hash=:hash"), {"hash": digest(token)}
        ).scalar_one()
    identity = VerifiedIdentity(api["tenant"], api["member"], api["user"])
    key = SecretBytes(secrets.token_bytes(32))
    service = OAuthService(
        api["app"].state.identity_service.app,
        SecretStr(secrets.token_urlsafe(48)),
        TokenCipher({1: key}, 1),
    )
    app = OAuthApplication(
        uuid4(),
        "synthetic-app",
        "adq",
        "tencent",
        "adq",
        "https://callback.example.invalid/v1/connections/oauth/tencent/callback",
    )
    try:
        yield api | {
            "identity": identity,
            "session": session_id,
            "service": service,
            "application": app,
            "maintenance": owner,
            "key": key,
        }
    finally:
        owner.dispose()


def begin(data: dict[str, Any]) -> Any:
    return data["service"].start(data["identity"], data["session"], data["application"], uuid4())


class SyntheticExchange:
    def __init__(
        self, data: dict[str, Any], *, failure: bool | None = None, revoke: bool = False
    ) -> None:
        self.data, self.failure, self.revoke = data, failure, revoke
        self.calls: list[OAuthExchangeContext] = []

    async def exchange(self, context: OAuthExchangeContext, code: SecretStr) -> TokenSet:
        self.calls.append(context)
        service: OAuthService = self.data["service"]
        # 端口执行时，服务既没有持有连接，也已经提交STARTED记录。
        assert cast(QueuePool, service.engine.pool).checkedout() == 0
        with business_transaction(service.engine, context.identity) as session:
            assert (
                session.execute(
                    text("SELECT phase FROM authorization_exchange_events WHERE state_id=:id"),
                    {"id": context.state_id},
                ).scalar_one()
                == "STARTED"
            )
        await asyncio.sleep(0)
        if self.revoke:
            with self.data["maintenance"].begin() as owner:
                owner.execute(text("SET LOCAL ROLE app_owner"))
                owner.execute(
                    text("UPDATE memberships SET status='REMOVED' WHERE id=:id"),
                    {"id": context.identity.membership_id},
                )
        if self.failure is not None:
            raise CodeExchangeFailure(unknown=self.failure)
        return TokenSet(
            access_token=SecretStr("synthetic-private-access"),
            refresh_token=SecretStr("synthetic-private-refresh"),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scopes=("read", "write"),
        )


async def test_cookie_free_callback_cipher_archive_and_single_use(
    oauth_ready: dict[str, Any],
) -> None:
    data = oauth_ready
    service = data["service"]
    start = begin(data)
    port = SyntheticExchange(data)
    data["client"].cookies.clear()  # 换设备没有Cookie，原会话仍在DB有效。
    result = await service.complete(
        start.state, SecretStr("synthetic-code"), data["application"], port
    )
    assert len(port.calls) == 1 and port.calls[0].identity == data["identity"]
    assert result["status"] == "ACTIVE" and result["authorizationId"] == str(start.authorization_id)
    assert "token" not in str(result).lower() and "scope" not in result
    with business_transaction(service.engine, data["identity"]) as session:
        row = (
            session.execute(
                text("SELECT * FROM provider_authorizations WHERE id=:id"),
                {"id": start.authorization_id},
            )
            .mappings()
            .one()
        )
        assert row["account_id"] is None and row["key_version"] == 1
        assert b"synthetic-private-access" not in row["access_token_enc"]
        assert b"synthetic-private-refresh" not in row["refresh_token_enc"]
        state = (
            session.execute(text("SELECT * FROM oauth_states WHERE id=:id"), {"id": start.id})
            .mappings()
            .one()
        )
        assert state["state"] == "SUCCEEDED" and state["session_id"] == data["session"]
        assert state["state_digest"] != start.state.get_secret_value()
        events = session.execute(
            text(
                "SELECT phase,code_digest FROM authorization_exchange_events "
                "WHERE state_id=:id ORDER BY id"
            ),
            {"id": start.id},
        ).all()
        assert [row[0] for row in events] == ["STARTED", "SUCCEEDED"]
        assert all(row[1] != "synthetic-code" for row in events)
        assert session.execute(text("SELECT count(*) FROM capability_assertions")).scalar_one() == 0
    tokens = service.tokens(data["identity"], data["session"], start.authorization_id)
    assert tokens.access_token.get_secret_value() == "synthetic-private-access"
    with pytest.raises(OAuthRejected):
        await service.complete(start.state, SecretStr("synthetic-code"), data["application"], port)
    assert len(port.calls) == 1


async def test_tamper_app_redirect_expiry_and_concurrent_replay(
    oauth_ready: dict[str, Any],
) -> None:
    data = oauth_ready
    service = data["service"]
    start = begin(data)
    port = SyntheticExchange(data)
    raw = start.state.get_secret_value()
    tampered = ("A" if raw[0] != "A" else "B") + raw[1:]
    for state, app in [
        (SecretStr(tampered), data["application"]),
        (start.state, replace(data["application"], id=uuid4())),
        (
            start.state,
            replace(data["application"], redirect_uri="https://callback.example.invalid/other"),
        ),
    ]:
        with pytest.raises(OAuthRejected):
            await service.complete(state, SecretStr("synthetic-code"), app, port)
    assert not port.calls
    results = await asyncio.gather(
        *[
            service.complete(start.state, SecretStr("synthetic-code"), data["application"], port)
            for _ in range(2)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) for result in results) == 1
    assert (
        sum(isinstance(result, OAuthRejected) for result in results) == 1 and len(port.calls) == 1
    )
    expired = begin(data)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""UPDATE oauth_states SET created_at=now()-interval '20 minutes',
            expires_at=now()-interval '15 minutes' WHERE id=:id"""),
            {"id": expired.id},
        )
    with pytest.raises(OAuthRejected):
        await service.complete(
            expired.state, SecretStr("synthetic-code"), data["application"], port
        )
    assert len(port.calls) == 1


@pytest.mark.parametrize("mode", ["session", "member", "permission", "scope", "user", "tenant"])
async def test_original_actor_revoked_before_callback_no_exchange(
    oauth_ready: dict[str, Any], mode: str
) -> None:
    data = oauth_ready
    start = begin(data)
    port = SyntheticExchange(data)
    statements = {
        "session": "UPDATE sessions SET revoked_at=now() WHERE id=:id",
        "member": "UPDATE memberships SET status='REMOVED' WHERE id=:id",
        "permission": "DELETE FROM role_permissions WHERE tenant_id=:id "
        "AND permission_code='connection.manage'",
        "scope": "DELETE FROM data_scopes WHERE membership_id=:id",
        "user": "UPDATE users SET status='DISABLED' WHERE id=:id",
        "tenant": "UPDATE tenants SET status='SUSPENDED' WHERE id=:id",
    }
    ids = {
        "session": data["session"],
        "member": data["member"],
        "permission": data["tenant"],
        "scope": data["member"],
        "user": data["user"],
        "tenant": data["tenant"],
    }
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(text(statements[mode]), {"id": ids[mode]})
    with pytest.raises(OAuthRejected):
        await data["service"].complete(
            start.state, SecretStr("synthetic-code"), data["application"], port
        )
    assert not port.calls


@pytest.mark.parametrize("failure", [False, True])
async def test_exchange_failure_no_half_authorization_no_retry(
    oauth_ready: dict[str, Any], failure: bool
) -> None:
    data = oauth_ready
    start = begin(data)
    port = SyntheticExchange(data, failure=failure)
    for _ in range(2):
        with pytest.raises(OAuthRejected):
            await data["service"].complete(
                start.state, SecretStr("synthetic-code"), data["application"], port
            )
    with business_transaction(data["service"].engine, data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM provider_authorizations WHERE id=:id"),
                {"id": start.authorization_id},
            ).scalar_one()
            == 0
        )
        assert session.execute(
            text("SELECT state FROM oauth_states WHERE id=:id"), {"id": start.id}
        ).scalar_one() == ("UNKNOWN" if failure else "FAILED")
    assert len(port.calls) == 1


async def test_revoked_during_exchange_no_active_grant(oauth_ready: dict[str, Any]) -> None:
    data = oauth_ready
    start = begin(data)
    port = SyntheticExchange(data, revoke=True)
    with pytest.raises(OAuthRejected):
        await data["service"].complete(
            start.state, SecretStr("synthetic-code"), data["application"], port
        )
    with business_transaction(data["service"].engine, data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM provider_authorizations WHERE id=:id"),
                {"id": start.authorization_id},
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text("SELECT state FROM oauth_states WHERE id=:id"), {"id": start.id}
            ).scalar_one()
            == "UNKNOWN"
        )


async def test_token_insert_failure_rolls_back_and_cipher_rotation(
    oauth_ready: dict[str, Any],
) -> None:
    data = oauth_ready
    service = data["service"]
    start = begin(data)
    port = SyntheticExchange(data)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(text("REVOKE INSERT ON provider_authorizations FROM app_login"))
    try:
        with pytest.raises(OAuthRejected):
            await service.complete(
                start.state, SecretStr("synthetic-code"), data["application"], port
            )
    finally:
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(text("GRANT INSERT ON provider_authorizations TO app_login"))
    with business_transaction(service.engine, data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM provider_authorizations WHERE id=:id"),
                {"id": start.authorization_id},
            ).scalar_one()
            == 0
        )
    good = begin(data)
    await service.complete(good.state, SecretStr("synthetic-code"), data["application"], port)
    with business_transaction(service.engine, data["identity"]) as session:
        previous = session.execute(
            text("SELECT access_token_enc FROM provider_authorizations WHERE id=:id"),
            {"id": good.authorization_id},
        ).scalar_one()
    new = SecretBytes(secrets.token_bytes(32))
    service.cipher = TokenCipher({1: data["key"], 2: new}, 2)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(text("REVOKE INSERT ON audit_logs FROM app_login"))
    try:
        with pytest.raises(DBAPIError):
            service.rotate(data["identity"], data["session"], good.authorization_id)
        with business_transaction(service.engine, data["identity"]) as session:
            unchanged = session.execute(
                text(
                    "SELECT key_version,access_token_enc FROM provider_authorizations WHERE id=:id"
                ),
                {"id": good.authorization_id},
            ).one()
            assert unchanged == (1, previous)
    finally:
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(text("GRANT INSERT ON audit_logs TO app_login"))
    service.rotate(data["identity"], data["session"], good.authorization_id)
    service.cipher = TokenCipher({2: new}, 2)
    assert (
        service.tokens(
            data["identity"], data["session"], good.authorization_id
        ).access_token.get_secret_value()
        == "synthetic-private-access"
    )
    with business_transaction(service.engine, data["identity"]) as session:
        row = session.execute(
            text(
                "SELECT key_version,version,access_token_enc FROM provider_authorizations "
                "WHERE id=:id"
            ),
            {"id": good.authorization_id},
        ).one()
        assert row[0:2] == (2, 2) and row[2] != previous
        assert (
            session.execute(
                text("SELECT count(*) FROM audit_logs WHERE action='connection.token.rotate'")
            ).scalar_one()
            == 1
        )
        with pytest.raises(DBAPIError):
            session.execute(text("DELETE FROM authorization_exchange_events"))


def test_operator_cannot_start(login_api: dict[str, Any]) -> None:
    api = login_api
    token = sign_in(api)
    with api["app"].state.identity_service.auth.begin() as connection:
        session_id = connection.execute(
            text("SELECT session_id FROM auth.resolve_session(:hash)"), {"hash": digest(token)}
        ).scalar_one()
    service = OAuthService(
        api["app"].state.identity_service.app,
        SecretStr(secrets.token_urlsafe(48)),
        TokenCipher({1: SecretBytes(secrets.token_bytes(32))}, 1),
    )
    with pytest.raises(OAuthRejected):
        service.start(
            VerifiedIdentity(api["tenant"], api["member"], api["user"]),
            session_id,
            OAuthApplication(
                uuid4(), "synthetic", "adq", "tencent", "adq", "https://callback.example.invalid/"
            ),
            uuid4(),
        )


async def test_tenant_isolation_immutable_context_and_no_auth_role_claim(
    oauth_ready: dict[str, Any],
) -> None:
    data = oauth_ready
    service = data["service"]
    start = begin(data)
    await service.complete(
        start.state, SecretStr("synthetic-code"), data["application"], SyntheticExchange(data)
    )
    other = VerifiedIdentity(999999, uuid4(), uuid4())
    with business_transaction(service.engine, other) as session:
        for statement in [
            "SELECT count(*) FROM oauth_states",
            "SELECT count(*) FROM provider_authorizations",
            "SELECT count(*) FROM authorization_exchange_events",
        ]:
            assert session.execute(text(statement)).scalar_one() == 0
    with pytest.raises(OAuthRejected):
        service.tokens(other, uuid4(), start.authorization_id)
    with (
        pytest.raises(DBAPIError),
        business_transaction(service.engine, data["identity"]) as session,
    ):
        session.execute(text("UPDATE provider_authorizations SET app_id='tampered'"))
    with pytest.raises(DBAPIError), data["app"].state.identity_service.auth.begin() as connection:
        connection.execute(
            text("SELECT * FROM access.oauth_claim(:digest,:app,'a','adq','b')"),
            {"digest": "0" * 64, "app": uuid4()},
        )


async def test_cancelled_exchange_consumed_state_stays_unknown(oauth_ready: dict[str, Any]) -> None:
    data = oauth_ready
    service = data["service"]
    start = begin(data)
    entered = asyncio.Event()

    class InterruptedExchange:
        async def exchange(self, context: OAuthExchangeContext, code: SecretStr) -> TokenSet:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    task = asyncio.create_task(
        service.complete(
            start.state, SecretStr("synthetic-code"), data["application"], InterruptedExchange()
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with business_transaction(service.engine, data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT state FROM oauth_states WHERE id=:id"), {"id": start.id}
            ).scalar_one()
            == "UNKNOWN"
        )
        assert (
            session.execute(
                text("SELECT count(*) FROM provider_authorizations WHERE id=:id"),
                {"id": start.authorization_id},
            ).scalar_one()
            == 0
        )
    with pytest.raises(OAuthRejected):
        await service.complete(
            start.state, SecretStr("synthetic-code"), data["application"], SyntheticExchange(data)
        )
