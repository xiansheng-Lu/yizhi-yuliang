"""通知模块 M2-05b：通用通知骨架，不含真实 SMTP 账号和对外发信。

设计注释（本文件承载脚手架意图，不另起 .md）：

整体边界
--------
本模块是**纯基础设施**层，不耦合 M2-15/M2-18b/M2-20 任何业务逻辑。
业务模块（批准、执行恢复、风控告警等）只应通过 enqueue(...) 写入一条待发送通知，
由 worker/定时任务统一触发 send_batch 投递，从而复用 outbox 的可靠消息语义。

数据模型（notification_models.py）
----------------------------------
Notification 记录一次"尝试触达某人"的动作快照，核心字段：

  id                UUID 主键
  tenant_id         租户隔离（驭量多租户前提）
  recipient         目标地址（EMAIL 是邮箱；SMS 是手机号；IN_APP 是站内会员 id）
  channel           EMAIL / SMS / PUSH / IN_APP（枚举）
  template          模板标识（业务方注册的模板代码，不含内容）
  payload           dict[str, Any] 模板渲染参数（纯 KV，不含 SMTP 凭据）
  status            PENDING / SENT / FAILED / SKIPPED
  idempotency_key   业务幂等键（同 tenant + key 只入队一次）
  created_at / sent_at / last_error 时间线

渠道分发（notification_service.py）
----------------------------------
send_one 按 channel 分发；M2-05b 最小脚手架只实现 IN_APP（静默 SKIPPED），
EMAIL / SMS / PUSH 全部 raise NotImplementedError("SMTP 账号未配置")。
后续 M2-20 会把 infrastructure/mail.py 的 SMTPMailer 接入 EMAIL 分支，
M2-21 再接 PUSH/SMS。这个 raise 不是 silent skip，必须让 worker 感知到
"渠道未就绪"，以便 outbox 恢复协议决定重试还是标记 UNKNOWN。

幂等性
------
enqueue 在 notifications 表上对 (tenant_id, idempotency_key) 做唯一约束，
ON CONFLICT DO NOTHING 返回已存在的记录 id，确保同一业务事件不会产生多条通知。
"""

from yuliang.modules.notify.notification_models import (
    Channel,
    Notification,
    NotificationInput,
    NotificationStatus,
    SendResult,
)
from yuliang.modules.notify.notification_service import (
    NotificationServiceError,
    enqueue,
    send_one,
)

__all__ = [
    "Channel",
    "Notification",
    "NotificationInput",
    "NotificationStatus",
    "NotificationServiceError",
    "SendResult",
    "enqueue",
    "send_one",
]
