"""仅显式--lifecycle运行：写独立探针、重复初始化、重启并比较四类真实数据。"""

import hashlib
import hmac
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
import redis
from kombu import Connection, Queue  # type: ignore[import-untyped]

from .test_infrastructure import local_url


def s3(method: str, path: str, body: bytes = b"") -> httpx.Response:
    endpoint = local_url("YULIANG_TEST_S3_URL").rstrip("/")
    now = datetime.now(UTC)
    timestamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    host = httpx.URL(endpoint).netloc.decode()
    digest = hashlib.sha256(body).hexdigest()
    headers = f"host:{host}\nx-amz-content-sha256:{digest}\nx-amz-date:{timestamp}\n"
    signed = "host;x-amz-content-sha256;x-amz-date"
    canonical = f"{method}\n{path}\n\n{headers}\n{signed}\n{digest}"
    scope = f"{day}/us-east-1/s3/aws4_request"
    to_sign = (
        f"AWS4-HMAC-SHA256\n{timestamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    )
    key = ("AWS4" + os.environ["YULIANG_TEST_S3_SECRET"]).encode()
    for part in (day, "us-east-1", "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return httpx.request(
        method,
        endpoint + path,
        content=body,
        headers={
            "x-amz-date": timestamp,
            "x-amz-content-sha256": digest,
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential=yuliang-local/{scope}, "
                f"SignedHeaders={signed}, Signature={signature}"
            ),
        },
        timeout=15,
        trust_env=False,
    )


def compose(*args: str) -> None:
    root = Path(__file__).resolve().parents[2]
    docker = shutil.which("docker")
    assert docker, "需要在具有Docker CLI的WSL/Linux中执行生命周期测试"
    result = subprocess.run(  # noqa: S603 -- 固定本项目Compose入口。
        [
            docker,
            "compose",
            "--project-name",
            "yuliang-local",
            "--env-file",
            "deployment/.env.infrastructure",
            "--file",
            "docker-compose.yml",
            *args,
        ],
        cwd=root,
        capture_output=True,
        check=False,
        timeout=240,
    )
    assert result.returncode == 0, "本项目Compose操作失败；不输出可能含凭据的容器日志"


def test_reinitialize_and_restart_preserve_real_data() -> None:
    if os.environ.get("YULIANG_TEST_LIFECYCLE") != "1":
        pytest.skip("显式使用scripts/test_infrastructure.py --lifecycle才允许重启本项目容器")
    probe = "infra_probe_" + uuid4().hex
    payload = uuid4().hex
    admin = local_url("YULIANG_TEST_ADMIN_DATABASE_URL")
    broker = local_url("YULIANG_TEST_BROKER_URL")
    cache_url = local_url("YULIANG_TEST_REDIS_URL")
    # SQL标识符全部由UUID生成，通过Identifier转义；独立schema不写业务表。
    from psycopg import sql

    with psycopg.connect(admin, autocommit=True) as db:
        db.execute(
            sql.SQL("CREATE SCHEMA {} AUTHORIZATION app_migrate").format(sql.Identifier(probe))
        )
        db.execute(
            sql.SQL("CREATE TABLE {}.probe (value text NOT NULL)").format(sql.Identifier(probe))
        )
        db.execute(
            sql.SQL("INSERT INTO {}.probe VALUES (%s)").format(sql.Identifier(probe)), [payload]
        )
        roles_before = db.execute("SELECT rolname FROM pg_roles ORDER BY rolname").fetchall()
    try:
        with redis.Redis.from_url(cache_url) as cache:
            cache.set(probe, payload)
        with Connection(broker) as connection:
            queue = Queue(probe, durable=True)(connection)
            queue.declare()
            connection.Producer().publish(
                payload, routing_key=probe, delivery_mode=2, serializer="json"
            )
        assert s3("PUT", f"/{probe.replace('_', '-')}").status_code == 200
        object_path = f"/{probe.replace('_', '-')}/probe"
        assert s3("PUT", object_path, payload.encode()).status_code == 200
        compose("exec", "-T", "postgres", "sh", "/docker-entrypoint-initdb.d/10-yuliang.sh")
        compose("exec", "-T", "postgres", "sh", "/docker-entrypoint-initdb.d/10-yuliang.sh")
        compose("restart")
        compose("up", "-d", "--wait", "--wait-timeout", "180")
        with psycopg.connect(admin, autocommit=True) as db:
            assert db.execute(
                sql.SQL("SELECT value FROM {}.probe").format(sql.Identifier(probe))
            ).fetchall() == [(payload,)]
            assert (
                db.execute("SELECT rolname FROM pg_roles ORDER BY rolname").fetchall()
                == roles_before
            )
        with redis.Redis.from_url(cache_url) as cache:
            assert cache.get(probe) == payload.encode()
        with Connection(broker) as connection:
            queue = Queue(probe, durable=True)(connection)
            message = queue.get(no_ack=False)
            assert message is not None and message.payload == payload
            message.ack()
        response = s3("GET", object_path)
        assert response.status_code == 200
        assert (
            hashlib.sha256(response.content).hexdigest()
            == hashlib.sha256(payload.encode()).hexdigest()
        )
    finally:
        with psycopg.connect(admin, autocommit=True) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(probe)))
        with redis.Redis.from_url(cache_url) as cache:
            cache.delete(probe)
        with Connection(broker) as connection:
            Queue(probe, durable=True)(connection).delete()
        assert s3("DELETE", f"/{probe.replace('_', '-')}/probe").status_code in (204, 404)
        assert s3("DELETE", f"/{probe.replace('_', '-')}").status_code in (204, 404)
