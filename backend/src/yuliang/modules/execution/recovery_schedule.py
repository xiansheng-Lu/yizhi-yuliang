"""恢复计时消息在PG事务外发布并确认；持久Outbox仍是恢复来源。"""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import Engine, text

from yuliang.modules.execution.guards import controlled
from yuliang.platform.db import VerifiedIdentity, business_transaction

RecoverySender = Callable[[UUID, str, dict[str, Any], datetime], None]


def celery_recovery_sender(app: Any) -> RecoverySender:
    def send(event: UUID, name: str, payload: dict[str, Any], due: datetime) -> None:
        if name != "yuliang.system.execution_recovery":
            raise ValueError("计时发布器只发送受控DB恢复任务")
        app.send_task(
            name,
            task_id=str(event),
            kwargs={"payload": payload},
            kwargsrepr="<ids-only>",
            eta=due,
            mandatory=True,
            retry=False,
        )

    return send


def publish_schedule(
    engine: Engine, identity: VerifiedIdentity, event: UUID, sender: RecoverySender
) -> bool:
    owner = uuid4()
    with business_transaction(engine, identity) as session:
        item = controlled(
            lambda: session.execute(
                text("SELECT access.execution_recovery_publish_claim(:member,:user,:event,:owner)"),
                {
                    "member": identity.membership_id,
                    "user": identity.user_id,
                    "event": event,
                    "owner": owner,
                },
            ).scalar_one()
        )
    if item is None:
        return False
    try:
        # 已释放所有数据库事务，MQ确认不能提交/回滚财务事实。
        sender(event, item["event_type"], item["payload"], datetime.fromisoformat(item["due_at"]))
    except Exception as error:
        with business_transaction(engine, identity) as session:
            session.execute(
                text("SELECT access.outbox_finish(:event,:owner,false)"),
                {"event": event, "owner": owner},
            )
        structlog.get_logger().warning(
            "recovery_publish_failed", event_id=str(event), exception_type=type(error).__name__
        )
        return False
    with business_transaction(engine, identity) as session:
        return bool(
            session.execute(
                text("SELECT access.outbox_finish(:event,:owner,true)"),
                {"event": event, "owner": owner},
            ).scalar_one()
        )


def publish_schedules(
    engine: Engine, identity: VerifiedIdentity, sender: RecoverySender, limit: int = 100
) -> int:
    if not 1 <= limit <= 100:
        raise ValueError("每批1至100项")
    with business_transaction(engine, identity) as session:
        events = (
            session.execute(
                text("""SELECT r.current_event_id FROM execution_recoveries r
        JOIN outbox_events e ON (e.tenant_id,e.event_id)=(r.tenant_id,r.current_event_id)
        WHERE r.state IN ('DUE','WAITING') AND e.state='PENDING'
        ORDER BY r.next_due,r.id LIMIT :limit"""),
                {"limit": limit},
            )
            .scalars()
            .all()
        )
    count = 0
    for event in events:
        if publish_schedule(engine, identity, event, sender):
            count += 1
        else:
            break
    return count
