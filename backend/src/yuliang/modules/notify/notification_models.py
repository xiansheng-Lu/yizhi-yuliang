"""通知模块 M2-05b：渠道枚举、状态枚举、入队输入模型、持久化快照模型。

模型设计遵循：
- StrEnum 走 Python 3.11+ StrEnum（项目已锁定 py313）
- frozen=True + extra="forbid"，与 idempotency_models / capability_models 等保持一致
- 所有时间戳用 AwareDatetime，禁止 naive datetime 混入
- 字段按"业务必填" vs "系统生成"清晰切分
"""

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Channel(StrEnum):
    """通知触达渠道。M2-05b 只定义枚举，不绑定具体发送器。

    - EMAIL: 后续由 infrastructure/mail.py 的 SMTPMailer 接入
    - SMS/PUSH: M2-21 直播人工交接时接入第三方服务
    - IN_APP: 站内通知，后续直接写数据库（M2-20 时间线读模型消费）
    """

    EMAIL = "EMAIL"
    SMS = "SMS"
    PUSH = "PUSH"
    IN_APP = "IN_APP"


class NotificationStatus(StrEnum):
    """通知生命周期状态。

    - PENDING: 已入队，待 worker 投递
    - SENT:    渠道已接受（不代表用户已读）
    - FAILED:  渠道明确拒绝（SMTP 4xx/5xx、短信网关错误码等）
    - SKIPPED: 渠道在当前环境下被策略跳过（如 SMTP 未配置但项目走静默降级）
    """

    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class NotificationInput(BaseModel):
    """业务方调用 enqueue 时的输入模型。

    注意：idempotency_key 必须由业务方传入，通知模块不替业务方猜幂等边界。
    推荐格式："<业务模块>:<事件类型>:<业务主键>"，如 "APPROVAL:REQUIRED:job-123"。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    tenant_id: UUID
    recipient: str = Field(min_length=1, max_length=512)
    channel: Channel
    template: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=256)


class Notification(BaseModel):
    """通知持久化快照，对应 notifications 表的一行。

    字段语义：
    - id:               UUID 主键（系统生成）
    - tenant_id:        租户隔离
    - recipient:        目标地址（EMAIL 是邮箱；SMS 是手机号；IN_APP 是站内会员 id）
    - channel:          渠道枚举
    - template:         模板标识（纯字符串代码，不含邮件正文）
    - payload:          模板渲染参数，业务方 KV（不含 SMTP 凭据）
    - status:           生命周期状态
    - idempotency_key:  业务幂等键，(tenant_id, idempotency_key) 唯一
    - created_at:       入队时间（PENDING 时刻）
    - sent_at:          渠道接受时间（SENT 时刻）
    - last_error:       最近一次失败的可读错误信息（不含堆栈）
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    tenant_id: UUID
    recipient: str = Field(min_length=1, max_length=512)
    channel: Channel
    template: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any]
    status: NotificationStatus
    idempotency_key: str = Field(min_length=1, max_length=256)
    created_at: AwareDatetime
    sent_at: AwareDatetime | None = None
    last_error: str | None = None


class SendResult(BaseModel):
    """send_one 的返回快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    notification_id: UUID
    status: Literal[
        NotificationStatus.SENT, NotificationStatus.FAILED, NotificationStatus.SKIPPED
    ]
    sent_at: AwareDatetime | None = None
    error: str | None = None
