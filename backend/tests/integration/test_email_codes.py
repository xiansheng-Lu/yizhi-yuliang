"""真实PG/HTTP验证邮件挑战；发送端口为隔离捕获夹具，实际SMTP收件另行明确验证。"""

import hashlib
import hmac
import secrets
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from yuliang.infrastructure.mail import MailUnavailable
from yuliang.main import create_app
from yuliang.modules.identity.email_login import login_code
from yuliang.modules.identity.routes import PREAUTH_COOKIE, SESSION_COOKIE
from yuliang.platform.errors import ApiError

from .test_identity_database import database as database
from .test_password_sessions import challenge
from .test_password_sessions import login_api as login_api


class CaptureMail:
    def __init__(self) -> None:
        self.calls: list[tuple[str, SecretStr, UUID, datetime]] = []
        self.fail = False

    def send_code(
        self, email: str, code: SecretStr, challenge_id: UUID, expires_at: datetime
    ) -> None:
        self.calls.append((email, code, challenge_id, expires_at))
        if self.fail:
            raise MailUnavailable("private-provider-failure-never-echo")


@pytest.fixture
def email_api(login_api: dict[str, Any]) -> Iterator[dict[str, Any]]:
    mail = CaptureMail()
    settings = login_api["settings"].model_copy(
        update={"email_login_enabled": True, "email_code_secret": SecretStr(secrets.token_hex(32))}
    )
    app = create_app(settings, mail_sender=mail)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield login_api | {
            "client": client,
            "app": app,
            "settings": settings,
            "mail": mail,
            "csrf": challenge(client),
        }


def send(api: dict[str, Any], email: str | None = None) -> Any:
    return api["client"].post(
        "/v1/auth/email-codes",
        json={"email": email or api["email"], "purpose": "LOGIN"},
        headers={"X-CSRF-Token": api["csrf"]},
    )


def body(
    api: dict[str, Any], *, code: str | None = None, email: str | None = None, index: int = -1
) -> dict[str, Any]:
    call = api["mail"].calls[index]
    return {
        "method": "email_code",
        "email": email or api["email"],
        "challengeId": str(call[2]),
        "code": code if code is not None else call[1].get_secret_value(),
    }


def login(api: dict[str, Any], **changes: Any) -> Any:
    return api["client"].post(
        "/v1/auth/login", json=body(api, **changes), headers={"X-CSRF-Token": api["csrf"]}
    )


def age_send_window(api: dict[str, Any]) -> None:
    instance = api["app"].state.identity_service
    keys = [instance.audit_digest("email", api["email"]), instance.audit_digest("ip", "testclient")]
    with psycopg.connect(api["database"]["admin"]) as control:
        control.execute(
            "UPDATE auth_rate_limits SET blocked_until=now()-interval '1 second' "
            "WHERE bucket_kind='EMAIL_SEND' AND key_digest=ANY(%s)",
            [keys],
        )


def test_m0_10_challenge_hash_and_atomic_email_session(email_api: dict[str, Any]) -> None:
    response = send(email_api)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == {"challengeId", "expiresAt", "retryAfterSeconds"}
    assert data["retryAfterSeconds"] == 60 and len(email_api["mail"].calls) == 1
    call = email_api["mail"].calls[0]
    assert UUID(data["challengeId"]) == call[2] and "code" not in data
    with psycopg.connect(email_api["database"]["admin"]) as control:
        row = control.execute(
            "SELECT code_hash,tenant_id,delivery_state,attempts FROM email_codes WHERE id=%s",
            [call[2]],
        ).fetchone()
        expected = hmac.new(
            email_api["settings"].email_code_secret.get_secret_value().encode(),
            f"{call[2]}|LOGIN|{call[1].get_secret_value()}".encode(),
            hashlib.sha256,
        ).hexdigest()
        assert row == (expected, None, "ACCEPTED", 0)
    preauth = email_api["client"].cookies[PREAUTH_COOKIE]
    accepted = login(email_api)
    assert accepted.status_code == 200 and accepted.json()["data"]["userId"] == str(
        email_api["user"]
    )
    assert (
        SESSION_COOKIE in email_api["client"].cookies
        and PREAUTH_COOKIE not in email_api["client"].cookies
    )
    with psycopg.connect(email_api["database"]["admin"]) as control:
        assert control.execute(
            "SELECT consumed_at IS NOT NULL,verified_at IS NOT NULL,attempts "
            "FROM email_codes WHERE id=%s",
            [call[2]],
        ).fetchone() == (True, True, 1)
        assert control.execute(
            "SELECT count(*) FROM auth_events WHERE user_id=%s AND outcome='EMAIL_ACCEPTED'",
            [email_api["user"]],
        ).fetchone() == (1,)
    email_api["client"].cookies.clear()
    email_api["client"].cookies.set(PREAUTH_COOKIE, preauth)
    assert login(email_api).status_code in (401, 403)


def test_m0_10_wrong_attempts_commit_and_attempt_limit(email_api: dict[str, Any]) -> None:
    assert send(email_api).status_code == 200
    code = email_api["mail"].calls[-1][1].get_secret_value()
    wrong = f"{(int(code) + 1) % 1000000:06d}"
    for _ in range(5):
        assert login(email_api, code=wrong).status_code == 401
    with psycopg.connect(email_api["database"]["admin"]) as control:
        assert control.execute(
            "SELECT attempts,consumed_at FROM email_codes WHERE id=%s",
            [email_api["mail"].calls[-1][2]],
        ).fetchone() == (5, None)
    limited = login(email_api)
    assert limited.status_code == 429 and 1 <= int(limited.headers["Retry-After"]) <= 300


