"""任务只接收必要ID；没有平台/AI执行器时明确失败，不假报业务成功。"""

import math
import os
import time
from datetime import UTC, datetime
from typing import Any, Literal

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


@app.task(
    name="yuliang.ai_media.relay_video_generate",
    reject_on_worker_lost=True,
    autoretry_for=(),
    max_retries=0,
)  # type: ignore[untyped-decorator]
def relay_video_generate(payload: dict[str, Any]) -> dict[str, Any]:
    """条件领取保证同一调用只执行一次；已发出的外部调用绝不自动重试。"""
    from yuliang.infrastructure.blob import BlobStore
    from yuliang.modules.content.relay_video_calls import PostgresVideoCallStore
    from yuliang.modules.content.relay_video_generation_worker import (
        execute_relay_video_call,
    )
    from yuliang.modules.content.video_generation_config import (
        load_video_transport_config,
    )
    from yuliang.platform.errors import ApiError

    item = message(payload)
    identity = VerifiedIdentity(item.tenant_id, item.membership_id, item.user_id)
    store = PostgresVideoCallStore(engine(), identity)
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
    try:
        config = load_video_transport_config()
    except ApiError:
        store.fail(
            item.job_id,
            expected="PENDING",
            error_code="PROVIDER_UNCONFIGURED",
            error_message="视频中转站连接配置缺失，未发起调用",
        )
        return {"callId": str(item.job_id), "state": "FAILED", "errorCode": "PROVIDER_UNCONFIGURED"}
    try:
        blob = BlobStore.from_settings(app.conf.yuliang_settings)
    except ValueError:
        blob = None
    if blob is None:
        store.fail(
            item.job_id,
            expected="PENDING",
            error_code="BLOB_UNCONFIGURED",
            error_message="私有对象存储未启用，视频产物无法落盘",
        )
        return {"callId": str(item.job_id), "state": "FAILED", "errorCode": "BLOB_UNCONFIGURED"}
    return execute_relay_video_call(store=store, blob=blob, call_id=item.job_id, config=config)


def unavailable(payload: dict[str, Any]) -> None:
    message(payload)
    raise RuntimeError("执行器尚未接入；未调用平台/供应商且不自动重试")


def approved_execution_runtime() -> Any:
    """生产显式装配入口；默认返回空 DispatchRuntime（持久 BLOCKED_EXECUTOR）。

    生产进程需在 worker 启动前调用 yuliang.modules.execution.write_adapter.register(...)
    按 (action, provider, product_line) 装配 ExecutorFactory。注册表为空时 resolve() 返回 None。
    消息不得覆盖或自动注入此工厂。
    """
    from yuliang.modules.execution.write_adapter import build_production_runtime

    return build_production_runtime()


