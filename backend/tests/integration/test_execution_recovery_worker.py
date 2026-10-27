"""真实prefork Worker/PG/MQ/Redis：重复与提交前杀子进程恢复。"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from kombu import Queue  # type: ignore[import-untyped]
from sqlalchemy import text

from yuliang.modules.execution.recovery_schedule import celery_recovery_sender, publish_schedule
from yuliang.platform.db import business_transaction
from yuliang.settings import Settings
from yuliang.worker.app import KINDS, create_celery

from .test_budget_initialization import budget_ready as budget_ready
from .test_budget_settlement import ledger
from .test_capability_evidence import capability as capability
from .test_execution_attempts import prepare
from .test_execution_recovery import event, expire
from .test_identity_database import ROOT
from .test_identity_database import database as database
from .test_infrastructure import local_url
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import risk_ready as risk_ready
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def recovery_worker(database: dict[str, str]) -> Iterator[dict[str, Any]]:
    prefix = "test-execution-recovery-" + uuid4().hex
    settings = Settings(
        _env_file=None,
        database_url=database["app"],
        auth_database_url=database["auth"],
        redis_url=local_url("YULIANG_TEST_REDIS_URL"),
        broker_url=local_url("YULIANG_TEST_BROKER_URL"),
        session_secret="synthetic-recovery-worker-secret-more-than-32-chars",
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
        YULIANG_EMAIL_LOGIN_ENABLED="false",
        YULIANG_WORKER_QUEUE_PREFIX=prefix,
    )
    hostname = f"{prefix}@{socket.gethostname()}"
    process = subprocess.Popen(  # noqa: S603 -- 固定项目Worker，仅凭据环境，不打印。
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
            f"--hostname={hostname}",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    results: list[Any] = []
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            assert process.poll() is None, "恢复Worker启动失败"
            if client.control.ping(destination=[hostname], timeout=0.5):
                break
            time.sleep(0.2)
        else:
            pytest.fail("真实恢复Worker未就绪")
        yield {
            "client": client,
            "hostname": hostname,
            "process": process,
            "results": results,
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


def test_real_worker_finishes_once_then_duplicate_consumption(
    budget_ready: dict[str, Any], recovery_worker: dict[str, Any]
) -> None:
    data, runtime = budget_ready, recovery_worker
    _, prepared, _ = prepare(data)
    identity = event(data, prepared.attempt.attempt_id)
    expire(data)
    assert publish_schedule(
        data["engine"], data["identity"], identity, celery_recovery_sender(runtime["client"])
    )
    pending = runtime["client"].AsyncResult(str(identity))
    runtime["results"].append(pending)
    first = pending.get(timeout=20)
    assert first["processed"] and first["recovery"]["outcome"] == "NOT_SENT"
    assert first["workerPid"] != os.getpid() and ledger(data)[:2] == (9990, 0)
    pending.forget()
    with business_transaction(data["engine"], data["identity"]) as session:
        payload = session.execute(
            text("SELECT payload FROM outbox_events WHERE event_id=:id"), {"id": identity}
        ).scalar_one()
    second = runtime["client"].send_task(
        "yuliang.system.execution_recovery",
        task_id=str(identity),
        kwargs={"payload": payload},
        kwargsrepr="<ids-only>",
    )
    runtime["results"].append(second)
    replay = second.get(timeout=20)
    assert not replay["processed"] and replay["recovery"] == first["recovery"]
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(text("SELECT count(*) FROM budget_job_finalizations")).scalar_one() == 1
        )
        assert (
            session.execute(text("SELECT count(*) FROM execution_recovery_evidence")).scalar_one()
            == 1
        )


def test_real_worker_lost_after_money_write_rolls_back_then_redelivers(
    budget_ready: dict[str, Any], recovery_worker: dict[str, Any]
) -> None:
    data, runtime = budget_ready, recovery_worker
    _, prepared, _ = prepare(data)
    identity = event(data, prepared.attempt.attempt_id)
    expire(data)
    # 只在此隔离测试库安装提交前屏障；生产实现没有进程退出/故障开关。
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.exec_driver_sql("""CREATE FUNCTION public.recovery_test_commit_barrier()
        RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$ BEGIN
         IF NEW.action='execution.recover' THEN
          PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text,760991)); END IF;
         RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION public.recovery_test_commit_barrier() FROM PUBLIC,app_login,app_auth;
        CREATE TRIGGER recovery_test_commit_barrier BEFORE INSERT ON audit_logs
         FOR EACH ROW EXECUTE FUNCTION public.recovery_test_commit_barrier();""")
    with data["maintenance"].connect() as blocker:
        blocker.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:tenant,760991))"),
            {"tenant": str(data["tenant"])},
        )
        blocker.commit()
        try:
            assert publish_schedule(
                data["engine"],
                data["identity"],
                identity,
                celery_recovery_sender(runtime["client"]),
            )
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                # app_migrate无权查看app_login的wait_event；只用隔离库admin观察进程，
                # 不扩大任何生产角色权限，也不输出连接信息。
                with psycopg.connect(data["database"]["admin"]) as observer:
                    waiting_row = observer.execute(
                        """SELECT count(*) FROM pg_stat_activity
                    WHERE datname=current_database() AND usename='app_login'
                     AND wait_event='advisory'"""
                    ).fetchone()
                    assert waiting_row is not None
                    waiting = waiting_row[0]
                if waiting:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("Worker未抵达财务写后、审计提交前屏障")
            stats = (
                runtime["client"]
                .control.inspect(destination=[runtime["hostname"]], timeout=2)
                .stats()
            )
            child = stats[runtime["hostname"]]["pool"]["processes"][0]
            # 只杀由本fixture启动的prefork子进程，先核对父PID归属。
            process_info = Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()
            assert int(process_info[1]) == runtime["process"].pid
            os.kill(child, signal.SIGKILL)
            time.sleep(0.5)
            assert ledger(data)[:2] == (9990, 10)
            assert (
                data["applications"].get(prepared.attempt.business.id).status.value == "EXECUTING"
            )
            with business_transaction(data["engine"], data["identity"]) as session:
                assert (
                    session.execute(
                        text("SELECT count(*) FROM budget_job_finalizations")
                    ).scalar_one()
                    == 0
                )
                assert (
                    session.execute(
                        text("SELECT count(*) FROM execution_recovery_evidence")
                    ).scalar_one()
                    == 0
                )
        finally:
            blocker.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:tenant,760991))"),
                {"tenant": str(data["tenant"])},
            )
            blocker.commit()
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        pending = runtime["client"].AsyncResult(str(identity))
        if pending.state == "SUCCESS":
            result = pending.get(timeout=2)
            runtime["results"].append(pending)
            break
        time.sleep(0.2)
    else:
        pytest.fail("Worker失联后原消息没有实际重投完成")
    assert result["workerPid"] != child and result["redelivered"]
    assert result["recovery"]["outcome"] == "NOT_SENT" and ledger(data)[:2] == (9990, 0)
    if evidence := os.getenv("YULIANG_RECOVERY_EVIDENCE"):
        Path(evidence).write_text(
            json.dumps(
                {
                    "eventId": str(identity),
                    "jobId": str(prepared.attempt.business.id),
                    "attemptId": str(prepared.attempt.attempt_id),
                    "killedWorkerPid": child,
                    "replacementWorkerPid": result["workerPid"],
                    "redelivered": result["redelivered"],
                    "recovery": result["recovery"],
                    "beforeCommitRollback": {
                        "B": 9990,
                        "R": 10,
                        "jobStatus": "EXECUTING",
                        "proofCount": 0,
                        "finalizationCount": 0,
                    },
                    "final": {"B": 9990, "R": 0, "platformReceipt": None},
                    "realPlatformCalls": 0,
                    "paidCalls": 0,
                    "proof": "REAL_PREFORK_SIGKILL_PG_ROLLBACK_MQ_REDELIVERY_REDIS_RESULT",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(text("SELECT count(*) FROM budget_job_finalizations")).scalar_one() == 1
        )
        assert (
            session.execute(text("SELECT count(*) FROM execution_recovery_evidence")).scalar_one()
            == 1
        )
