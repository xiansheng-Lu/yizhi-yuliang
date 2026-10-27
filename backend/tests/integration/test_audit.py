"""真实普通PG角色验证审计只追加、分区维护、业务原子性与租户/敏感边界。"""

import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.audit.service import by_request, record
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError

from .test_identity_database import database as database
from .test_password_sessions import challenge, sign_in
from .test_password_sessions import login_api as login_api


@pytest.fixture(scope="module")
def audit_data(database: dict[str, str]) -> Iterator[dict[str, Any]]:
    user = uuid4()
    members = [uuid4(), uuid4()]
    shops = [uuid4(), uuid4()]
    tenants = []
    with psycopg.connect(database["admin"]) as setup:
        setup.execute(
            "INSERT INTO users(id,email,display_name) VALUES(%s,%s,'Audit probe')",
            [user, f"{user}@example.invalid"],
        )
        for index in range(2):
            row = setup.execute(
                "INSERT INTO tenants(name) VALUES('Audit probe') RETURNING id"
            ).fetchone()
            assert row
            tenants.append(row[0])
            setup.execute(
                "INSERT INTO memberships(id,tenant_id,user_id) VALUES(%s,%s,%s)",
                [members[index], row[0], user],
            )
            setup.execute(
                "INSERT INTO shops(id,tenant_id,platform,native_id,name) "
                "VALUES(%s,%s,'wxshop','audit','Original')",
                [shops[index], row[0]],
            )
    engine = create_engine(database["app"], hide_parameters=True)
    try:
        yield {
            "engine": engine,
            "a": VerifiedIdentity(tenants[0], members[0], user),
            "b": VerifiedIdentity(tenants[1], members[1], user),
            "shop": shops[0],
        }
    finally:
        engine.dispose()


def entry(data: dict[str, Any], request: UUID) -> dict[str, Any]:
    return {
        "action": "shop.update",
        "object_type": "shop",
        "object_id": data["shop"],
        "request_id": request,
        "before_hash": "a" * 64,
        "after_hash": "b" * 64,
        "detail": {"changed_fields": ["status"]},
    }


def test_m0_17_append_and_cross_tenant_query(audit_data: dict[str, Any]) -> None:
    engine: Engine = audit_data["engine"]
    request = uuid4()
    with business_transaction(engine, audit_data["a"]) as session:
        audit_id = record(session, audit_data["a"], entry(audit_data, request))
    with business_transaction(engine, audit_data["a"]) as session:
        rows = by_request(session, audit_data["a"], request)
        assert len(rows) == 1 and rows[0]["id"] == audit_id
        assert rows[0]["actor_id"] == audit_data["a"].user_id
    with business_transaction(engine, audit_data["b"]) as session:
        assert by_request(session, audit_data["b"], request) == []
        assert (
            session.execute(
                text("SELECT count(*) FROM audit_logs WHERE request_id=:id"), {"id": request}
            ).scalar_one()
            == 0
        )
        with pytest.raises(ApiError):
            record(session, audit_data["a"], entry(audit_data, uuid4()))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_logs SET action='shop.update'",
        "DELETE FROM audit_logs",
        "TRUNCATE audit_logs",
        "SELECT access.ensure_audit_partition(date_trunc('month',now())::date)",
    ],
)
def test_m0_17_runtime_cannot_rewrite_or_maintain(
    audit_data: dict[str, Any], statement: str
) -> None:
    with (
        pytest.raises(DBAPIError),
        business_transaction(audit_data["engine"], audit_data["a"]) as session,
    ):
        session.execute(text(statement))


def test_m0_17_child_acl_and_force_rls(
    database: dict[str, str], audit_data: dict[str, Any]
) -> None:
    with psycopg.connect(database["admin"]) as control:
        children = control.execute(
            "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity "
            "FROM pg_class c JOIN pg_inherits i ON i.inhrelid=c.oid "
            "WHERE i.inhparent='audit_logs'::regclass ORDER BY c.relname"
        ).fetchall()
        assert len(children) == 2 and all(row[1:] == (True, True) for row in children)
        for table, *_ in children:
            for operation in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ):
                assert control.execute(
                    "SELECT has_table_privilege('app_login',%s,%s)", [table, operation]
                ).fetchone() == (False,)
    # 不接受仅检查ACL元数据：真实普通角色直接读子表也必须失败。
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        psycopg.connect(database["app"].replace("postgresql+psycopg://", "postgresql://")) as app,
    ):
        app.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(children[0][0])))


def test_m0_17_business_and_audit_rollback_together(audit_data: dict[str, Any]) -> None:
    request = uuid4()
    with (
        pytest.raises(RuntimeError),
        business_transaction(audit_data["engine"], audit_data["a"]) as session,
    ):
        session.execute(
            text("UPDATE shops SET name='Rollback' WHERE id=:id"), {"id": audit_data["shop"]}
        )
        record(session, audit_data["a"], entry(audit_data, request))
        raise RuntimeError("business rejected")
    with business_transaction(audit_data["engine"], audit_data["a"]) as session:
        assert by_request(session, audit_data["a"], request) == []
        assert (
            session.execute(
                text("SELECT name FROM shops WHERE id=:id"), {"id": audit_data["shop"]}
            ).scalar_one()
            == "Original"
        )


