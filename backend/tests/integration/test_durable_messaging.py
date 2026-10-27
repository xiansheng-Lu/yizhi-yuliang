"""真实PG/MQ下的Outbox原子性、mandatory确认、Inbox并发和多崩溃点恢复。"""

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from kombu import Exchange, Producer, Queue  # type: ignore[import-untyped]
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from yuliang.infrastructure.outbox import (
    celery_sender,
    consume,
    dispatch_pending,
    emit,
    publish_one,
)
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.messages import WorkMessage
from yuliang.settings import Settings
from yuliang.worker.app import create_celery

from .test_identity_database import ROOT
from .test_identity_database import database as database
from .test_identity_database import seeded as seeded
from .test_infrastructure import local_url
from .test_worker import worker as worker


@pytest.fixture
def transport(database: dict[str, str], seeded: dict[str, Any]) -> Iterator[dict[str, Any]]:
    prefix = "test-outbox-" + uuid4().hex
    settings = Settings(
        _env_file=None,
        database_url=database["app"],
        auth_database_url=database["auth"],
        redis_url=local_url("YULIANG_TEST_REDIS_URL"),
        broker_url=local_url("YULIANG_TEST_BROKER_URL"),
        session_secret="test-outbox-secret-with-at-least-32-characters",
        worker_queue_prefix=prefix,
    )
    client = create_celery(settings)
    with client.connection_for_write() as connection:
        exchange = Exchange(prefix, type="direct", durable=True)
        Queue(prefix + ".system", exchange=exchange, routing_key=prefix + ".system", durable=True)(
            connection
        ).declare()
    engine = create_engine(database["app"], hide_parameters=True)
    identity = VerifiedIdentity(1, seeded["member_a"], seeded["user"])
    item = WorkMessage(
        event_id=uuid4(),
        job_id=uuid4(),
        tenant_id=1,
        membership_id=seeded["member_a"],
        user_id=seeded["user"],
    )
    with business_transaction(engine, identity) as session:
        session.execute(text("UPDATE shops SET name='shop' WHERE id=:id"), {"id": seeded["shop_a"]})
    try:
        yield {
            "settings": settings,
            "client": client,
            "engine": engine,
            "identity": identity,
            "item": item,
            "data": seeded,
            "database": database,
            "prefix": prefix,
            "sender": celery_sender(client),
        }
    finally:
        with client.connection_for_write() as connection:
            Queue(prefix + ".system")(connection).delete()
            connection.channel().exchange_delete(exchange=prefix, if_unused=True)
        client.close()
        engine.dispose()


def enqueue(value: dict[str, Any]) -> None:
    with business_transaction(value["engine"], value["identity"]) as session:
        emit(session, value["item"], "yuliang.system.schedule_recovery")


def receive(value: dict[str, Any], operation: Any = None) -> bool:
    with value["client"].connection_for_read() as connection:
        queue = Queue(value["prefix"] + ".system")(connection)
        deadline = time.monotonic() + 6
        delivery = None
        while delivery is None and time.monotonic() < deadline:
            delivery = queue.get(no_ack=False)
            if delivery is None:
                time.sleep(0.05)
        assert delivery is not None, "恢复后没有真实MQ消息"
        assert delivery.headers["id"] == str(value["item"].event_id)
        if operation is None:

            def operation(session: Session) -> None:
                session.execute(
                    text("UPDATE shops SET name=name||:mark WHERE id=:id"),
                    {"mark": "|" + str(value["item"].event_id)[:8], "id": value["data"]["shop_a"]},
                )

        changed = consume(
            value["engine"], value["identity"], "system.recovery", value["item"].event_id, operation
        )
        delivery.ack()
        return changed


