"""仅在显式提供本地测试配置后运行，不建立/删除容器或任意业务数据。"""

import os
from urllib.parse import urlsplit

import httpx
import psycopg
import pytest
import redis
from kombu import Connection  # type: ignore[import-untyped]


def local_url(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"BLOCKED M0-04：缺少{name}，不能以Mock替代真实基础设施")
    parsed = urlsplit(value)
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        pytest.fail("基础设施冒烟仅允许本地独立环境")
    return value


def test_postgres_extensions_and_unprivileged_application_role() -> None:
    with psycopg.connect(local_url("YULIANG_TEST_DATABASE_URL"), connect_timeout=5) as connection:
        row = connection.execute(
            "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles "
            "WHERE rolname = current_user"
        ).fetchone()
        assert row == (False, False, False, False)
        assert connection.execute("SELECT current_user").fetchone() == ("app_login",)
        assert connection.execute(
            "SELECT pg_get_userbyid(datdba) = current_user FROM pg_database "
            "WHERE datname = current_database()"
        ).fetchone() == (False,)
        assert connection.execute(
            "SELECT has_schema_privilege(current_user, 'public', 'CREATE')"
        ).fetchone() == (False,)
        for role in ("app_auth", "app_migrate"):
            assert connection.execute(
                "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles "
                "WHERE rolname = %s",
                [role],
            ).fetchone() == (False, False, False, False)
        version = connection.execute("SHOW server_version_num").fetchone()
        assert version is not None and 170000 <= int(version[0]) < 180000
        extensions = connection.execute("SELECT extname FROM pg_extension").fetchall()
        assert {"vector", "pgcrypto"} <= {row[0] for row in extensions}


def test_redis_real_ping() -> None:
    with redis.Redis.from_url(local_url("YULIANG_TEST_REDIS_URL"), socket_timeout=5) as connection:
        assert connection.ping()


def test_amqp_real_connection() -> None:
    with Connection(local_url("YULIANG_TEST_BROKER_URL"), connect_timeout=5) as connection:
        connection.ensure_connection(max_retries=0)
        assert connection.connected


def test_object_storage_ready() -> None:
    endpoint = local_url("YULIANG_TEST_S3_URL").rstrip("/")
    response = httpx.get(f"{endpoint}/minio/health/ready", timeout=5, trust_env=False)
    assert response.status_code == 200
