"""仅测试调用的真实故障进程；不在应用中加入崩溃钩子。"""

import json
import os
import time

from kombu import Connection, Queue  # type: ignore[import-untyped]
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from yuliang.infrastructure.outbox import celery_sender, consume, publish_one
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.messages import WorkMessage
from yuliang.settings import Settings
from yuliang.worker.app import create_celery

settings = Settings()
item = WorkMessage.model_validate_json(os.environ["YULIANG_FAULT_MESSAGE"])
identity = VerifiedIdentity(item.tenant_id, item.membership_id, item.user_id)
engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
phase = os.environ["YULIANG_FAULT_PHASE"]
print(json.dumps({"pid": os.getpid(), "eventId": str(item.event_id), "phase": phase}), flush=True)
if phase in ("before_publish", "before_confirm", "after_confirm"):
    app = create_celery(settings)
    if phase == "before_confirm":
        app.conf.broker_transport_options = {"confirm_publish": False}
    sender = celery_sender(app)

    def crash_sender(*args: object) -> None:
        if phase == "before_publish":
            os._exit(71)
        sender(item.event_id, "yuliang.system.schedule_recovery", item.model_dump(mode="json"))
        os._exit(72)

    publish_one(engine, identity, item.event_id, crash_sender)
else:
    connection = Connection(settings.broker_url.get_secret_value())
    queue = Queue(settings.worker_queue_prefix + ".system")(connection)
    delivery = None
    deadline = time.monotonic() + 5
    while delivery is None and time.monotonic() < deadline:
        delivery = queue.get(no_ack=False)
        if delivery is None:
            time.sleep(0.05)
    if delivery is None:
        raise RuntimeError("故障进程未拿到实际消息")

    def operation(session: Session) -> None:
        if phase == "before_processing":
            os._exit(73)
        session.execute(
            text("UPDATE shops SET name=name||:marker WHERE id=:id"),
            {"marker": "|" + str(item.event_id)[:8], "id": os.environ["YULIANG_FAULT_SHOP"]},
        )
        if phase == "during_processing":
            os._exit(74)

    consume(engine, identity, "system.recovery", item.event_id, operation)
    if phase == "before_ack":
        os._exit(75)
    delivery.ack()
    connection.close()
    os._exit(76)
