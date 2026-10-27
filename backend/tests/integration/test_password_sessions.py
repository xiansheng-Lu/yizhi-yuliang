"""受限auth角色和真实PG下验证HTTP登录/CSRF/续期/吊销；测试密钥不入应用配置。"""

import secrets
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from yuliang.main import create_app
from yuliang.modules.identity.routes import PREAUTH_COOKIE, SESSION_COOKIE
from yuliang.modules.identity.service import HASHER, IdentityService, digest
from yuliang.settings import Settings

from .test_identity_database import database as database
from .test_infrastructure import local_url


@pytest.fixture
def login_api(database: dict[str, str]) -> Iterator[dict[str, Any]]:
    user, member, shop = uuid4(), uuid4(), uuid4()
    email, password = f"{user}@example.invalid", secrets.token_urlsafe(20)
    with psycopg.connect(database["migration"]) as setup:
        setup.execute("SET LOCAL ROLE app_owner")
        tenant = setup.execute(
            "INSERT INTO tenants(name) VALUES ('Auth probe') RETURNING id"
        ).fetchone()
        assert tenant
        setup.execute(
            "INSERT INTO users(id,email,password_hash,display_name) VALUES (%s,%s,%s,'User')",
            [user, email, HASHER.hash(password)],
        )
        setup.execute(
            "INSERT INTO memberships(id,tenant_id,user_id) VALUES (%s,%s,%s)",
            [member, tenant[0], user],
        )
        setup.execute("SELECT access.seed_tenant_roles(%s)", [tenant[0]])
        role = setup.execute(
            "SELECT id FROM roles WHERE tenant_id=%s AND code='OPERATOR'", [tenant[0]]
        ).fetchone()
        assert role
        setup.execute(
            "INSERT INTO role_grants(tenant_id,membership_id,role_id) VALUES (%s,%s,%s)",
            [tenant[0], member, role[0]],
        )
        setup.execute(
            "INSERT INTO data_scopes(tenant_id,membership_id,scope_type) VALUES "
            "(%s,%s,'ALL_TENANT')",
            [tenant[0], member],
        )
        setup.execute(
            "INSERT INTO shops(id,tenant_id,platform,native_id,name) VALUES "
            "(%s,%s,'wxshop','shop','Shop')",
            [shop, tenant[0]],
        )
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=database["app"],
        auth_database_url=database["auth"],
        redis_url=local_url("YULIANG_TEST_REDIS_URL"),
        broker_url=local_url("YULIANG_TEST_BROKER_URL"),
        session_secret=secrets.token_hex(32),
        identity_api_enabled=True,
        auth_audit_secret=secrets.token_hex(32),
        allowed_origins=["http://testserver", "https://testserver", "http://localhost:3000"],
    )
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield {
            "client": client,
            "app": app,
            "settings": settings,
            "email": email,
            "password": password,
            "user": user,
            "member": member,
            "tenant": tenant[0],
            "shop": shop,
            "database": database,
        }


def challenge(client: TestClient) -> str:
    response = client.get("/v1/auth/csrf")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return str(response.json()["data"]["token"])