def test_m0_10_expired_and_wrong_preauth_cannot_consume(email_api: dict[str, Any]) -> None:
    assert send(email_api).status_code == 200
    with TestClient(
        create_app(email_api["settings"], mail_sender=CaptureMail()), raise_server_exceptions=False
    ) as other:
        other_csrf = challenge(other)
        response = other.post(
            "/v1/auth/login", json=body(email_api), headers={"X-CSRF-Token": other_csrf}
        )
        assert response.status_code == 401
    with psycopg.connect(email_api["database"]["admin"]) as control:
        control.execute(
            "UPDATE email_codes SET expires_at=now()-interval '1 minute' WHERE id=%s",
            [email_api["mail"].calls[-1][2]],
        )
    assert login(email_api).status_code == 401


def test_m0_10_send_rate_limit_and_resend_invalidates_old_code(email_api: dict[str, Any]) -> None:
    assert send(email_api).status_code == 200
    limited = send(email_api)
    assert limited.status_code == 429 and 1 <= int(limited.headers["Retry-After"]) <= 60
    age_send_window(email_api)
    assert send(email_api).status_code == 200
    assert login(email_api, index=0).status_code == 401
    assert login(email_api, index=1).status_code == 200


@pytest.mark.parametrize("known", [True, False])
def test_m0_10_unknown_email_has_same_challenge_and_login_error(
    email_api: dict[str, Any], known: bool
) -> None:
    address = email_api["email"] if known else f"{uuid4()}@example.invalid"
    response = send(email_api, address)
    assert response.status_code == 200
    assert set(response.json()["data"]) == {"challengeId", "expiresAt", "retryAfterSeconds"}
    real_code = email_api["mail"].calls[-1][1].get_secret_value()
    code = f"{(int(real_code) + 1) % 1000000:06d}" if known else real_code
    rejected = login(email_api, email=address, code=code)
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "UNAUTHENTICATED"
    assert rejected.json()["error"]["message"] == "邮箱或验证码不正确，或验证码已失效"


def test_m0_10_concurrent_code_only_creates_one_session(email_api: dict[str, Any]) -> None:
    assert send(email_api).status_code == 200
    instance = email_api["app"].state.identity_service
    call = email_api["mail"].calls[-1]
    preauth = str(email_api["client"].cookies[PREAUTH_COOKIE])

    def consume(_: int) -> bool:
        try:
            login_code(
                instance,
                email_api["email"],
                call[2],
                call[1],
                preauth,
                email_api["csrf"],
                "testclient",
                uuid4().hex,
            )
            return True
        except ApiError:
            return False

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(consume, range(2)))
    assert sorted(outcomes) == [False, True]
    with psycopg.connect(email_api["database"]["admin"]) as control:
        assert control.execute(
            "SELECT count(*) FROM sessions WHERE user_id=%s", [email_api["user"]]
        ).fetchone() == (1,)


def test_m0_10_insert_failure_rolls_back_consumption_and_preauth(email_api: dict[str, Any]) -> None:
    assert send(email_api).status_code == 200
    with psycopg.connect(email_api["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "CREATE FUNCTION public.test_mail_session_failure() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test session failure'; END; $$"
        )
        control.execute(
            "CREATE TRIGGER test_mail_session_failure BEFORE INSERT ON sessions "
            "FOR EACH ROW EXECUTE FUNCTION public.test_mail_session_failure()"
        )
    try:
        assert login(email_api).status_code == 500
        with psycopg.connect(email_api["database"]["admin"]) as control:
            assert control.execute(
                "SELECT attempts,consumed_at,verified_at FROM email_codes WHERE id=%s",
                [email_api["mail"].calls[-1][2]],
            ).fetchone() == (0, None, None)
    finally:
        with psycopg.connect(email_api["database"]["migration"]) as control:
            control.execute("SET LOCAL ROLE app_owner")
            control.execute("DROP TRIGGER test_mail_session_failure ON sessions")
            control.execute("DROP FUNCTION public.test_mail_session_failure()")
    assert login(email_api).status_code == 200


def test_m0_10_smtp_failure_is_not_delivery_success(
    email_api: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    email_api["mail"].fail = True
    response = send(email_api)
    assert response.status_code == 502
    assert "private-provider-failure" not in response.text + capsys.readouterr().out
    with psycopg.connect(email_api["database"]["admin"]) as control:
        assert control.execute(
            "SELECT delivery_state,invalidated_at IS NOT NULL FROM email_codes WHERE id=%s",
            [email_api["mail"].calls[-1][2]],
        ).fetchone() == ("FAILED", True)
    assert login(email_api).status_code == 401


def test_m0_10_daily_cap_is_server_controlled_and_preserves_existing_code(
    email_api: dict[str, Any],
) -> None:
    email_api["app"].state.identity_service.email_daily_limit = 1
    assert send(email_api).status_code == 200
    age_send_window(email_api)
    response = send(email_api)
    assert response.status_code == 429 and int(response.headers["Retry-After"]) == 3600
    assert len(email_api["mail"].calls) == 1
    assert login(email_api).status_code == 200


@pytest.mark.parametrize("role", ["app", "auth"])
def test_m0_10_runtime_roles_cannot_read_mail_code_table(
    database: dict[str, str], role: str
) -> None:
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        psycopg.connect(
            database[role].replace("postgresql+psycopg://", "postgresql://")
        ) as runtime,
    ):
        runtime.execute("SELECT * FROM email_codes")