@app.task(
    bind=True,
    name="yuliang.system.approved_execution",
    reject_on_worker_lost=True,
    autoretry_for=(),
    max_retries=0,
)  # type: ignore[untyped-decorator]
def approved_execution(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from yuliang.modules.execution.dispatch_service import (
        ApprovedExecutionDispatch,
        DispatchMessageRejected,
    )
    from yuliang.modules.execution.recovery_schedule import celery_recovery_sender

    try:
        item = message(payload)
    except ValueError:
        raise Reject("执行消息结构无效，拒绝非持久申请", requeue=False) from None
    settings = app.conf.yuliang_settings
    try:
        service = ApprovedExecutionDispatch(
            engine(),
            settings.auth_audit_secret or settings.session_secret,
            approved_execution_runtime(),
        )
        result = service.handle(item)
        handoff = service.publish_recovery(item, celery_recovery_sender(app))
    except DispatchMessageRejected:
        raise Reject("执行消息与真实批准来源不符", requeue=False) from None
    except Exception:
        time.sleep(1)
        raise Reject("执行事实未完成，保留原事件核对", requeue=True) from None
    return result | {
        "recoveryDelivery": handoff,
        "workerPid": os.getpid(),
        "redelivered": bool((self.request.delivery_info or {}).get("redelivered")),
    }


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
    name="yuliang.system.analysis_accept",
    reject_on_worker_lost=True,
    autoretry_for=(),
    max_retries=0,
)  # type: ignore[untyped-decorator]
def accept_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    """仅确认持久申请投递；尚未交付计算阶段时保持queued/0。"""
    from yuliang.infrastructure.outbox import consume

    item = message(payload)
    identity = VerifiedIdentity(item.tenant_id, item.membership_id, item.user_id)

    def receive(session: Any) -> None:
        from yuliang.modules.analysis.service import AnalysisService
        from yuliang.modules.reporting.coverage import require_coverage

        query = AnalysisService._task(session, item.job_id).query
        from yuliang.platform.errors import ApiError, ErrorCode

        try:
            original_scope = tuple(
                session.execute(
                    text("SELECT unnest(account_scope) FROM analysis_tasks WHERE id=:id"),
                    dict(id=item.job_id),
                ).scalars()
            )
            require_coverage(session, query, account_scope=original_scope)
        except ApiError as error:
            if error.code != ErrorCode.DATA_COVERAGE_INSUFFICIENT:
                raise
            session.execute(
                text("SELECT access.analysis_coverage_failed(:id)"), dict(id=item.job_id)
            )
        session.execute(text("SELECT access.analysis_receive(:id)"), {"id": item.event_id})

    try:
        with business_transaction(engine(), identity) as session:
            stored = session.execute(
                text(
                    "SELECT payload FROM outbox_events WHERE event_id=:id "
                    "AND event_type='yuliang.system.analysis_accept'"
                ),
                {"id": item.event_id},
            ).scalar_one_or_none()
        if stored != item.model_dump(mode="json"):
            raise ValueError("分析消息与原持久申请不一致")
        changed = consume(
            engine(),
            identity,
            "analysis.accept",
            item.event_id,
            receive,
        )
    except DBAPIError:
        raise Reject("分析投递事实未提交，保持内部任务重投", requeue=True) from None
    from yuliang.modules.analysis.repository import AnalysisLeaseBusy, execute_analysis

    try:
        computed = execute_analysis(engine(), identity, item.job_id)
    except (DBAPIError, AnalysisLeaseBusy):
        raise Reject("分析阶段未提交，按原任务恢复", requeue=True) from None
    return {
        "taskId": str(item.job_id),
        "eventId": str(item.event_id),
        "deliveryRecorded": changed,
        "computed": computed,
    }


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