def test_m0_17_missing_partition_fails_closed_then_maintenance_recovers(
    database: dict[str, str], audit_data: dict[str, Any]
) -> None:
    statement = text("""INSERT INTO audit_logs(
        tenant_id,actor_id,action,object_type,object_id,request_id,created_at)
        VALUES(:tenant,:actor,'shop.update','shop',:object,:request,
        date_trunc('month',now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'+interval '12 months')""")
    params = {
        "tenant": audit_data["a"].tenant_id,
        "actor": audit_data["a"].user_id,
        "object": str(audit_data["shop"]),
        "request": uuid4(),
    }
    with (
        pytest.raises(DBAPIError),
        business_transaction(audit_data["engine"], audit_data["a"]) as session,
    ):
        session.execute(
            text("UPDATE shops SET name='Missing partition' WHERE id=:id"),
            {"id": audit_data["shop"]},
        )
        session.execute(statement, params)
    with psycopg.connect(database["migration"]) as maintenance:
        maintenance.execute("SET LOCAL ROLE app_owner")
        for _ in range(2):
            maintenance.execute(
                "SELECT access.ensure_audit_partition(("
                "date_trunc('month',now() AT TIME ZONE 'UTC')+interval '12 months')::date)"
            )
    with business_transaction(audit_data["engine"], audit_data["a"]) as session:
        session.execute(statement, params)
        assert len(by_request(session, audit_data["a"], params["request"])) == 1
        assert (
            session.execute(
                text("SELECT name FROM shops WHERE id=:id"), {"id": audit_data["shop"]}
            ).scalar_one()
            == "Original"
        )


def test_m0_17_profile_http_audit_and_auth_failure_are_separate(
    login_api: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    client: TestClient = login_api["client"]
    sign_in(login_api)
    csrf = challenge(client)
    name = "NeverLogPrivateDisplay"
    response = client.patch(
        "/v1/users/me", json={"displayName": name}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200
    request = UUID(response.headers["X-Request-Id"])
    with psycopg.connect(login_api["database"]["admin"]) as control:
        row = control.execute(
            "SELECT tenant_id,actor_id,action,request_id,before_hash,after_hash,detail::text "
            "FROM audit_logs WHERE request_id=%s",
            [request],
        ).fetchone()
        assert row and row[:4] == (
            login_api["tenant"],
            login_api["user"],
            "profile.update",
            request,
        )
        assert row[4] != row[5] and name not in str(row) and login_api["password"] not in str(row)
    client.cookies.clear()
    csrf = challenge(client)
    bad = client.post(
        "/v1/auth/login",
        json={"email": login_api["email"], "password": "PrivateRejectedCredential"},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 401
    with psycopg.connect(login_api["database"]["admin"]) as control:
        assert control.execute(
            "SELECT tenant_id FROM auth_events WHERE request_id=%s AND outcome='PASSWORD_REJECTED'",
            [bad.headers["X-Request-Id"]],
        ).fetchone() == (None,)
        assert control.execute(
            "SELECT count(*) FROM audit_logs WHERE request_id=%s",
            [UUID(bad.headers["X-Request-Id"])],
        ).fetchone() == (0,)
    assert "PrivateRejectedCredential" not in bad.text + caplog.text
    assert name not in caplog.text and login_api["password"] not in caplog.text


@pytest.mark.parametrize(
    "detail",
    [
        {"order_phone": "13800138000"},
        {"changed_fields": [], "result": None},
        {"changed_fields": ["order_phone"], "result": "APPLIED"},
        {"changed_fields": [], "result": "APPLIED", "password": "PrivateCredential"},
    ],
)
def test_m0_17_database_rejects_raw_pii_detail(
    audit_data: dict[str, Any], detail: dict[str, Any]
) -> None:
    params = {
        "tenant": audit_data["a"].tenant_id,
        "actor": audit_data["a"].user_id,
        "object": str(audit_data["shop"]),
        "request": uuid4(),
        "detail": json.dumps(detail),
    }
    with (
        pytest.raises(DBAPIError),
        business_transaction(audit_data["engine"], audit_data["a"]) as session,
    ):
        session.execute(
            text(
                "INSERT INTO audit_logs(tenant_id,actor_id,action,object_type,"
                "object_id,request_id,detail) VALUES(:tenant,:actor,'shop.update','shop',"
                ":object,:request,CAST(:detail AS jsonb))"
            ),
            params,
        )


@pytest.mark.parametrize("role", ["app", "auth"])
def test_m0_17_auth_events_never_directly_readable(database: dict[str, str], role: str) -> None:
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        psycopg.connect(
            database[role].replace("postgresql+psycopg://", "postgresql://")
        ) as runtime,
    ):
        runtime.execute("SELECT * FROM auth_events")
