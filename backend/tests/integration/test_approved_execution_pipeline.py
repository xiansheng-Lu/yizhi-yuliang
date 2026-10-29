"""批准→真Rabbit子进程→真实loopback HTTP；生产平台写始终未启用。"""

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from kombu import Queue  # type: ignore[import-untyped]
from sqlalchemy import text

from yuliang.infrastructure.outbox import celery_sender, publish_one
from yuliang.platform.db import business_transaction
from yuliang.platform.messages import WorkMessage
from yuliang.settings import Settings
from yuliang.worker.app import create_celery

from .approved_execution_worker import LoopbackWriteProbe
from .test_approvals import pending, request, reviewer
from .test_execution_recovery import expire
from .test_execution_recovery_worker import (
    applications as applications,
)
from .test_execution_recovery_worker import (
    budget_ready as budget_ready,
)
from .test_execution_recovery_worker import (
    capability as capability,
)
from .test_execution_recovery_worker import (
    database as database,
)
from .test_execution_recovery_worker import (
    governance as governance,
)
from .test_execution_recovery_worker import (
    health_ready as health_ready,
)
from .test_execution_recovery_worker import (
    login_api as login_api,
)
from .test_execution_recovery_worker import (
    oauth_ready as oauth_ready,
)
from .test_execution_recovery_worker import (
    risk_ready as risk_ready,
)
from .test_execution_recovery_worker import (
    sync_ready as sync_ready,
)
from .test_identity_database import ROOT
from .test_infrastructure import POSIX_PREFORK_WORKER, local_url

pytestmark = [pytest.mark.integration, POSIX_PREFORK_WORKER]


def approved_message(data: dict[str, Any]) -> WorkMessage:
    job = pending(data)
    result = reviewer(data).review(request(job))
    assert result.job.approval.actor_id == str(data["approver"])
    with business_transaction(data["engine"], data["identity"]) as session:
        payload = session.execute(
            text(
                "SELECT payload FROM outbox_events WHERE aggregate_id=:job "
                "AND event_type='yuliang.system.approved_execution'"
            ),
            dict(job=job.job_id),
        ).scalar_one()
    item = WorkMessage.model_validate(payload)
    assert item.user_id == data["identity"].user_id != data["approver"]
    return item


def state(data: dict[str, Any], item: WorkMessage) -> dict[str, Any] | None:
    with business_transaction(data["engine"], data["identity"]) as session:
        value = session.execute(
            text("SELECT to_jsonb(d) FROM approved_execution_dispatches d WHERE event_id=:id"),
            dict(id=item.event_id),
        ).scalar_one_or_none()
    return dict(value) if value else None


def wait_state(data: dict[str, Any], item: WorkMessage, expected: str) -> dict[str, Any]:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        seed = data.get("_pipeline_seed")
        if seed and seed.with_suffix(".rejected").exists():
            rejected = json.loads(seed.with_suffix(".rejected").read_text())
            diagnostic = seed.with_suffix(".diagnostic")
            if not rejected["requeue"]:
                pytest.fail(
                    "真实Worker拒绝: "
                    + seed.with_suffix(".rejected").read_text()
                    + (diagnostic.read_text() if diagnostic.exists() else "")
                )
        value = state(data, item)
        if value and value["state"] == expected:
            return value
        time.sleep(0.1)
    pytest.fail("真实批准Worker未达到预期持久状态: " + expected)


def counts(data: dict[str, Any]) -> tuple[int, int, int]:
    with business_transaction(data["engine"], data["identity"]) as session:
        return tuple(
            session.execute(text(sql)).scalar_one()
            for sql in (
                "SELECT count(*) FROM execution_orders",
                "SELECT count(*) FROM execution_attempts",
                "SELECT count(*) FROM platform_requests WHERE effect='WRITE'",
            )
        )


