"""业务事务写Outbox；数据库租约之外发布；Inbox与内部DB副作用原子提交。"""

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.messages import WorkMessage

Sender = Callable[[UUID, str, dict[str, Any]], None]


def emit(session: Session, item: WorkMessage, event_type: str) -> None:
    payload = item.model_dump(mode="json")
    inserted = session.execute(
        text("""
      INSERT INTO outbox_events(event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
      VALUES(:event,:tenant,'job',:job,:type,CAST(:payload AS jsonb))
      ON CONFLICT(event_id) DO NOTHING RETURNING event_id
    """),
        {
            "event": item.event_id,
            "tenant": item.tenant_id,
            "job": str(item.job_id),
            "type": event_type,
            "payload": json.dumps(payload),
        },
    ).scalar_one_or_none()
    if inserted is None:
        existing = session.execute(
            text("SELECT event_type,payload FROM outbox_events WHERE event_id=:id"),
            {"id": item.event_id},
        ).first()
        if existing is None or existing[0] != event_type or existing[1] != payload:
            raise ValueError("同事件ID不能替换内容或租户")


def publish_one(engine: Engine, identity: VerifiedIdentity, event_id: UUID, sender: Sender) -> bool:
    owner = uuid4()
    with business_transaction(engine, identity) as session:
        event = (
            session.execute(
                text("SELECT * FROM access.outbox_claim(:event,:owner)"),
                {"event": event_id, "owner": owner},
            )
            .mappings()
            .first()
        )
    if event is None:
        return False
    try:
        # 此时没有持有数据库事务或行锁，MQ确认失败也不回滚已提交业务。
        sender(event_id, event["event_type"], event["payload"])
    except Exception as error:
        with business_transaction(engine, identity) as session:
            session.execute(
                text("SELECT access.outbox_finish(:event,:owner,false)"),
                {"event": event_id, "owner": owner},
            )
        structlog.get_logger().warning(
            "outbox_publish_failed", event_id=str(event_id), exception_type=type(error).__name__
        )
        return False
    with business_transaction(engine, identity) as session:
        return bool(
            session.execute(
                text("SELECT access.outbox_finish(:event,:owner,true)"),
                {"event": event_id, "owner": owner},
            ).scalar_one()
        )


def consume(
    engine: Engine,
    identity: VerifiedIdentity,
    consumer: str,
    event_id: UUID,
    operation: Callable[[Session], None],
) -> bool:
    # RECEIVED先独立提交，进程随时退出仍有未完成事实；只允许PROCESSED直接跳过。
    with business_transaction(engine, identity) as session:
        processed = session.execute(
            text("SELECT access.inbox_receive(:consumer,:event)"),
            {"consumer": consumer, "event": event_id},
        ).scalar_one()
    if processed:
        return False
    with business_transaction(engine, identity) as session:
        processed = session.execute(
            text("SELECT access.inbox_lock(:consumer,:event)"),
            {"consumer": consumer, "event": event_id},
        ).scalar_one()
        if processed:
            return False
        # 回调只允许内部数据库用例。外部动作必须另建attempt并遵循UNKNOWN恢复协议。
        operation(session)
        session.execute(
            text("SELECT access.inbox_processed(:consumer,:event)"),
            {"consumer": consumer, "event": event_id},
        )
    return True


def schedule_recovery(session: Session, item: WorkMessage) -> None:
    session.execute(
        text("""
      INSERT INTO scheduled_tasks(tenant_id,kind,business_key,payload,due_at,source_event_id)
      VALUES(:tenant,'RECOVER',:key,CAST(:payload AS jsonb),now(),:event)
      ON CONFLICT(tenant_id,kind,business_key) DO NOTHING
    """),
        {
            "tenant": item.tenant_id,
            "key": str(item.event_id),
            "event": item.event_id,
            "payload": json.dumps(item.model_dump(mode="json")),
        },
    )


def celery_sender(app: Any) -> Sender:
    def send(event_id: UUID, name: str, payload: dict[str, Any]) -> None:
        app.send_task(
            name,
            task_id=str(event_id),
            kwargs={"payload": payload},
            kwargsrepr="<ids-only>",
            mandatory=True,
            retry=False,
        )

    return send


def dispatch_pending(
    engine: Engine, identity: VerifiedIdentity, sender: Sender, limit: int = 100
) -> int:
    if not 1 <= limit <= 100:
        raise ValueError("每批只能派发1至100条")
    with business_transaction(engine, identity) as session:
        rows = session.execute(
            text("SELECT * FROM access.outbox_candidates(:limit)"),
            {"limit": limit},
        ).all()
        for row in rows:
            if row.event_type == "yuliang.system.schedule_recovery":
                session.execute(
                    text("SELECT access.outbox_reopen_recovery(:event)"), {"event": row.event_id}
                )
    return sum(publish_one(engine, identity, row.event_id, sender) for row in rows)