@app.task(
    bind=True,
    name="yuliang.system.execution_recovery",
    reject_on_worker_lost=True,
    autoretry_for=(),
    max_retries=None,
)  # type: ignore[untyped-decorator]
def recover_execution(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """只做PG事实处理和内部MQ计时，绝不重发平台请求。"""
    from yuliang.infrastructure.outbox import consume
    from yuliang.modules.command.applications import JobApplications
    from yuliang.modules.execution.recovery import (
        ExecutionRecovery,
        RecoveryDeferred,
        RecoveryResult,
    )
    from yuliang.modules.execution.recovery_schedule import (
        celery_recovery_sender,
        publish_schedule,
    )
    from yuliang.platform.errors import ApiError, ErrorCode

    item = message(payload)
    identity = VerifiedIdentity(item.tenant_id, item.membership_id, item.user_id)
    settings = app.conf.yuliang_settings
    service = ExecutionRecovery(
        JobApplications(
            engine(), identity, item.event_id, settings.auth_audit_secret or settings.session_secret
        )
    )
    try:
        with business_transaction(engine(), identity) as session:
            stored = session.execute(
                text("""SELECT payload FROM outbox_events WHERE event_id=:id
            AND event_type='yuliang.system.execution_recovery'"""),
                {"id": item.event_id},
            ).scalar_one_or_none()
    except DBAPIError:
        time.sleep(1)
        raise Reject("原恢复事件尚不可核对，保持重投", requeue=True) from None
    if stored != item.model_dump(mode="json"):
        raise ValueError("恢复消息与原持久事件不符")
    holder: dict[str, RecoveryResult] = {}

    def operate(session: Any) -> None:
        holder["result"] = service.inspect(item.event_id, unit_of_work=session)

    try:
        changed = consume(engine(), identity, "execution.recovery", item.event_id, operate)
    except RecoveryDeferred as deferred:
        delay = max(1, math.ceil((deferred.until - datetime.now(UTC)).total_seconds()))
        raise self.retry(countdown=delay, max_retries=None) from None
    except ApiError as error:
        if error.code not in {
            ErrorCode.FORBIDDEN,
            ErrorCode.INVALID_INPUT,
            ErrorCode.IDEMPOTENCY_MISMATCH,
            ErrorCode.NO_DATA,
        }:
            time.sleep(1)
            raise Reject("恢复条件竞争，原事件未完成", requeue=True) from None
        reason: Literal["AUTH_REVOKED", "INTEGRITY_REQUIRED"] = (
            "AUTH_REVOKED" if error.code == ErrorCode.FORBIDDEN else "INTEGRITY_REQUIRED"
        )

        def blocked(session: Any) -> None:
            holder["result"] = service.blocked(item.event_id, reason, unit_of_work=session)

        try:
            changed = consume(engine(), identity, "execution.recovery", item.event_id, blocked)
        except Exception:
            time.sleep(1)
            raise Reject("阻塞事实尚未提交，原事件未完成", requeue=True) from None
    except Exception:
        time.sleep(1)
        raise Reject("恢复事务未完成，保持重投", requeue=True) from None
    if "result" not in holder:
        with business_transaction(engine(), identity) as session:
            value = session.execute(
                text("""SELECT r.result FROM execution_recovery_events d
            JOIN execution_recoveries r ON (r.tenant_id,r.id)=(d.tenant_id,d.recovery_id)
            WHERE d.event_id=:id"""),
                {"id": item.event_id},
            ).scalar_one()
        holder["result"] = RecoveryResult.model_validate(value)
    result = holder["result"]
    if result.state in {"DUE", "WAITING"}:
        next_event = None
        try:
            with business_transaction(engine(), identity) as session:
                current = session.execute(
                    text("""SELECT current_event_id,state FROM execution_recoveries
                    WHERE id=:id AND job_id=:job AND actor_id=:user
                    AND membership_id=:member"""),
                    dict(
                        id=result.recovery_id,
                        job=item.job_id,
                        user=item.user_id,
                        member=item.membership_id,
                    ),
                ).first()
            if current is None:
                raise ValueError("原恢复任务不可核对")
            if current.state in {"DUE", "WAITING"}:
                next_event = current.current_event_id
                if next_event is None:
                    raise ValueError("原恢复任务缺持久后继事件")
                publish_schedule(engine(), identity, next_event, celery_recovery_sender(app))
            with business_transaction(engine(), identity) as session:
                pending = session.execute(
                    text("""SELECT count(*) FROM execution_recoveries r
                JOIN outbox_events e ON (e.tenant_id,e.event_id)=(r.tenant_id,r.current_event_id)
                WHERE r.id=:id AND r.state IN ('DUE','WAITING') AND e.state='PENDING'"""),
                    {"id": result.recovery_id},
                ).scalar_one()
        except ApiError as error:
            if error.code != ErrorCode.FORBIDDEN or next_event is None:
                raise Reject("后继调度尚未确认", requeue=True) from None
            result = service.blocked(next_event, "AUTH_REVOKED")
            pending = 0
        except Exception:
            time.sleep(1)
            raise Reject("后继计时消息未确认，持久事件保留", requeue=True) from None
        if pending:
            time.sleep(1)
            raise Reject("后继计时消息仍待发布", requeue=True)
    return {
        "eventId": str(item.event_id),
        "processed": changed,
        "workerPid": os.getpid(),
        "redelivered": bool((self.request.delivery_info or {}).get("redelivered")),
        "recovery": result.model_dump(mode="json"),
    }