@pytest.fixture
def pipeline(budget_ready: dict[str, Any], tmp_path: Path) -> Iterator[Any]:
    data = budget_ready
    runtimes: list[dict[str, Any]] = []

    def start(
        endpoint: str,
        *,
        closed: bool = False,
        crash: str = "",
        workers: int = 1,
        recovery_confirmation_lost: bool = False,
    ) -> Any:
        prefix = "test-approved-pipeline-" + uuid4().hex
        seed_path = tmp_path / (prefix + ".json")
        data["_pipeline_seed"] = seed_path
        context = asdict(data["ctx"])
        context.pop("identity")
        spec = asdict(data["spec"])
        spec.pop("parameters_model")
        seed_path.write_text(
            json.dumps(
                dict(
                    ctx=context,
                    spec=spec,
                    target=data["target"].model_dump(mode="json"),
                    endpoint=endpoint,
                    closed=closed,
                    crash=crash,
                    recovery_confirmation_lost=recovery_confirmation_lost,
                ),
                default=str,
            )
        )
        key = data["applications"].key.get_secret_value()
        settings = Settings(
            _env_file=None,
            database_url=data["database"]["app"],
            auth_database_url=data["database"]["auth"],
            redis_url=local_url("YULIANG_TEST_REDIS_URL"),
            broker_url=local_url("YULIANG_TEST_BROKER_URL"),
            session_secret=key,
            worker_queue_prefix=prefix,
        )
        client = create_celery(settings)
        env = os.environ.copy()
        env.update(
            YULIANG_DATABASE_URL=data["database"]["app"],
            YULIANG_AUTH_DATABASE_URL=data["database"]["auth"],
            YULIANG_REDIS_URL=settings.redis_url.get_secret_value(),
            YULIANG_BROKER_URL=settings.broker_url.get_secret_value(),
            YULIANG_SESSION_SECRET=key,
            YULIANG_AUTH_AUDIT_SECRET=key,
            YULIANG_WORKER_QUEUE_PREFIX=prefix,
            YULIANG_TEST_APPROVED_SEED=str(seed_path),
            YULIANG_IDENTITY_API_ENABLED="false",
            YULIANG_ADMIN_API_ENABLED="false",
            YULIANG_EMAIL_LOGIN_ENABLED="false",
        )
        runtime: dict[str, Any] = dict(
            client=client, prefix=prefix, processes=[], results=[], seed=seed_path
        )
        runtimes.append(runtime)
        for index in range(workers):
            hostname = f"{prefix}-{index}@{socket.gethostname()}"
            process = subprocess.Popen(  # noqa: S603 -- 固定隔离项目Worker，不打印环境凭据。
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "tests.integration.approved_execution_worker:app",
                    "worker",
                    "--pool=prefork",
                    "--concurrency=1",
                    "--without-gossip",
                    "--without-mingle",
                    "--without-heartbeat",
                    f"--hostname={hostname}",
                    "-Q",
                    prefix + ".execute," + prefix + ".system",
                ],
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            runtime["processes"].append(process)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                assert process.poll() is None, "本fixture批准Worker启动失败"
                if client.control.ping(destination=[hostname], timeout=0.5):
                    break
                time.sleep(0.1)
            else:
                pytest.fail("本fixture批准Worker未就绪")
        return runtime

    yield start
    for runtime in runtimes:
        for process in runtime["processes"]:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for result in runtime["results"]:
            result.forget()
        with runtime["client"].connection_for_write() as connection:
            Queue(runtime["prefix"] + ".execute")(connection).delete()
            Queue(runtime["prefix"] + ".system")(connection).delete()
            connection.channel().exchange_delete(exchange=runtime["prefix"], if_unused=True)
        runtime["client"].close()


def sent_committed(data: dict[str, Any]) -> None:
    with business_transaction(data["engine"], data["identity"]) as session:
        row = session.execute(
            text("SELECT state,dispatch_consumed_at FROM execution_attempts")
        ).one()
        assert row.state == "SENT" and row.dispatch_consumed_at is not None


def publish(data: dict[str, Any], runtime: dict[str, Any], item: WorkMessage) -> None:
    assert publish_one(
        data["engine"], data["identity"], item.event_id, celery_sender(runtime["client"])
    )
    runtime["results"].append(runtime["client"].AsyncResult(str(item.event_id)))


def duplicate(runtime: dict[str, Any], item: WorkMessage) -> Any:
    result = runtime["client"].send_task(
        "yuliang.system.approved_execution",
        kwargs={"payload": item.model_dump(mode="json")},
        queue=runtime["prefix"] + ".execute",
    )
    runtime["results"].append(result)
    return result


def test_real_rabbit_a_created_b_approved_two_workers_one_write_and_receipt(
    budget_ready: dict[str, Any],
    pipeline: Any,
) -> None:
    data = budget_ready
    item = approved_message(data)
    with LoopbackWriteProbe(lambda: sent_committed(data)) as probe:
        runtime = pipeline(probe.endpoint, workers=2)
        publish(data, runtime, item)
        second = duplicate(runtime, item)
        wait_state(data, item, "RECEIPT_RECORDED")
        second.get(timeout=25)
        assert counts(data) == (1, 1, 1) and len(probe.calls) == 1 and not probe.errors
        duplicate(runtime, item).get(timeout=25)
        assert len(probe.calls) == 1


def test_real_rabbit_default_closed_persists_block_without_attempt_or_http(
    budget_ready: dict[str, Any],
    pipeline: Any,
) -> None:
    data = budget_ready
    item = approved_message(data)
    with LoopbackWriteProbe(lambda: sent_committed(data)) as probe:
        runtime = pipeline(probe.endpoint, closed=True)
        publish(data, runtime, item)
        wait_state(data, item, "BLOCKED_EXECUTOR")
        duplicate(runtime, item).get(timeout=25)
        assert counts(data) == (0, 0, 0) and probe.calls == []


