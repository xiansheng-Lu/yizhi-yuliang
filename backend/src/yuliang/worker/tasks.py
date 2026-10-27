"""任务只接收必要ID；没有平台/AI执行器时明确失败，不假报业务成功。"""

import os
import time
from typing import Any

from celery.exceptions import Reject  # type: ignore[import-untyped]
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.messages import parse_message as message
from yuliang.worker.app import app

_engine: Engine | None = None
_pid: int | None = None


def engine() -> Engine:
    global _engine, _pid
    current = os.getpid()
    if _pid != current or _engine is None:
        if _engine is not None:
            _engine.dispose(close=False)
        _engine = create_engine(
            app.conf.yuliang_settings.database_url.get_secret_value(),
            hide_parameters=True,
            pool_size=1,
            max_overflow=0,
        )
        _pid = current
    return _engine


@app.task(
    bind=True,
    name="yuliang.system.identity_probe",
    reject_on_worker_lost=True,
    autoretry_for=(),
    max_retries=0,
)  # type: ignore[untyped-decorator]
def identity_probe(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
    item = message(payload)
    identity = VerifiedIdentity(item.tenant_id, item.membership_id, item.user_id)
    with business_transaction(engine(), identity) as session:
        valid = session.execute(
            text("""
          SELECT m.id FROM memberships m JOIN tenants t ON t.id=m.tenant_id
          WHERE m.id=:member AND m.user_id=:user AND m.status='ACTIVE'
            AND t.status='ACTIVE' AND t.deleted_at IS NULL
        """),
            {"member": item.membership_id, "user": item.user_id},
        ).scalar_one_or_none()
        if not valid:
            raise ValueError("任务身份已失效")
        tenant = session.execute(text("SELECT public.current_tenant_id()")).scalar_one()
    return {
        "eventId": str(item.event_id),
        "tenantId": str(tenant),
        "workerPid": os.getpid(),
        "redelivered": bool((self.request.delivery_info or {}).get("redelivered")),
        "retries": self.request.retries,
    }


def unavailable(payload: dict[str, Any]) -> None:
    message(payload)
    raise RuntimeError("执行器尚未接入；未调用平台/供应商且不自动重试")


@app.task(
    name="yuliang.execute.guard", autoretry_for=(), max_retries=0, reject_on_worker_lost=False
)  # type: ignore[untyped-decorator]
def execute_guard(payload: dict[str, Any]) -> None:
    unavailable(payload)


@app.task(
    name="yuliang.platform.guard", autoretry_for=(), max_retries=0, reject_on_worker_lost=False
)  # type: ignore[untyped-decorator]
def platform_guard(payload: dict[str, Any]) -> None:
    unavailable(payload)


@app.task(
    name="yuliang.ai_media.guard", autoretry_for=(), max_retries=0, reject_on_worker_lost=False
)  # type: ignore[untyped-decorator]
def ai_media_guard(payload: dict[str, Any]) -> None:
    unavailable(payload)


@app.task(
    name="yuliang.system.schedule_recovery",
    reject_on_worker_lost=True,
    autoretry_for=(),
    max_retries=0,
)  # type: ignore[untyped-decorator]
def recover_internal_event(payload: dict[str, Any]) -> dict[str, Any]:
    from yuliang.infrastructure.outbox import consume, schedule_recovery

    item = message(payload)
    identity = VerifiedIdentity(item.tenant_id, item.membership_id, item.user_id)
    try:
        with business_transaction(engine(), identity) as session:
            stored = session.execute(
                text(
                    "SELECT payload FROM outbox_events WHERE event_id=:id "
                    "AND event_type='yuliang.system.schedule_recovery'"
                ),
                {"id": item.event_id},
            ).scalar_one_or_none()
    except DBAPIError:
        time.sleep(1)
        raise Reject("数据库事实未就绪，内部任务不ACK", requeue=True) from None
    if stored != item.model_dump(mode="json"):
        raise ValueError("消息与持久事件不一致")
    try:
        changed = consume(
            engine(),
            identity,
            "system.recovery",
            item.event_id,
            lambda session: schedule_recovery(session, item),
        )
    except Exception:
        # 仅此内部DB幂等任务可重投；平台/AI guard保持不自动重试。
        time.sleep(1)
        raise Reject("恢复事实未提交，内部任务不ACK", requeue=True) from None
    return {"eventId": str(item.event_id), "processed": changed, "state": "PROCESSED"}
