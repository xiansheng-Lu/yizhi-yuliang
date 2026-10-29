"""真实空库迁移、普通角色RLS、池清理、异步/同步同用例与范围红绿测试。"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from yuliang.modules.access.service import require_shop_account
from yuliang.platform.db import (
    VerifiedIdentity,
    async_business_transaction,
    business_transaction,
)
from yuliang.platform.errors import ApiError

from .test_infrastructure import local_url

ROOT = Path(__file__).resolve().parents[2]


def database_url(source: str, name: str, sqlalchemy: bool = False) -> str:
    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(source)
    scheme = "postgresql+psycopg" if sqlalchemy else "postgresql"
    return urlunsplit((scheme, parsed.netloc, "/" + name, "", ""))


def migrate(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["YULIANG_MIGRATION_DATABASE_URL"] = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return subprocess.run(  # noqa: S603 -- 固定Alembic入口，凭据不放命令行。
        [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def checked_migrate(url: str, *args: str) -> None:
    result = migrate(url, *args)
    # 工具错误只回显去掉本地密码的输出，方便定位迁移SQL问题。
    password = str(conninfo_to_dict(url).get("password", ""))
    output = result.stdout + result.stderr
    if password:
        output = output.replace(password, "[REDACTED]")
    assert result.returncode == 0, output


@pytest.fixture(scope="module")
def database() -> Iterator[dict[str, str]]:
    name = "yuliang_test_" + uuid4().hex
    source = local_url("YULIANG_TEST_ADMIN_DATABASE_URL")
    migration = database_url(local_url("YULIANG_TEST_MIGRATION_DATABASE_URL"), name)
    admin = database_url(source, name)
    with psycopg.connect(source, autocommit=True) as control:
        # 与migrate_local共用集群级窗口：LOGIN是全局角色属性，不随测试库隔离。
        # 取得锁即证明没有其他迁移窗口；先无条件把角色归一化为NOLOGIN，
        # 这样上一轮崩溃遗留的LOGIN不会让后续每个模块的setup集体失败（自愈，
        # 不依赖上一模块的清理是否成功）。
        control.execute("SET lock_timeout='45s'")
        control.execute("SELECT pg_advisory_lock(760001)")
        control.execute("ALTER ROLE app_migrate NOLOGIN")
        control.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            control.execute("ALTER ROLE app_migrate LOGIN")
            with psycopg.connect(admin, autocommit=True) as setup:
                setup.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                setup.execute("GRANT USAGE,CREATE ON SCHEMA public TO app_owner")
                setup.execute("GRANT USAGE ON SCHEMA public TO app_login,auth_fn_owner")
                setup.execute("CREATE SCHEMA auth AUTHORIZATION auth_fn_owner")
                setup.execute("CREATE SCHEMA access AUTHORIZATION app_owner")
                setup.execute("REVOKE ALL ON SCHEMA auth FROM PUBLIC")
                setup.execute("GRANT USAGE ON SCHEMA auth TO app_auth")
            checked_migrate(migration, "upgrade", "0001_identity_catalog")
            # 中间版本也保持关闭：未开放策略，不能借迁移间隙访问业务表。
            with psycopg.connect(database_url(local_url("YULIANG_TEST_DATABASE_URL"), name)) as app:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    app.execute("SELECT * FROM shops")
            checked_migrate(migration, "upgrade", "head")
            yield {
                "admin": admin,
                "migration": migration,
                "name": name,
                "app": database_url(local_url("YULIANG_TEST_DATABASE_URL"), name, True),
                "auth": database_url(local_url("YULIANG_TEST_AUTH_DATABASE_URL"), name, True),
            }
        finally:
            control.execute("ALTER ROLE app_migrate NOLOGIN")
            control.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s", [name]
            )
            control.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


@pytest.fixture(scope="module")
def seeded(database: dict[str, str]) -> dict[str, Any]:
    data: dict[str, Any] = {"tenant_a": 1, "tenant_b": 2}
    for name in (
        "user",
        "other_user",
        "member_a",
        "member_b",
        "shop_a",
        "shop_b",
        "account_a",
        "account_other",
        "account_b",
        "role",
    ):
        data[name] = uuid4()
    with psycopg.connect(database["admin"]) as db:
        db.execute("INSERT INTO tenants(id,name) OVERRIDING SYSTEM VALUE VALUES (1,'A'),(2,'B')")
        for user, email in (
            (data["user"], "a@example.invalid"),
            (data["other_user"], "b@example.invalid"),
        ):
            db.execute(
                "INSERT INTO users(id,email,display_name) VALUES (%s,%s,'Probe')", [user, email]
            )
        for tenant, member in ((1, data["member_a"]), (2, data["member_b"])):
            db.execute(
                "INSERT INTO memberships(id,tenant_id,user_id) VALUES (%s,%s,%s)",
                [member, tenant, data["user"]],
            )
        for tenant, shop in ((1, data["shop_a"]), (2, data["shop_b"])):
            db.execute(
                "INSERT INTO shops(id,tenant_id,platform,native_id,name) "
                "VALUES (%s,%s,'wxshop','shop','shop')",
                [shop, tenant],
            )
        for tenant, account, native in (
            (1, data["account_a"], "a"),
            (1, data["account_other"], "other"),
            (2, data["account_b"], "b"),
        ):
            db.execute(
                "INSERT INTO "
                "ad_accounts(id,tenant_id,provider,product_line,native_id,name) "
                "VALUES (%s,%s,'tencent','adq',%s,'account')",
                [account, tenant, native],
            )
        for account in (data["account_a"], data["account_other"]):
            db.execute(
                "INSERT INTO entity_links(tenant_id,shop_id,account_id) VALUES (1,%s,%s)",
                [data["shop_a"], account],
            )
        db.execute(
            "INSERT INTO permissions(code,description) VALUES ('snapshot.read','Read') "
            "ON CONFLICT DO NOTHING"
        )
        db.execute(
            "INSERT INTO role_templates(code,name) VALUES ('AUDITOR','Read-only template') "
            "ON CONFLICT DO NOTHING"
        )
        db.execute(
            "INSERT INTO roles(id,tenant_id,code,name) VALUES (%s,1,'PROBE','probe')",
            [data["role"]],
        )
        db.execute(
            "INSERT INTO "
            "role_permissions(tenant_id,role_id,permission_code) VALUES "
            "(1,%s,'snapshot.read')",
            [data["role"]],
        )
        db.execute(
            "INSERT INTO role_grants(tenant_id,membership_id,role_id) VALUES (1,%s,%s)",
            [data["member_a"], data["role"]],
        )
        db.execute(
            "INSERT INTO "
            "data_scopes(tenant_id,membership_id,scope_type,shop_id) VALUES "
            "(1,%s,'SHOP',%s)",
            [data["member_a"], data["shop_a"]],
        )
        db.execute(
            "INSERT INTO "
            "data_scopes(tenant_id,membership_id,scope_type,account_id) "
            "VALUES (1,%s,'ACCOUNT',%s)",
            [data["member_a"], data["account_a"]],
        )
    return data


@pytest.fixture
def app_engine(database: dict[str, str], seeded: dict[str, Any]) -> Iterator[Engine]:
    engine = create_engine(database["app"], pool_size=1, max_overflow=0, hide_parameters=True)
    yield engine
    engine.dispose()


def identity(data: dict[str, Any], tenant: int = 1) -> VerifiedIdentity:
    return VerifiedIdentity(
        tenant, data["member_a"] if tenant == 1 else data["member_b"], data["user"]
    )


def visible_shops(session: Session) -> list[str]:
    return [
        str(value) for value in session.execute(text("SELECT id FROM shops ORDER BY id")).scalars()
    ]


def test_m0_06_empty_upgrade_repeat_and_safe_rollback(database: dict[str, str]) -> None:
    with psycopg.connect(database["admin"]) as db:
        before = db.execute(
            "SELECT tablename,tableowner FROM pg_tables WHERE "
            "schemaname='public' ORDER BY tablename"
        ).fetchall()
        assert {
            "tenants",
            "users",
            "memberships",
            "sessions",
            "auth_challenges",
            "shops",
            "ad_accounts",
            "roles",
            "data_scopes",
        } <= {name for name, _ in before}
        assert {owner for _, owner in before} == {"app_owner"}
        assert db.execute(
            "SELECT bool_and(convalidated) FROM pg_constraint WHERE contype='f'"
        ).fetchone() == (True,)
        assert db.execute(
            "SELECT count(*) FROM pg_partitioned_table WHERE partrelid IN "
            "(SELECT oid FROM pg_class WHERE relnamespace='public'::regnamespace "
            "AND relname IN ('tenants','users','memberships','sessions','shops','ad_accounts'))"
        ).fetchone() == (0,)
    checked_migrate(database["migration"], "upgrade", "head")
    assert migrate(database["migration"], "downgrade", "base").returncode != 0
    with psycopg.connect(database["admin"]) as db:
        assert (
            db.execute(
                "SELECT tablename,tableowner FROM pg_tables WHERE "
                "schemaname='public' ORDER BY tablename"
            ).fetchall()
            == before
        )
        expected = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (expected,)


def test_m0_06_session_identity_fk_and_updates(
    database: dict[str, str], seeded: dict[str, Any]
) -> None:
    with psycopg.connect(database["admin"]) as db:
        query = """INSERT INTO sessions(token_hash,csrf_token_hash,tenant_id,user_id,
        membership_id,authz_version,expires_at,absolute_expires_at)
        VALUES (%s,%s,1,%s,%s,1,now()+interval '1 hour',now()+interval '1 day')"""
        with pytest.raises(psycopg.errors.ForeignKeyViolation), db.transaction():
            db.execute(query, ["a" * 64, "b" * 64, seeded["other_user"], seeded["member_a"]])
        db.execute(query, ["c" * 64, "d" * 64, seeded["user"], seeded["member_a"]])
        db.execute(
            "UPDATE users SET display_name='Changed',updated_at='2000-01-01' WHERE id=%s",
            [seeded["user"]],
        )
        assert db.execute(
            "SELECT updated_at>'2001-01-01'::timestamptz FROM users WHERE id=%s", [seeded["user"]]
        ).fetchone() == (True,)


def test_m0_07_pool_reuse_and_rollback(app_engine: Engine, seeded: dict[str, Any]) -> None:
    statements: list[str] = []
    event.listen(
        app_engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, parameters, context, executemany: statements.append(
            statement
        ),
    )
    with business_transaction(app_engine, identity(seeded)) as session:
        assert visible_shops(session) == [str(seeded["shop_a"])]
    assert statements[0].startswith("SELECT set_config")
    with business_transaction(app_engine, identity(seeded, 2)) as session:
        assert visible_shops(session) == [str(seeded["shop_b"])]
    with pytest.raises(RuntimeError), business_transaction(app_engine, identity(seeded)) as session:
        session.execute(
            text("UPDATE shops SET name='must rollback' WHERE id=:id"), {"id": seeded["shop_a"]}
        )
        raise RuntimeError("rollback probe")
    with app_engine.connect() as raw:
        assert raw.execute(text("SELECT public.current_tenant_id()")).scalar_one() is None
        assert raw.execute(text("SELECT count(*) FROM shops")).scalar_one() == 0
    with business_transaction(app_engine, identity(seeded)) as session:
        assert (
            session.execute(
                text("SELECT name FROM shops WHERE id=:id"), {"id": seeded["shop_a"]}
            ).scalar_one()
            == "shop"
        )


@pytest.mark.asyncio
async def test_m0_07_api_and_worker_same_usecase(
    database: dict[str, str], seeded: dict[str, Any]
) -> None:
    engine = create_async_engine(database["app"], pool_size=1, max_overflow=0, hide_parameters=True)
    try:
        async with async_business_transaction(engine, identity(seeded)) as session:
            assert await session.run_sync(visible_shops) == [str(seeded["shop_a"])]
        async with async_business_transaction(engine, identity(seeded, 2)) as session:
            assert await session.run_sync(visible_shops) == [str(seeded["shop_b"])]
        async with engine.connect() as raw:
            assert (
                await raw.execute(text("SELECT public.current_tenant_id()"))
            ).scalar_one() is None
    finally:
        await engine.dispose()


@pytest.mark.parametrize("tenant", ["", "0", "-1", "invalid", "9999999999999999999999999999"])
def test_m0_08a_invalid_context_fails_closed(app_engine: Engine, tenant: str) -> None:
    with app_engine.begin() as db:
        db.execute(text("SELECT set_config('app.current_tenant',:tenant,true)"), {"tenant": tenant})
        assert db.execute(text("SELECT count(*) FROM shops")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM role_templates")).scalar_one() == 0


def test_m0_08a_four_commands_and_immutable_templates(
    app_engine: Engine, seeded: dict[str, Any]
) -> None:
    probe = uuid4()
    with business_transaction(app_engine, identity(seeded)) as session:
        assert session.execute(text("SELECT count(*) FROM role_templates")).scalar_one() == 5
        session.execute(
            text(
                "INSERT INTO shops(id,tenant_id,platform,native_id,name) "
                "VALUES (:id,1,'wxshop',:native,'probe')"
            ),
            {"id": probe, "native": str(probe)},
        )
        assert session.execute(
            text("UPDATE shops SET name='updated' WHERE id=:id RETURNING id"), {"id": probe}
        ).scalars().all() == [probe]
        assert session.execute(
            text("DELETE FROM shops WHERE id=:id RETURNING id"), {"id": probe}
        ).scalars().all() == [probe]
        assert (
            session.execute(
                text("UPDATE shops SET name='bad' WHERE id=:id RETURNING id"),
                {"id": seeded["shop_b"]},
            )
            .scalars()
            .all()
            == []
        )
        assert (
            session.execute(
                text("DELETE FROM shops WHERE id=:id RETURNING id"), {"id": seeded["shop_b"]}
            )
            .scalars()
            .all()
            == []
        )
    for command in (
        "UPDATE role_templates SET name='bad'",
        "DELETE FROM role_templates",
        "INSERT INTO role_templates VALUES ('OWNER','bad')",
        "SELECT * FROM users",
        "SET ROLE app_owner",
        "SET ROLE auth_fn_owner",
        "UPDATE memberships SET status='REMOVED'",
    ):
        with (
            pytest.raises(DBAPIError),
            business_transaction(app_engine, identity(seeded)) as session,
        ):
            session.execute(text(command))
    for command in (
        "INSERT INTO shops(tenant_id,platform,native_id,name) VALUES (2,'wxshop','bad','bad')",
        "UPDATE shops SET tenant_id=2 WHERE id=:id",
    ):
        with (
            pytest.raises(DBAPIError),
            business_transaction(app_engine, identity(seeded)) as session,
        ):
            session.execute(text(command), {"id": seeded["shop_a"]})


def test_m0_08b_fk_and_permission_scope(
    app_engine: Engine, seeded: dict[str, Any], database: dict[str, str]
) -> None:
    with pytest.raises(DBAPIError), business_transaction(app_engine, identity(seeded)) as session:
        session.execute(
            text(
                "INSERT INTO entity_links(tenant_id,shop_id,account_id) VALUES (1,:shop,:account)"
            ),
            {"shop": seeded["shop_a"], "account": seeded["account_b"]},
        )
    with business_transaction(app_engine, identity(seeded)) as session:
        require_shop_account(
            session, identity(seeded), seeded["shop_a"], seeded["account_a"], "snapshot.read"
        )
        for account, permission in (
            (seeded["account_other"], "snapshot.read"),
            (seeded["account_a"], "task.execute"),
            (seeded["account_b"], "snapshot.read"),
        ):
            with pytest.raises(ApiError):
                require_shop_account(
                    session, identity(seeded), seeded["shop_a"], account, permission
                )
    # 防护敏感性：临时只移除该测试库的复合FK，非法跨租户引用即能写入；恢复后再次拒绝。
    # 用例全程普通应用角色执行写入，维护身份仅调整本次临时库的约束。
    with psycopg.connect(database["admin"], autocommit=True) as maintenance:
        row = maintenance.execute(
            "SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='entity_links'::regclass AND "
            "confrelid='ad_accounts'::regclass"
        ).fetchone()
        assert row
        maintenance.execute(
            sql.SQL("ALTER TABLE entity_links DROP CONSTRAINT {}").format(sql.Identifier(row[0]))
        )
        try:
            with business_transaction(app_engine, identity(seeded)) as session:
                probe = uuid4()
                session.execute(
                    text(
                        "INSERT INTO "
                        "entity_links(id,tenant_id,shop_id,account_id) "
                        "VALUES (:id,1,:shop,:account)"
                    ),
                    {"id": probe, "shop": seeded["shop_a"], "account": seeded["account_b"]},
                )
                session.execute(text("DELETE FROM entity_links WHERE id=:id"), {"id": probe})
        finally:
            maintenance.execute(
                sql.SQL("ALTER TABLE entity_links ADD CONSTRAINT {} ").format(
                    sql.Identifier(row[0])
                )
                + sql.SQL(row[1])
            )


def test_m0_13_seeds_are_idempotent_and_admin_has_no_business_grant(
    database: dict[str, str], seeded: dict[str, Any], app_engine: Engine
) -> None:
    from yuliang.modules.access.catalog import ROLE_PERMISSIONS, workspace_pages

    with psycopg.connect(database["migration"]) as db:
        db.execute("SET LOCAL ROLE app_owner")
        db.execute("SELECT access.seed_tenant_roles(1); SELECT access.seed_tenant_roles(2)")
        before = db.execute("SELECT id,tenant_id,code FROM roles ORDER BY id").fetchall()
        db.execute("SELECT access.seed_tenant_roles(1); SELECT access.seed_tenant_roles(2)")
        assert db.execute("SELECT id,tenant_id,code FROM roles ORDER BY id").fetchall() == before
        for code, permissions in ROLE_PERMISSIONS.items():
            actual = db.execute(
                "SELECT permission_code FROM role_template_permissions WHERE template_code=%s",
                [code],
            ).fetchall()
            assert {row[0] for row in actual} == permissions
        assert "command" in workspace_pages(set(ROLE_PERMISSIONS["OPERATOR"]))
        assert not workspace_pages(set(ROLE_PERMISSIONS["PLATFORM_ADMIN"]))
        operator = db.execute(
            "SELECT id FROM roles WHERE tenant_id=1 AND code='OPERATOR'"
        ).fetchone()
        assert operator
        db.execute(
            "DELETE FROM role_permissions WHERE tenant_id=1 AND role_id=%s AND "
            "permission_code='content.write'",
            [operator[0]],
        )
        db.execute("SELECT access.seed_tenant_roles(1)")
        assert db.execute(
            "SELECT count(*) FROM role_permissions WHERE tenant_id=1 AND role_id=%s "
            "AND permission_code='content.write'",
            [operator[0]],
        ).fetchone() == (0,)
        admin_member = uuid4()
        db.execute(
            "INSERT INTO memberships(id,tenant_id,user_id) VALUES (%s,1,%s)",
            [admin_member, seeded["other_user"]],
        )
        role = db.execute(
            "SELECT id FROM roles WHERE tenant_id=1 AND code='PLATFORM_ADMIN'"
        ).fetchone()
        assert role
        db.execute(
            "INSERT INTO role_grants(tenant_id,membership_id,role_id) VALUES (1,%s,%s)",
            [admin_member, role[0]],
        )
        db.execute(
            "INSERT INTO data_scopes(tenant_id,membership_id,scope_type) VALUES "
            "(1,%s,'ALL_TENANT')",
            [admin_member],
        )
    admin_identity = VerifiedIdentity(1, admin_member, seeded["other_user"])
    with business_transaction(app_engine, admin_identity) as session:
        with pytest.raises(ApiError):
            require_shop_account(
                session, admin_identity, seeded["shop_a"], seeded["account_a"], "snapshot.read"
            )


def test_m0_13_controlled_scope_change_and_removal_are_audited(
    database: dict[str, str], seeded: dict[str, Any], app_engine: Engine
) -> None:
    from pydantic import ValidationError

    from yuliang.modules.access.management import MemberScopeChange, change_member_scope

    owner_member = uuid4()
    owner_user = uuid4()
    owner_hash = uuid4().hex + uuid4().hex
    with psycopg.connect(database["migration"]) as db:
        db.execute("SET LOCAL ROLE app_owner")
        db.execute("SELECT access.seed_tenant_roles(1)")
        db.execute(
            "INSERT INTO users(id,email,display_name) VALUES (%s,%s,'Owner')",
            [owner_user, str(owner_user) + "@example.invalid"],
        )
        db.execute(
            "INSERT INTO memberships(id,tenant_id,user_id) VALUES (%s,1,%s)",
            [owner_member, owner_user],
        )
        role = db.execute("SELECT id FROM roles WHERE tenant_id=1 AND code='OWNER'").fetchone()
        assert role
        db.execute(
            "INSERT INTO role_grants(tenant_id,membership_id,role_id) VALUES (1,%s,%s)",
            [owner_member, role[0]],
        )
        db.execute(
            "INSERT INTO sessions(token_hash,csrf_token_hash,tenant_id,user_id,members"
            "hip_id,authz_version,expires_at,absolute_expires_at) VALUES "
            "(%s,%s,1,%s,%s,1,now()+interval '1 hour',now()+interval '1 day')",
            [owner_hash, uuid4().hex * 2, owner_user, owner_member],
        )
    actor = VerifiedIdentity(1, owner_member, owner_user)
    scope = {"expectedVersion": 1, "status": "ACTIVE", "scopes": [{"scopeType": "ALL_TENANT"}]}
    for forged in ({"role": "OWNER"}, {"tenantId": "2"}, {"permissions": ["admin.access"]}):
        with pytest.raises(ValidationError):
            MemberScopeChange.model_validate(scope | forged)
    with pytest.raises(DBAPIError), business_transaction(app_engine, identity(seeded)) as session:
        change_member_scope(
            session,
            "invalid-session",
            seeded["member_a"],
            MemberScopeChange.model_validate(scope),
            "scope-denied",
        )
    bad = MemberScopeChange.model_validate(
        {
            "expectedVersion": 1,
            "status": "ACTIVE",
            "scopes": [{"scopeType": "ACCOUNT", "accountId": str(seeded["account_b"])}],
        }
    )
    with pytest.raises(DBAPIError), business_transaction(app_engine, actor) as session:
        change_member_scope(session, owner_hash, seeded["member_a"], bad, "scope-bad-fk")
    with business_transaction(app_engine, actor) as session:
        assert (
            session.execute(
                text("SELECT authz_version FROM memberships WHERE id=:id"),
                {"id": seeded["member_a"]},
            ).scalar_one()
            == 1
        )
        assert session.execute(text("SELECT count(*) FROM access_change_events")).scalar_one() == 0
        version = change_member_scope(
            session,
            owner_hash,
            seeded["member_a"],
            MemberScopeChange.model_validate(scope),
            "scope-change",
        )
        assert version == 2
    with pytest.raises(DBAPIError), business_transaction(app_engine, actor) as session:
        change_member_scope(
            session,
            owner_hash,
            seeded["member_a"],
            MemberScopeChange.model_validate(scope),
            "scope-stale",
        )
    with business_transaction(app_engine, actor) as session:
        version = change_member_scope(
            session,
            owner_hash,
            seeded["member_a"],
            MemberScopeChange.model_validate(
                {"expectedVersion": 2, "status": "REMOVED", "scopes": []}
            ),
            "member-remove",
        )
        assert version == 3
        events = session.execute(
            text(
                "SELECT request_id,old_value,new_value FROM access_change_events ORDER"
                " BY created_at,id"
            )
        ).all()
        assert {row[0] for row in events} == {"scope-change", "member-remove"}
        assert any(row[2]["status"] == "REMOVED" for row in events)
    with business_transaction(app_engine, identity(seeded)) as session:
        with pytest.raises(ApiError):
            require_shop_account(
                session, identity(seeded), seeded["shop_a"], seeded["account_a"], "snapshot.read"
            )
    for sql_command in (
        "UPDATE access_change_events SET request_id='tampered'",
        "DELETE FROM access_change_events",
    ):
        with pytest.raises(DBAPIError), business_transaction(app_engine, actor) as session:
            session.execute(text(sql_command))


def test_m0_13_agency_has_only_explicit_client_scope(
    database: dict[str, str], seeded: dict[str, Any], app_engine: Engine
) -> None:
    user, member_a, member_b = uuid4(), uuid4(), uuid4()
    with psycopg.connect(database["migration"]) as db:
        db.execute("SET LOCAL ROLE app_owner")
        db.execute("SELECT access.seed_tenant_roles(1); SELECT access.seed_tenant_roles(2)")
        db.execute(
            "INSERT INTO users(id,email,display_name) VALUES (%s,%s,'Agency')",
            [user, str(user) + "@example.invalid"],
        )
        for tenant, member in ((1, member_a), (2, member_b)):
            db.execute(
                "INSERT INTO memberships(id,tenant_id,user_id,kind) "
                "VALUES (%s,%s,%s,'AGENCY_STAFF')",
                [member, tenant, user],
            )
        role = db.execute(
            "SELECT id FROM roles WHERE tenant_id=1 AND code='AGENCY_OPERATOR'"
        ).fetchone()
        assert role
        db.execute(
            "INSERT INTO role_grants(tenant_id,membership_id,role_id) VALUES (1,%s,%s)",
            [member_a, role[0]],
        )
        db.execute(
            "INSERT INTO data_scopes(tenant_id,membership_id,scope_type,permissions_mask) "
            "VALUES (1,%s,'ALL_TENANT',ARRAY['snapshot.read'])",
            [member_a],
        )
        db.execute(
            "INSERT INTO entity_links(tenant_id,shop_id,account_id) VALUES (2,%s,%s)",
            [seeded["shop_b"], seeded["account_b"]],
        )
    actor_a = VerifiedIdentity(1, member_a, user)
    actor_b = VerifiedIdentity(2, member_b, user)
    with business_transaction(app_engine, actor_a) as session:
        require_shop_account(
            session, actor_a, seeded["shop_a"], seeded["account_a"], "snapshot.read"
        )
        with pytest.raises(ApiError):
            require_shop_account(
                session, actor_a, seeded["shop_a"], seeded["account_a"], "task.create"
            )
    with business_transaction(app_engine, actor_b) as session:
        with pytest.raises(ApiError):
            require_shop_account(
                session, actor_b, seeded["shop_b"], seeded["account_b"], "snapshot.read"
            )