@pytest.mark.parametrize("crash,wire_calls", [("after_prepare", 0), ("before_response_persist", 1)])
def test_real_rabbit_child_exit_after_sent_redelivers_to_recovery_without_second_write(
    budget_ready: dict[str, Any],
    pipeline: Any,
    crash: str,
    wire_calls: int,
) -> None:
    data = budget_ready
    item = approved_message(data)
    with LoopbackWriteProbe(lambda: sent_committed(data)) as probe:
        runtime = pipeline(probe.endpoint, crash=crash, recovery_confirmation_lost=wire_calls == 1)
        publish(data, runtime, item)
        deadline = time.monotonic() + 15
        while not runtime["seed"].with_suffix("." + crash).exists():
            assert time.monotonic() < deadline, "子进程未抵达实际退出故障点"
            time.sleep(0.05)
        assert counts(data) == (1, 1, wire_calls)
        expire(data)
        runtime["seed"].with_suffix(".allow_exit").write_text("ALLOW\n")
        wait_state(data, item, "RECOVERY_REQUIRED")
        deadline = time.monotonic() + 30
        proof = None
        while time.monotonic() < deadline:
            with business_transaction(data["engine"], data["identity"]) as session:
                proof = session.execute(
                    text(
                        "SELECT kind,evidence_level,network_attempts "
                        "FROM execution_recovery_evidence WHERE job_id=:job "
                        "ORDER BY created_at LIMIT 1"
                    ),
                    dict(job=item.job_id),
                ).first()
            if proof:
                break
            time.sleep(0.1)
        assert proof is not None, "真实SYSTEM Worker未运行Inspector产生恢复依据"
        assert tuple(proof) == (("NOT_SENT", "E1", 0) if wire_calls == 0 else ("UNKNOWN", "E0", 1))
        if wire_calls:
            assert runtime["seed"].with_suffix(".recovery_confirmation_lost").exists()
        duplicate(runtime, item).get(timeout=25)
        assert counts(data) == (1, 1, wire_calls)
        assert len(probe.calls) == wire_calls and not probe.errors


def test_real_rabbit_publish_confirmation_lost_republish_same_event_single_write(
    budget_ready: dict[str, Any],
    pipeline: Any,
) -> None:
    data = budget_ready
    item = approved_message(data)
    with LoopbackWriteProbe(lambda: sent_committed(data)) as probe:
        runtime = pipeline(probe.endpoint)
        sender = celery_sender(runtime["client"])

        def lost_confirmation(event: UUID, kind: str, payload: dict[str, Any]) -> None:
            sender(event, kind, payload)
            runtime["results"].append(runtime["client"].AsyncResult(str(event)))
            raise ConnectionError("synthetic publish confirmation lost after broker accepted")

        assert not publish_one(data["engine"], data["identity"], item.event_id, lost_confirmation)
        wait_state(data, item, "RECEIPT_RECORDED")
        publish(data, runtime, item)
        duplicate(runtime, item).get(timeout=25)
        assert counts(data) == (1, 1, 1) and len(probe.calls) == 1 and not probe.errors


def test_real_rabbit_forged_job_envelope_does_not_dispatch_original_event(
    budget_ready: dict[str, Any],
    pipeline: Any,
) -> None:
    data = budget_ready
    item = approved_message(data)
    with LoopbackWriteProbe(lambda: sent_committed(data)) as probe:
        runtime = pipeline(probe.endpoint)
        forged = item.model_copy(update={"job_id": uuid4()})
        duplicate(runtime, forged)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if runtime["seed"].with_suffix(".rejected").exists():
                break
            time.sleep(0.1)
        else:
            pytest.fail("伪造消息未抵达实际Worker REJECTED信号屏障")
        rejected = json.loads(runtime["seed"].with_suffix(".rejected").read_text())
        assert rejected == {"state": "REJECTED", "requeue": False}
        assert state(data, item) is None and counts(data) == (0, 0, 0) and not probe.calls
        runtime["seed"].with_suffix(".rejected").unlink()
        publish(data, runtime, item)
        wait_state(data, item, "RECEIPT_RECORDED")
        assert len(probe.calls) == 1 and not probe.errors


def test_real_rabbit_current_authorization_revoked_blocks_before_wire(
    budget_ready: dict[str, Any],
    pipeline: Any,
) -> None:
    data = budget_ready
    item = approved_message(data)
    with data["maintenance"].begin() as session:
        session.execute(text("SET LOCAL ROLE app_owner"))
        session.execute(
            text(
                "UPDATE provider_authorizations SET status='REVOKED',version=version+1 WHERE id=:id"
            ),
            dict(id=data["ctx"].authorization_id),
        )
    with LoopbackWriteProbe(lambda: sent_committed(data)) as probe:
        runtime = pipeline(probe.endpoint)
        publish(data, runtime, item)
        wait_state(data, item, "BLOCKED_AUTH")
        duplicate(runtime, item).get(timeout=25)
        assert counts(data) == (0, 0, 0) and not probe.calls
