"""真实RabbitMQ/Redis/PG及prefork Worker进程；只读探针不会调用平台。"""

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import redis
from kombu import Queue  # type: ignore[import-untyped]

from yuliang.settings import Settings
from yuliang.worker.app import KINDS, create_celery
from yuliang.worker.scheduler import BeatLease

from .test_identity_database import ROOT
from .test_identity_database import database as database
from .test_identity_database import seeded as seeded
from .test_infrastructure import local_url


@pytest.fixture(scope="module")
def worker(database: dict[str, str], seeded: dict[str, Any]) -> Iterator[dict[str, Any]]:
    prefix = "test-worker-" + uuid4().hex
    settings = Settings(
        _env_file=None,
        database_url=database["app"],
        auth_database_url=database["auth"],
        redis_url=local_url("YULIANG_TEST_REDIS_URL"),
        broker_url=local_url("YULIANG_TEST_BROKER_URL"),
        session_secret="test-worker-secret-with-more-than-32-characters",
        worker_queue_prefix=prefix,
    )
    client = create_celery(settings)
    env = os.environ.copy()
    env.update(
        YULIANG_DATABASE_URL=database["app"],
        YULIANG_AUTH_DATABASE_URL=database["auth"],
        YULIANG_REDIS_URL=settings.redis_url.get_secret_value(),
        YULIANG_BROKER_URL=settings.broker_url.get_secret_value(),
        YULIANG_SESSION_SECRET=settings.session_secret.get_secret_value(),
        YULIANG_IDENTITY_API_ENABLED="false",
        YULIANG_ADMIN_API_ENABLED="false",
        YULIANG_WORKER_QUEUE_PREFIX=prefix,
    )
    hostname = f"{prefix}@{socket.gethostname()}"
    process = subprocess.Popen(  # noqa: S603 -- 固定Worker入口，凭据只在环境中。
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "yuliang.worker.app:app",
            "worker",
            "--pool=prefork",
            "--concurrency=1",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
            "--loglevel=ERROR",
            "--hostname=" + hostname,
            "--queues=" + ",".join(f"{prefix}.{kind}" for kind in KINDS),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    results: list[Any] = []
    try:
        until = time.monotonic() + 25
        while time.monotonic() < until:
            assert process.poll() is None, "Worker在就绪前退出"
            if client.control.ping(destination=[hostname], timeout=0.5):
                break
            time.sleep(0.2)
        else:
            pytest.fail("真实Worker未就绪")
        yield {
            "client": client,
            "hostname": hostname,
            "results": results,
            "data": seeded,
            "database": database,
            "prefix": prefix,
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        for result in results:
            result.forget()
        with client.connection_for_write() as connection:
            for kind in KINDS:
                Queue(f"{prefix}.{kind}")(connection).delete()
            connection.channel().exchange_delete(exchange=prefix, if_unused=True)
        client.close()


def send(
    worker: dict[str, Any], name: str = "yuliang.system.identity_probe", tenant: int = 1
) -> Any:
    data = worker["data"]
    payload = {
        "event_id": str(uuid4()),
        "job_id": str(uuid4()),
        "tenant_id": tenant,
        "membership_id": str(data["member_a"] if tenant == 1 else data["member_b"]),
        "user_id": str(data["user"]),
    }
    result = worker["client"].send_task(name, kwargs={"payload": payload}, kwargsrepr="<ids-only>")
    worker["results"].append(result)
    return result


def test_m0_15_real_worker_context_is_per_task(worker: dict[str, Any]) -> None:
    first = send(worker).get(timeout=20)
    second = send(worker, tenant=2).get(timeout=20)
    assert first["tenantId"] == "1" and second["tenantId"] == "2"
    assert first["workerPid"] == second["workerPid"]
    data = worker["data"]
    forged = {
        "event_id": str(uuid4()),
        "job_id": str(uuid4()),
        "tenant_id": 2,
        "membership_id": str(data["member_a"]),
        "user_id": str(data["user"]),
    }
    bad = worker["client"].send_task("yuliang.system.identity_probe", kwargs={"payload": forged})
    worker["results"].append(bad)
    assert isinstance(bad.get(timeout=20, propagate=False), ValueError)
    assert send(worker).get(timeout=20)["tenantId"] == "1"


def test_m0_15_three_side_effect_queues_fail_closed_without_retry(worker: dict[str, Any]) -> None:
    for name in ("yuliang.execute.guard", "yuliang.platform.guard", "yuliang.ai_media.guard"):
        result = send(worker, name)
        assert isinstance(result.get(timeout=20, propagate=False), RuntimeError)
        assert result.state == "FAILURE"
    assert worker["client"].conf.task_publish_retry is False
    assert worker["client"].conf.task_reject_on_worker_lost is False


def test_m0_15_readonly_task_is_redelivered_after_child_loss(worker: dict[str, Any]) -> None:
    # 只锁测试库的成员表，让实际只读任务处于数据库等待；不增加生产测试钩子。
    with psycopg.connect(worker["database"]["admin"]) as lock:
        lock.execute("LOCK TABLE memberships IN ACCESS EXCLUSIVE MODE")
        result = send(worker)
        until = time.monotonic() + 15
        child = None
        while time.monotonic() < until:
            active = (
                worker["client"]
                .control.inspect(destination=[worker["hostname"]], timeout=1)
                .active()
                or {}
            )
            task = next(
                (row for row in active.get(worker["hostname"], []) if row["id"] == result.id), None
            )
            if task:
                child = task["worker_pid"]
                break
            time.sleep(0.1)
        assert child, "任务未进入实际Worker子进程"
        os.kill(child, signal.SIGKILL)  # 仅本测试启动Worker的已确认活动子进程。
    recovered = result.get(timeout=25)
    assert recovered["redelivered"] is True
    assert recovered["workerPid"] != child
    assert recovered["retries"] == 0


def test_m0_15_beat_lease_is_exclusive_and_fenced() -> None:
    with redis.Redis.from_url(local_url("YULIANG_TEST_REDIS_URL")) as cache:
        prefix = "test-beat-" + uuid4().hex
        first, second = BeatLease(cache, prefix), BeatLease(cache, prefix)
        try:
            assert first.acquire() and first.acquire()
            assert not second.acquire()
            second.release()
            assert first.acquire()
            first.release()
            assert second.acquire() and not first.acquire()
        finally:
            first.release()
            second.release()
