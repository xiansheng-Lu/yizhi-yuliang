"""通知模块 M2-05b：enqueue / send_one / send_batch 服务函数。

渠道可用性判定（channel_available）
----------------------------------
M2-05b 脚手架只实现 IN_APP 的 SKIPPED 行为（站内通知后续 M2-20 接读模型消费），
其它渠道一律 raise NotImplementedError。这不是 silent skip——必须让 worker 感知
渠道未就绪，outbox 恢复协议才能决定重试次数或标记 UNKNOWN。

enqueue 幂等策略
----------------
NOTIFICATIONS 表上有 (tenant_id, idempotency_key) 唯一约束。
先 SELECT 再 INSERT，避免并发下 ON CONFLICT DO NOTHING 返回 None 而丢失已有记录快照。

send_one 分发
-------------
按 Channel 枚举分派：
  IN_APP  → 直接标记 SKIPPED（未来 M2-20 写站内消息表）
  EMAIL   → raise NotImplementedError("SMTP 账号未配置")
  SMS     → raise NotImplementedError("短信网关未配置")
  PUSH    → raise NotImplementedError("推送通道未配置")
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime
from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.notify.notification_models import (
    Channel,
    Notification,
    NotificationInput,
    NotificationStatus,
    SendResult,
)


class NotificationServiceError(Exception):
    """通知模块异常基类，上层 worker 可以 catch 后做 outbox 恢复。"""


class ChannelNotReadyError(NotificationServiceError):
    """渠道尚未配置（SMTP 账号、短信网关、推送凭据缺失）。"""


def channel_available(channel: Channel) -> bool:
    """返回指定渠道在当前进程中是否具备发送能力。

    M2-05b 脚手架：只有 IN_APP 被视为"可用"（但 send_one 仍 SKIPPED，
    因为真正的站内写库在 M2-20 接），其它一律 False。
    后续 SMTPMailer 初始化成功后，EMAIL 应返回 True。
    """
    return channel == Channel.IN_APP


def enqueue(
    session: Session,
    *,
    input: NotificationInput,
    now: AwareDatetime,
) -> Notification:
    """将一条通知入队，保证 (tenant_id, idempotency_key) 幂等。

    Args:
        session:  调用方业务事务的 Session。enqueue 不自己开事务，
                  跟随调用方的事务边界——业务写 + 通知写在同一个事务里原子提交。
        input:    业务方提供的通知输入。
        now:      当前时间戳（依赖注入，便于测试冻结时间）。

    Returns:
        已存在或新插入的 Notification 快照（status=PENDING）。

    Raises:
        NotificationServiceError: 数据库约束或反序列化异常。
    """
    existing = session.execute(
        text(
            "SELECT id, tenant_id, recipient, channel, template, payload, "
            "       status, idempotency_key, created_at, sent_at, last_error "
            "FROM notifications "
            "WHERE tenant_id=:tenant AND idempotency_key=:key "
            "LIMIT 1"
        ),
        {"tenant": str(input.tenant_id), "key": input.idempotency_key},
    ).mappings().first()
    if existing is not None:
        return _row_to_notification(existing)

    # id 在 SQL 层生成（gen_random_uuid()），不从 Python 侧传。
    result = session.execute(
        text(
            "INSERT INTO notifications "
            "(id, tenant_id, recipient, channel, template, payload, "
            " status, idempotency_key, created_at) "
            "VALUES (gen_random_uuid(), :tenant, :recipient, :channel, :template, "
            "        CAST(:payload AS jsonb), 'PENDING', :key, :created) "
            "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING "
            "RETURNING id, tenant_id, recipient, channel, template, payload, "
            "          status, idempotency_key, created_at, sent_at, last_error"
        ),
        {
            "tenant": str(input.tenant_id),
            "recipient": input.recipient,
            "channel": input.channel.value,
            "template": input.template,
            "payload": _dump_payload(input.payload),
            "key": input.idempotency_key,
            "created": now.isoformat(),
        },
    )
    row = result.mappings().first()
    if row is None:
        # ON CONFLICT DO NOTHING 触发——幂等命中，回退到第一次的查询结果
        existing_after = session.execute(
            text(
                "SELECT id, tenant_id, recipient, channel, template, payload, "
                "       status, idempotency_key, created_at, sent_at, last_error "
                "FROM notifications "
                "WHERE tenant_id=:tenant AND idempotency_key=:key"
            ),
            {"tenant": str(input.tenant_id), "key": input.idempotency_key},
        ).mappings().one()
        return _row_to_notification(existing_after)
    return _row_to_notification(row)


def send_one(session: Session, *, notification_id: UUID, now: AwareDatetime) -> SendResult:
    """投递一条待发送通知。

    分发规则（M2-05b 脚手架）：
      IN_APP  → 标记 SKIPPED（站内写库逻辑 M2-20 接）
      EMAIL   → raise NotImplementedError("SMTP 账号未配置")
      SMS     → raise NotImplementedError("短信网关未配置")
      PUSH    → raise NotImplementedError("推送通道未配置")

    注意：raise 不是 silent skip，worker 必须感知，outbox 恢复协议才能决定
    重试次数或标记 UNKNOWN。
    """
    row = session.execute(
        text(
            "SELECT id, tenant_id, recipient, channel, template, payload, "
            "       status, idempotency_key, created_at, sent_at, last_error "
            "FROM notifications WHERE id=:id"
        ),
        {"id": str(notification_id)},
    ).mappings().first()
    if row is None:
        raise NotificationServiceError(f"通知 {notification_id} 不存在")

    notification = _row_to_notification(row)
    if notification.status != NotificationStatus.PENDING:
        # 非 PENDING 的记录直接原样返回，幂等。
        return SendResult(
            notification_id=notification.id,
            status=notification.status,
            sent_at=notification.sent_at,
            error=notification.last_error,
        )

    if notification.channel == Channel.IN_APP:
        status = NotificationStatus.SKIPPED
        error = None
        sent_at: AwareDatetime | None = now
    elif notification.channel == Channel.EMAIL:
        raise NotImplementedError("SMTP 账号未配置")
    elif notification.channel == Channel.SMS:
        raise NotImplementedError("短信网关未配置")
    elif notification.channel == Channel.PUSH:
        raise NotImplementedError("推送通道未配置")
    else:
        raise NotificationServiceError(f"未知渠道 {notification.channel}")

    session.execute(
        text(
            "UPDATE notifications "
            "SET status=:status, sent_at=:sent, last_error=:error "
            "WHERE id=:id AND status='PENDING'"
        ),
        {
            "status": status.value,
            "sent": sent_at.isoformat() if sent_at else None,
            "error": error,
            "id": str(notification.id),
        },
    )
    return SendResult(
        notification_id=notification.id,
        status=status,
        sent_at=sent_at,
        error=error,
    )


def send_batch(
    *,
    ids: list[UUID],
    session_factory: Callable[[], Session],
    now_provider: Callable[[], AwareDatetime],
    limit: int = 100,
) -> list[SendResult]:
    """批量投递，逐个 try/except，单条失败不影响后续。

    Args:
        ids:             待投递通知 id 列表。
        session_factory: 每次迭代开新 Session（项目 worker 模式通常是这样）。
        now_provider:    获取当前时间戳的 callable（便于测试冻结时间）。
        limit:           单次批大小上限（防御性）。

    Returns:
        每条通知对应的 SendResult；失败条目的 error 字段携带异常描述。
    """
    if not 1 <= limit <= 1000:
        raise NotificationServiceError("批量大小必须在 1 到 1000 之间")

    bounded = ids[:limit]
    results: list[SendResult] = []
    for nid in bounded:
        with session_factory() as session:
            try:
                results.append(send_one(session, notification_id=nid, now=now_provider()))
            except NotImplementedError as exc:
                # SMTP/SMS/PUSH 未配置 → FAILED（不是 SKIPPED，SKIPPED 是策略性跳过）
                session.execute(
                    text(
                        "UPDATE notifications "
                        "SET status='FAILED', last_error=:err "
                        "WHERE id=:id AND status='PENDING'"
                    ),
                    {"err": str(exc), "id": str(nid)},
                )
                results.append(
                    SendResult(
                        notification_id=nid,
                        status=NotificationStatus.FAILED,
                        sent_at=None,
                        error=str(exc),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - 顶层兜底，别让单条炸掉整批
                session.execute(
                    text(
                        "UPDATE notifications "
                        "SET status='FAILED', last_error=:err "
                        "WHERE id=:id AND status='PENDING'"
                    ),
                    {"err": type(exc).__name__, "id": str(nid)},
                )
                results.append(
                    SendResult(
                        notification_id=nid,
                        status=NotificationStatus.FAILED,
                        sent_at=None,
                        error=type(exc).__name__,
                    )
                )
    return results


def _row_to_notification(row: Any) -> Notification:
    """把数据库 mapping 行反序列化为 Notification 模型。"""
    payload = row["payload"] if isinstance(row["payload"], dict) else dict(row["payload"] or {})
    return Notification(
        id=row["id"],
        tenant_id=row["tenant_id"],
        recipient=row["recipient"],
        channel=Channel(row["channel"]),
        template=row["template"],
        payload=payload,
        status=NotificationStatus(row["status"]),
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        sent_at=row["sent_at"],
        last_error=row["last_error"],
    )


def _dump_payload(payload: dict[str, Any]) -> str:
    """把 dict 序列化成 jsonb 友好的字符串。"""
    import json

    return json.dumps(payload, ensure_ascii=False)