def sign_in(api: dict[str, Any]) -> str:
    client: TestClient = api["client"]
    csrf = challenge(client)
    response = client.post(
        "/v1/auth/login",
        json={"email": api["email"], "password": api["password"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return str(client.cookies[SESSION_COOKIE])


def test_m0_09_correct_password_identity_and_no_secret_leak(
    login_api: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    token = sign_in(login_api)
    client: TestClient = login_api["client"]
    response = client.get("/v1/auth/session")
    identity = response.json()["data"]
    assert identity["mode"] == "API" and identity["userId"] == str(login_api["user"])
    assert identity["tenantId"] == str(login_api["tenant"])
    assert identity["shopIds"] == [str(login_api["shop"])]
    assert "command" in identity["workspacePages"]
    assert token not in response.text and login_api["password"] not in response.text
    assert PREAUTH_COOKIE not in client.cookies
    with psycopg.connect(login_api["database"]["admin"]) as db:
        stored = db.execute(
            "SELECT token_hash,csrf_token_hash FROM sessions WHERE user_id=%s", [login_api["user"]]
        ).fetchone()
        assert stored and stored[0] == digest(token) and stored[1] != token
        assert db.execute(
            "SELECT outcome FROM auth_events WHERE user_id=%s", [login_api["user"]]
        ).fetchone() == ("PASSWORD_ACCEPTED",)
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err
    assert login_api["password"] not in captured.out + captured.err


def test_m0_09_ambiguous_rejections_and_persistent_rate_limit(login_api: dict[str, Any]) -> None:
    client: TestClient = login_api["client"]
    csrf = challenge(client)
    responses = [
        client.post(
            "/v1/auth/login",
            json={"email": email, "password": "wrong-password"},
            headers={"X-CSRF-Token": csrf},
        )
        for email in (login_api["email"], "unknown@example.invalid")
    ]
    assert [response.status_code for response in responses] == [401, 401]
    assert responses[0].json()["error"] == responses[1].json()["error"]
    for _ in range(3):
        assert (
            client.post(
                "/v1/auth/login",
                json={"email": login_api["email"], "password": "wrong-password"},
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 401
        )
    blocked = client.post(
        "/v1/auth/login",
        json={"email": login_api["email"], "password": login_api["password"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    # 新应用实例没有内存状态，仍被PG持久桶拒绝。
    with TestClient(create_app(login_api["settings"]), raise_server_exceptions=False) as restarted:
        csrf = challenge(restarted)
        assert (
            restarted.post(
                "/v1/auth/login",
                json={"email": login_api["email"], "password": login_api["password"]},
                headers={"X-CSRF-Token": csrf},
            ).status_code
            == 429
        )


def test_m0_09_auth_database_role_has_only_exact_functions(login_api: dict[str, Any]) -> None:
    source = login_api["database"]["auth"].replace("postgresql+psycopg://", "postgresql://", 1)
    for query in (
        "SELECT * FROM users",
        "SELECT * FROM sessions",
        "SELECT * FROM shops",
        "SET ROLE app_owner",
    ):
        with psycopg.connect(source) as db, pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute(query)
    with psycopg.connect(source) as db:
        assert db.execute("SELECT * FROM auth.credentials(%s)", [login_api["email"]]).fetchone()
        assert db.execute("SELECT current_setting('app.current_tenant',true)").fetchone() == (None,)


def test_m0_11a_pg_expiry_cap_revocation_and_stale_cache(login_api: dict[str, Any]) -> None:
    token = sign_in(login_api)
    client: TestClient = login_api["client"]
    app: FastAPI = login_api["app"]
    cache: redis.Redis = app.state.identity_service.cache
    key = "session:" + digest(token)
    cache.set(key, "stale-active", ex=60)
    try:
        with psycopg.connect(login_api["database"]["admin"]) as db:
            db.execute(
                "UPDATE sessions SET absolute_expires_at=now()+interval '5 "
                "minutes',expires_at=now()+interval '1 minute' WHERE token_hash=%s",
                [digest(token)],
            )
        assert client.get("/v1/auth/session").json()["data"] is not None
        with psycopg.connect(login_api["database"]["admin"]) as db:
            assert db.execute(
                "SELECT expires_at<=absolute_expires_at FROM sessions WHERE token_hash=%s",
                [digest(token)],
            ).fetchone() == (True,)
            db.execute("UPDATE memberships SET status='REMOVED' WHERE id=%s", [login_api["member"]])
        assert cache.get(key) is not None
        assert client.get("/v1/auth/session").json()["data"] is None
    finally:
        cache.delete(key)


def test_m0_11a_expired_forged_unknown_cookie(login_api: dict[str, Any]) -> None:
    token = sign_in(login_api)
    client: TestClient = login_api["client"]
    with psycopg.connect(login_api["database"]["admin"]) as db:
        db.execute(
            "UPDATE sessions SET created_at=now()-interval '2 "
            "hours',absolute_expires_at=now()+interval '1 day',"
            "expires_at=now()-interval '1 minute' WHERE token_hash=%s",
            [digest(token)],
        )
    assert client.get("/v1/auth/session").json()["data"] is None
    for invalid in ("forged", secrets.token_urlsafe(32)):
        client.cookies.set(SESSION_COOKIE, invalid)
        assert client.get("/v1/auth/session").json()["data"] is None


def test_m0_11b_missing_headers_origin_and_rotated_csrf(login_api: dict[str, Any]) -> None:
    client: TestClient = login_api["client"]
    csrf = challenge(client)
    body = {"email": login_api["email"], "password": login_api["password"]}
    assert client.post("/v1/auth/login", json=body).status_code == 403
    assert (
        client.post("/v1/auth/login", json=body, headers={"X-CSRF-Token": "forged"}).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/auth/login",
            json=body,
            headers={"X-CSRF-Token": csrf, "Origin": "https://evil.invalid"},
        ).status_code
        == 403
    )
    assert (
        client.post("/v1/auth/login", json=body, headers={"X-CSRF-Token": csrf}).status_code == 200
    )
    assert client.post("/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 403
    first = challenge(client)
    second = challenge(client)
    assert client.post("/v1/auth/logout", headers={"X-CSRF-Token": first}).status_code == 403
    assert client.post("/v1/auth/logout", headers={"X-CSRF-Token": second}).status_code == 200


def test_m0_11b_exact_cors_and_production_cookie(login_api: dict[str, Any]) -> None:
    settings: Settings = login_api["settings"].model_copy(update={"environment": "prod"})
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        preflight = {
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        }
        response = client.options("/v1/auth/login", headers=preflight)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert (
            client.options(
                "/v1/auth/login", headers=preflight | {"Origin": "https://evil.invalid"}
            ).status_code
            == 400
        )
        csrf = challenge(client)
        response = client.post(
            "/v1/auth/login",
            json={"email": login_api["email"], "password": login_api["password"]},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        cookie = response.headers.get_list("set-cookie")[0].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
        assert "domain=" not in cookie


def test_m0_11c_logout_commits_even_when_redis_unavailable(login_api: dict[str, Any]) -> None:
    token = sign_in(login_api)
    client: TestClient = login_api["client"]
    instance: IdentityService = login_api["app"].state.identity_service
    instance.cache.close()
    instance.cache = redis.Redis(
        host="127.0.0.1", port=1, socket_connect_timeout=0.1, socket_timeout=0.1
    )
    csrf = challenge(client)
    response = client.post("/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200 and response.json()["data"] is None
    assert instance.row(token) is None
    assert client.get("/v1/auth/session").json()["data"] is None
    assert client.post("/v1/auth/logout").status_code == 200
    with psycopg.connect(login_api["database"]["admin"]) as db:
        assert db.execute(
            "SELECT revoked_at IS NOT NULL FROM sessions WHERE token_hash=%s", [digest(token)]
        ).fetchone() == (True,)
        assert db.execute(
            "SELECT count(*) FROM auth_events WHERE user_id=%s AND outcome='LOGOUT'",
            [login_api["user"]],
        ).fetchone() == (1,)


def test_m0_12_profile_only_self_and_legal_empty_snapshot(login_api: dict[str, Any]) -> None:
    sign_in(login_api)
    client: TestClient = login_api["client"]
    csrf = challenge(client)
    response = client.patch(
        "/v1/users/me", json={"displayName": "  新昵称  "}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200 and response.json()["data"]["displayName"] == "新昵称"
    for invalid in (
        {"displayName": "x", "userId": str(uuid4())},
        {"displayName": "x", "tenantId": "2"},
        {"displayName": "x", "permissions": ["admin.access"]},
        {"displayName": " "},
        {"displayName": "x" * 41},
    ):
        assert (
            client.patch("/v1/users/me", json=invalid, headers={"X-CSRF-Token": csrf}).status_code
            == 422
        )
    assert client.patch("/v1/users/me", json={"displayName": "forged"}).status_code == 403
    snapshot = client.get("/v1/frontend/snapshot").json()["data"]
    assert snapshot["source"] == "API" and snapshot["schemaVersion"] == "command-job-v1.0"
    assert snapshot["revision"] > 0 and snapshot["scope"]["timezone"] == "Asia/Shanghai"
    arrays = (
        "tenants",
        "shops",
        "adAccounts",
        "liveSessions",
        "plans",
        "products",
        "materials",
        "tasks",
        "riskPolicies",
        "replaySegments",
        "replayReports",
        "connectors",
    )
    assert all(isinstance(snapshot[key], list) for key in arrays)
    assert snapshot["tenants"][0]["id"] == str(login_api["tenant"])
    assert all(not snapshot[key] for key in arrays if key != "tenants")
    client.cookies.clear()
    assert client.get("/v1/frontend/snapshot").status_code == 401


def test_m0_14_admin_is_separate_audited_and_not_business_owner(login_api: dict[str, Any]) -> None:
    settings: Settings = login_api["settings"].model_copy(update={"admin_api_enabled": True})
    with TestClient(create_app(settings), client=("127.0.0.1", 50001)) as client:
        token = sign_in(login_api | {"client": client})
        assert client.get("/internal/admin/directory").status_code == 403
        for role_code, expected in (("OWNER", 403), ("PLATFORM_ADMIN", 200)):
            with psycopg.connect(login_api["database"]["migration"]) as setup:
                setup.execute("SET LOCAL ROLE app_owner")
                setup.execute(
                    "DELETE FROM role_grants WHERE tenant_id=%s AND membership_id=%s",
                    [login_api["tenant"], login_api["member"]],
                )
                role = setup.execute(
                    "SELECT id FROM roles WHERE tenant_id=%s AND code=%s",
                    [login_api["tenant"], role_code],
                ).fetchone()
                assert role
                setup.execute(
                    "INSERT INTO role_grants(tenant_id,membership_id,role_id) VALUES (%s,%s,%s)",
                    [login_api["tenant"], login_api["member"], role[0]],
                )
            result = client.get("/internal/admin/directory")
            assert result.status_code == expected
        body = result.text
        assert "users" in result.json()["data"] and "tenants" in result.json()["data"]
        assert "password_hash" not in body and "token_hash" not in body
        assert token not in body and login_api["password"] not in body
        assert client.get("/v1/frontend/snapshot").status_code == 403
        with psycopg.connect(login_api["database"]["admin"]) as db:
            assert db.execute(
                "SELECT count(*) FROM access_change_events WHERE tenant_id=%s "
                "AND action='ADMIN_DIRECTORY_READ'",
                [login_api["tenant"]],
            ).fetchone() == (1,)
        with TestClient(create_app(settings), client=("203.0.113.5", 50001)) as external:
            external.cookies.set(SESSION_COOKIE, token)
            assert (
                external.get(
                    "/internal/admin/directory", headers={"X-Forwarded-For": "127.0.0.1"}
                ).status_code
                == 403
            )