def assert_once(value: dict[str, Any]) -> None:
    with business_transaction(value["engine"], value["identity"]) as session:
        name = session.execute(
            text("SELECT name FROM shops WHERE id=:id"), {"id": value["data"]["shop_a"]}
        ).scalar_one()
        assert name.count("|" + str(value["item"].event_id)[:8]) == 1
    with psycopg.connect(value["database"]["admin"]) as db:
        assert db.execute(
            "SELECT processed_at IS NOT NULL FROM consumer_inbox "
            "WHERE consumer='system.recovery' AND event_id=%s",
            [value["item"].event_id],
        ).fetchone() == (True,)


def test_m0_16a_business_and_event_rollback_together(transport: dict[str, Any]) -> None:
    with (
        pytest.raises(RuntimeError),
        business_transaction(transport["engine"], transport["identity"]) as session,
    ):
        session.execute(
            text("UPDATE shops SET name='must roll back' WHERE id=:id"),
            {"id": transport["data"]["shop_a"]},
        )
        emit(session, transport["item"], "yuliang.system.schedule_recovery")
        raise RuntimeError("transaction rollback")
    with business_transaction(transport["engine"], transport["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM outbox_events WHERE event_id=:id"),
                {"id": transport["item"].event_id},
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text("SELECT name FROM shops WHERE id=:id"), {"id": transport["data"]["shop_a"]}
            ).scalar_one()
            == "shop"
        )


def test_m0_16a_mandatory_unroutable_stays_pending(transport: dict[str, Any]) -> None:
    enqueue(transport)

    def unroutable(event: UUID, name: str, payload: dict[str, Any]) -> None:
        with transport["client"].connection_for_write() as connection:
            exchange = Exchange(transport["prefix"], type="direct", durable=True)
            Producer(connection, exchange=exchange).publish(
                payload,
                serializer="json",
                mandatory=True,
                retry=False,
                routing_key="not-bound-" + uuid4().hex,
                declare=[exchange],
            )

    assert not publish_one(
        transport["engine"], transport["identity"], transport["item"].event_id, unroutable
    )
    with business_transaction(transport["engine"], transport["identity"]) as session:
        row = session.execute(
            text("SELECT state,attempts,last_error_code FROM outbox_events WHERE event_id=:id"),
            {"id": transport["item"].event_id},
        ).one()
        assert row == ("PENDING", 1, "MQ_UNAVAILABLE")
    assert publish_one(
        transport["engine"], transport["identity"], transport["item"].event_id, transport["sender"]
    )
    assert receive(transport)
    assert_once(transport)


def test_m0_16b_received_is_resumable_and_duplicates_serialize(transport: dict[str, Any]) -> None:
    enqueue(transport)

    def fail(session: Session) -> None:
        session.execute(
            text("UPDATE shops SET name='must roll back' WHERE id=:id"),
            {"id": transport["data"]["shop_a"]},
        )
        raise RuntimeError("rollback before processed")

    with pytest.raises(RuntimeError):
        consume(
            transport["engine"],
            transport["identity"],
            "system.recovery",
            transport["item"].event_id,
            fail,
        )
    with psycopg.connect(transport["database"]["admin"]) as db:
        assert db.execute(
            "SELECT processed_at FROM consumer_inbox WHERE event_id=%s",
            [transport["item"].event_id],
        ).fetchone() == (None,)
    # 同一事件真实发布两次，两个真实消费者争用Inbox行锁，只有一个内部副作用。
    transport["sender"](
        transport["item"].event_id,
        "yuliang.system.schedule_recovery",
        transport["item"].model_dump(mode="json"),
    )
    transport["sender"](
        transport["item"].event_id,
        "yuliang.system.schedule_recovery",
        transport["item"].model_dump(mode="json"),
    )
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: receive(transport), range(2)))
    assert sorted(results) == [False, True]
    assert_once(transport)


