"""任务只接收必要ID；没有平台/AI执行器时明确失败，不假报业务成功。"""

import os
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Engine, create_engine, text

from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.worker.app import app

_engine: Engine | None = None
_pid: int | None = None


class WorkMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    event_id: UUID
    tenant_id: int = Field(strict=True, gt=0, le=9223372036854775807)
    job_id: UUID
    membership_id: UUID
    user_id: UUID


def message(value: dict[str, Any]) -> WorkMessage:
    try:
        return WorkMessage.model_validate(value)
    except ValidationError:
        raise ValueError("消息结构不合法，仅允许必要ID字段") from None


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
