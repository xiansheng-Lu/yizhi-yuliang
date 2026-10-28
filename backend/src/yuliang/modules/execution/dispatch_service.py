"""批准事件派发：内部领取提交后发送，重投只恢复原尝试。"""

import asyncio
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from yuliang.infrastructure.outbox import consume
from yuliang.modules.command.applications import JobApplications
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.execution.approval_outbox import load_approved_event, load_approved_origin
from yuliang.modules.execution.attempt_models import BusinessKind, BusinessRef
from yuliang.modules.execution.attempts import ExecutionOrders, PreparedExecution
from yuliang.modules.execution.dispatch_runtime import DispatchExecutor, DispatchRuntime
from yuliang.modules.execution.guards import TargetGuards, controlled
from yuliang.modules.execution.receipts import ExecutionReceipts
from yuliang.modules.execution.recovery import ExecutionRecovery
from yuliang.modules.execution.recovery_schedule import RecoverySender, publish_schedule
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode
from yuliang.platform.messages import WorkMessage


class DispatchMessageRejected(ValueError):
    """坏信封或无真实批准来源；不可重投，且无发送权。"""


class RecoveryHandoffPending(RuntimeError):
    """恢复消息仍待broker确认，原批准任务不能ACK；重投不再WRITE。"""


class ApprovedExecutionDispatch:
    def __init__(
        self, engine: Engine, audit_key: SecretStr, runtime: DispatchRuntime | None = None
    ):
        self.engine, self.audit_key = engine, audit_key
        self.runtime = runtime or DispatchRuntime()

    @staticmethod
    def _identity(item: WorkMessage) -> VerifiedIdentity:
        return VerifiedIdentity(item.tenant_id, item.membership_id, item.user_id)

    def _applications(self, item: WorkMessage) -> JobApplications:
        return JobApplications(self.engine, self._identity(item), item.event_id, self.audit_key)

    @staticmethod
    def _record(
        session: Session,
        item: WorkMessage,
        state: str,
        attempt: UUID | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return dict(
            controlled(
                lambda: session.execute(
                    text("""SELECT access.approved_dispatch_record(
          :member,:user,:event,:job,:state,:attempt,:reason)"""),
                    dict(
                        member=item.membership_id,
                        user=item.user_id,
                        event=item.event_id,
                        job=item.job_id,
                        state=state,
                        attempt=attempt,
                        reason=reason,
                    ),
                ).scalar_one()
            )
        )

    def status(self, item: WorkMessage) -> dict[str, Any]:
        with business_transaction(self.engine, self._identity(item)) as session:
            load_approved_origin(session, item)
            value = session.execute(
                text("SELECT to_jsonb(d) FROM approved_execution_dispatches d WHERE event_id=:id"),
                dict(id=item.event_id),
            ).scalar_one_or_none()
            if value is None:
                raise ApiError(ErrorCode.NO_DATA, "批准事件尚无派发准入事实")
            return dict(value)

    def handle(self, item: WorkMessage) -> dict[str, Any]:
        try:
            value = self._handle(item)
        except ApiError:
            value = self._blocked(item)
        safe = {
            key: value.get(key) for key in ("event_id", "job_id", "state", "attempt_id", "reason")
        }
        if "recovery" in value:
            safe["recovery"] = {
                key: value["recovery"].get(key)
                for key in ("recovery_id", "attempt_id", "state", "next_event_id", "next_due")
            }
        return safe

    def _blocked(self, item: WorkMessage) -> dict[str, Any]:
        try:
            with business_transaction(self.engine, self._identity(item)) as session:
                return self._record(session, item, "BLOCKED_AUTH", reason="CURRENT_AUTH_OR_STATE")
        except ApiError:
            raise DispatchMessageRejected("消息与真实原批准来源不一致") from None

    def _handle(self, item: WorkMessage) -> dict[str, Any]:
        with business_transaction(self.engine, self._identity(item)) as session:
            origin = load_approved_origin(session, item)
        if not origin.subject_active:
            return self._blocked(item)
        applications = self._applications(item)
        holder: dict[str, Any] = {}

        def admit(session: Session) -> None:
            event = load_approved_event(session, item)
            previous = session.execute(
                text("SELECT to_jsonb(d) FROM approved_execution_dispatches d WHERE event_id=:id"),
                dict(id=item.event_id),
            ).scalar_one_or_none()
            if previous is not None:
                holder["status"] = dict(previous)
                return
            orders = ExecutionOrders(applications)
            business = BusinessRef(kind=BusinessKind.COMMAND_JOB, id=event.job_id)
            # 同一个job已SENT但事件消费回执丢失时，不重新生成发送凭证。
            existing = orders._load(session, business)
            if existing is not None:
                holder["status"] = self._record(
                    session, item, "RECOVERY_REQUIRED", existing["attempt_id"], "RECOVERY_ONLY"
                )
                return
            orders._scope(session, business)
            current = (
                session.execute(
                    text("""SELECT status,version,expires_at>clock_timestamp() AS valid
              FROM command_jobs WHERE id=:id"""),
                    dict(id=event.job_id),
                )
                .mappings()
                .one()
            )
            if (current["status"], current["version"], current["valid"]) != (
                "APPROVED",
                event.approved_job_version,
                True,
            ):
                raise ApiError(ErrorCode.VERSION_CONFLICT, "原批准版本不再可领取")
            executor = self.runtime.resolve(session, event)
            if executor is None:
                holder["status"] = self._record(
                    session, item, "BLOCKED_EXECUTOR", reason="EXECUTOR_UNAVAILABLE"
                )
                return
            guard = TargetGuards(applications).describe(session, event.job_id)
            orders = ExecutionOrders(applications, executor.serializer)
            prepared = orders.prepare(
                business,
                executor.ctx,
                event.approved_job_version,
                int(guard["fence"]),
                uuid4(),
                unit_of_work=session,
            )
            holder["status"] = self._record(session, item, "ADMITTED", prepared.attempt.attempt_id)
            holder["prepared"], holder["executor"], holder["orders"] = prepared, executor, orders

        consume(self.engine, self._identity(item), "approved.execution", item.event_id, admit)
        status = holder.get("status")
        if status is None:
            status = self.status(item)
        if status["state"].startswith("BLOCKED_") or status["state"] == "RECEIPT_RECORDED":
            return dict(status)
        prepared = holder.get("prepared")
        if prepared is not None and prepared.attempt.send_allowed:
            return self._send(item, holder["orders"], prepared, holder["executor"])
        return self._recover(item, status)

    def _recover(self, item: WorkMessage, status: dict[str, Any]) -> dict[str, Any]:
        # 0036已在prepare同事务创建恢复调度；这里实际检查而非伪造新请求。
        recovery = ExecutionRecovery(self._applications(item)).for_job(item.job_id)
        with business_transaction(self.engine, self._identity(item)) as session:
            result = self._record(
                session, item, "RECOVERY_REQUIRED", UUID(str(status["attempt_id"])), "RECOVERY_ONLY"
            )
        return result | dict(recovery=recovery.model_dump(mode="json"))

    def _recovery_target(self, item: WorkMessage) -> dict[str, Any]:
        with business_transaction(self.engine, self._identity(item)) as session:
            origin = load_approved_origin(session, item)
            dispatch = (
                session.execute(
                    text("""SELECT attempt_id FROM approved_execution_dispatches
              WHERE event_id=:event AND job_id=:job"""),
                    dict(event=item.event_id, job=item.job_id),
                )
                .mappings()
                .first()
            )
            if dispatch is None:
                raise RecoveryHandoffPending("批准事件缺持久派发事实")
            if dispatch["attempt_id"] is None:
                return dict(attempt_id=None, subject_active=origin.subject_active)
            row = (
                session.execute(
                    text("""SELECT a.id AS attempt_id,r.id AS recovery_id,
              r.state AS recovery_state,r.current_event_id AS recovery_event_id,
              e.state AS publish_state
              FROM execution_attempts a JOIN execution_orders o
              ON (o.tenant_id,o.id)=(a.tenant_id,a.order_id)
              JOIN execution_recoveries r ON (r.tenant_id,r.attempt_id,r.order_id,r.job_id)
                =(a.tenant_id,a.id,o.id,o.command_job_id)
              JOIN outbox_events e ON (e.tenant_id,e.event_id)=(r.tenant_id,r.current_event_id)
              WHERE a.id=:attempt AND o.command_job_id=:job AND o.created_by=:user
              AND o.membership_id=:member AND o.requested_version=:version
              AND r.actor_id=:user AND r.membership_id=:member
              AND e.event_type='yuliang.system.execution_recovery'"""),
                    dict(
                        attempt=dispatch["attempt_id"],
                        job=item.job_id,
                        user=item.user_id,
                        member=item.membership_id,
                        version=origin.approved_job_version,
                    ),
                )
                .mappings()
                .first()
            )
            if row is None:
                raise RecoveryHandoffPending("实际attempt缺匹配的持久恢复事件")
            return dict(row) | dict(subject_active=origin.subject_active)

    def publish_recovery(self, item: WorkMessage, sender: RecoverySender) -> dict[str, Any]:
        """批准task ACK前的定向handoff；仅发布该真实attempt，不扫描其他job。"""
        for _ in range(3):
            target = self._recovery_target(item)
            safe = dict(
                event_id=str(item.event_id),
                job_id=str(item.job_id),
                attempt_id=str(target["attempt_id"]) if target["attempt_id"] else None,
                recovery_id=str(target["recovery_id"]) if target.get("recovery_id") else None,
                recovery_event_id=str(target["recovery_event_id"])
                if target.get("recovery_event_id")
                else None,
            )
            if target["attempt_id"] is None:
                return safe | dict(state="NO_ATTEMPT")
            if target["recovery_state"] not in {"DUE", "WAITING"}:
                return safe | dict(state="TERMINAL", recovery_state=target["recovery_state"])
            recovery_event = target["recovery_event_id"]
            try:
                if not target["subject_active"]:
                    raise ApiError(ErrorCode.FORBIDDEN, "原申请者已失活，仅允许恢复阻断事实")
                # publish_schedule内部先短事务claim，退出事务后MQ发布并确认。
                publish_schedule(self.engine, self._identity(item), recovery_event, sender)
            except ApiError as error:
                reason: Literal["AUTH_REVOKED", "INTEGRITY_REQUIRED"] = (
                    "AUTH_REVOKED" if error.code == ErrorCode.FORBIDDEN else "INTEGRITY_REQUIRED"
                )
                blocked = ExecutionRecovery(self._applications(item)).blocked(
                    recovery_event, reason
                )
                return safe | dict(state="TERMINAL" if blocked.state == "DONE" else blocked.state)
            current = self._recovery_target(item)
            if current["recovery_state"] not in {"DUE", "WAITING"}:
                continue
            if current["recovery_event_id"] != recovery_event:
                continue
            if current["publish_state"] == "PUBLISHED":
                return safe | dict(state="PUBLISHED")
            raise RecoveryHandoffPending("恢复消息尚未确认，原批准事件保留重投")
        raise RecoveryHandoffPending("恢复代际并发变化，按原批准事件重试")

    def _send(
        self,
        item: WorkMessage,
        orders: ExecutionOrders,
        prepared: PreparedExecution,
        executor: DispatchExecutor,
    ) -> dict[str, Any]:
        ticket = prepared.attempt
        if prepared.spec is None or prepared.parameters is None:
            raise RuntimeError("仅首次已提交准备结果可持有发送资格")
        spec, parameters = prepared.spec, prepared.parameters

        async def send() -> Any:
            options: dict[str, Any] = dict(
                target=executor.target,
                execution_id=ticket.execution_id,
                attempt_id=ticket.attempt_id,
                before_verify=orders.before_verify(ticket),
                after_admission=orders.after_admission(ticket, executor.ctx, spec, parameters),
            )
            if isinstance(executor.client, GovernedPlatformClient):
                options["lane"] = "EXECUTE"
            return await executor.client.send(
                executor.ctx,
                spec,
                ticket.outbound_id,
                parameters,
                executor.credential(),
                **options,
            )

        # 此处consume和SENT/guard事务均已提交，无数据库事务跨越外部HTTP。
        try:
            asyncio.run(send())
        except ApiError:
            return self._blocked(item)
        with business_transaction(self.engine, self._identity(item)) as session:
            raw = session.execute(
                text("""SELECT id FROM platform_raw_payloads
              WHERE outbound_id=:outbound AND phase<>'ATTEMPT' ORDER BY id DESC LIMIT 1"""),
                dict(outbound=ticket.outbound_id),
            ).scalar_one_or_none()
            if raw is None:
                return self._record(
                    session, item, "RECOVERY_REQUIRED", ticket.attempt_id, "RECOVERY_ONLY"
                )
            ExecutionReceipts(self._applications(item)).record(
                ticket.attempt_id, raw, ticket.execution_version, unit_of_work=session
            )
            return self._record(session, item, "RECEIPT_RECORDED", ticket.attempt_id, "RAW_RECEIPT")