def test_m0_16b_actual_worker_persists_recovery_once(
    transport: dict[str, Any], worker: dict[str, Any]
) -> None:
    enqueue(transport)
    item = transport["item"]
    assert publish_one(
        transport["engine"], transport["identity"], item.event_id, celery_sender(worker["client"])
    )
    first = worker["client"].AsyncResult(str(item.event_id))
    worker["results"].append(first)
    assert first.get(timeout=20)["processed"] is True
    second = worker["client"].send_task(
        "yuliang.system.schedule_recovery", kwargs={"payload": item.model_dump(mode="json")}
    )
    worker["results"].append(second)
    assert second.get(timeout=20)["processed"] is False
    with business_transaction(transport["engine"], transport["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM scheduled_tasks WHERE source_event_id=:id"),
                {"id": item.event_id},
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize(
    "phase",
    [
        "before_publish",
        "before_confirm",
        "after_confirm",
        "before_processing",
        "during_processing",
        "before_ack",
        "after_ack",
    ],
)
def test_m0_16c_real_process_crash_points(transport: dict[str, Any], phase: str) -> None:
    enqueue(transport)
    item = transport["item"]
    if phase not in ("before_publish", "before_confirm", "after_confirm"):
        assert publish_one(
            transport["engine"], transport["identity"], item.event_id, transport["sender"]
        )
    settings = transport["settings"]
    env = os.environ.copy()
    env.update(
        YULIANG_DATABASE_URL=settings.database_url.get_secret_value(),
        YULIANG_AUTH_DATABASE_URL=settings.auth_database_url.get_secret_value(),
        YULIANG_REDIS_URL=settings.redis_url.get_secret_value(),
        YULIANG_BROKER_URL=settings.broker_url.get_secret_value(),
        YULIANG_SESSION_SECRET=settings.session_secret.get_secret_value(),
        YULIANG_WORKER_QUEUE_PREFIX=transport["prefix"],
        YULIANG_FAULT_PHASE=phase,
        YULIANG_FAULT_MESSAGE=item.model_dump_json(),
        YULIANG_FAULT_SHOP=str(transport["data"]["shop_a"]),
    )
    crashed = subprocess.run(  # noqa: S603 -- 仅固定本地故障夹具进程，凭据不进参数。
        [sys.executable, str(ROOT / "tests/integration/messaging_fault_process.py")],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=False,
        text=True,
    )
    assert crashed.returncode in range(71, 77), "故障进程未到指定真实崩溃点"
    proof = json.loads(crashed.stdout.splitlines()[0])
    assert proof["phase"] == phase and proof["eventId"] == str(item.event_id)
    proof["exitCode"] = crashed.returncode
    with psycopg.connect(transport["database"]["admin"]) as db:
        row = db.execute(
            "SELECT state,lease_owner FROM outbox_events WHERE event_id=%s", [item.event_id]
        ).fetchone()
        assert row
        proof["afterCrash"] = {"outboxState": row[0], "leaseOwner": str(row[1]) if row[1] else None}
        if phase in ("before_publish", "before_confirm", "after_confirm"):
            assert row[0] == "PENDING" and row[1] is not None
            db.execute(
                "UPDATE outbox_events SET lease_until=now()-interval '1 second' WHERE event_id=%s",
                [item.event_id],
            )
    if phase in ("before_publish", "before_confirm", "after_confirm"):
        assert publish_one(
            transport["engine"], transport["identity"], item.event_id, transport["sender"]
        )
    if phase != "after_ack":
        receive(transport)
    # 确认窗口可能已经送达一份：剩余重复消息必须仍使用同一event_id且仅ACK。
    with transport["client"].connection_for_read() as connection:
        queue = Queue(transport["prefix"] + ".system")(connection)
        while (delivery := queue.get(no_ack=False)) is not None:
            assert delivery.headers["id"] == str(item.event_id)
            assert not consume(
                transport["engine"],
                transport["identity"],
                "system.recovery",
                item.event_id,
                lambda _: pytest.fail("重复副作用"),
            )
            delivery.ack()
    assert_once(transport)
    with psycopg.connect(transport["database"]["admin"]) as db:
        processed = db.execute(
            "SELECT processed_at FROM consumer_inbox "
            "WHERE consumer='system.recovery' AND event_id=%s",
            [item.event_id],
        ).fetchone()
        assert processed and processed[0] is not None
        proof["processedAt"] = processed[0].isoformat()
    proof["internalEffectCount"] = 1
    archive = ROOT.parent / ".runtime/messaging-faults.jsonl"
    archive.parent.mkdir(exist_ok=True)
    with archive.open("a", encoding="utf-8") as output:
        output.write(json.dumps(proof) + "\n")


def test_m0_16a_mq_disconnect_preserves_committed_event(transport: dict[str, Any]) -> None:
    from urllib.parse import urlsplit, urlunsplit

    from pydantic import SecretStr

    enqueue(transport)
    parsed = urlsplit(transport["settings"].broker_url.get_secret_value())
    netloc = parsed.netloc.rsplit(":", 1)[0] + ":1"
    unavailable = create_celery(
        transport["settings"].model_copy(
            update={
                "broker_url": SecretStr(urlunsplit((parsed.scheme, netloc, parsed.path, "", "")))
            }
        )
    )
    try:
        assert not publish_one(
            transport["engine"],
            transport["identity"],
            transport["item"].event_id,
            celery_sender(unavailable),
        )
    finally:
        unavailable.close()
    assert publish_one(
        transport["engine"], transport["identity"], transport["item"].event_id, transport["sender"]
    )
    receive(transport)
    assert_once(transport)


def test_m0_16b_persistent_scan_recovers_received_without_replaying_done(
    transport: dict[str, Any], worker: dict[str, Any]
) -> None:
    enqueue(transport)
    item = transport["item"]
    assert publish_one(
        transport["engine"], transport["identity"], item.event_id, transport["sender"]
    )
    with transport["client"].connection_for_read() as connection:
        delivery = Queue(transport["prefix"] + ".system")(connection).get(no_ack=False)
        assert delivery is not None

        def interrupt(session: Session) -> None:
            raise RuntimeError("consumer interrupted")

        with pytest.raises(RuntimeError):
            consume(
                transport["engine"],
                transport["identity"],
                "system.recovery",
                item.event_id,
                interrupt,
            )
        # 故障夹具模拟消息被拒绝丢弃，PG的RECEIVED事实必须仍能恢复。
        delivery.reject(requeue=False)
    with psycopg.connect(transport["database"]["admin"]) as db:
        db.execute(
            "UPDATE outbox_events SET published_at=now()-interval '2 minutes' WHERE event_id=%s",
            [item.event_id],
        )
    assert (
        dispatch_pending(
            transport["engine"], transport["identity"], celery_sender(worker["client"])
        )
        == 1
    )
    result = worker["client"].AsyncResult(str(item.event_id))
    worker["results"].append(result)
    assert result.get(timeout=20)["processed"] is True
    with psycopg.connect(transport["database"]["admin"]) as db:
        db.execute(
            "UPDATE outbox_events SET published_at=now()-interval '2 minutes' WHERE event_id=%s",
            [item.event_id],
        )
    assert (
        dispatch_pending(
            transport["engine"], transport["identity"], celery_sender(worker["client"])
        )
        == 0
    )
    # 历史已完成事件不能占满扫描页，阻止后续PENDING事件被派发。
    next_item = item.model_copy(update={"event_id": uuid4(), "job_id": uuid4()})
    with business_transaction(transport["engine"], transport["identity"]) as session:
        emit(session, next_item, "yuliang.system.schedule_recovery")
    assert (
        dispatch_pending(
            transport["engine"], transport["identity"], celery_sender(worker["client"]), limit=1
        )
        == 1
    )
    next_result = worker["client"].AsyncResult(str(next_item.event_id))
    worker["results"].append(next_result)
    assert next_result.get(timeout=20)["processed"] is True
