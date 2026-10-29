"""通知模块 M2-05b 单元测试。

覆盖：
- Pydantic 模型校验（字段长度、枚举值、extra forbid）
- channel_available 渠道可用性判定
- Notification 反序列化辅助函数
- enqueue 在"已存在"/"新插入"/"并发冲突"三种路径下的行为（mock Session）
- send_one 分发：EMAIL raise NotImplementedError；IN_APP → SKIPPED；PENDING 外状态幂等
- send_batch 单条 FAILED 不影响后续

注意：本测试是 unit 层，不连真实 PG。数据库约束（(tenant_id, idempotency_key) 唯一）
在 integration 测试和 alembic 迁移中验证。
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.notify.notification_models import (
    Channel,
    NotificationInput,
    NotificationStatus,
)
from yuliang.modules.notify.notification_service import (
    NotificationServiceError,
    _row_to_notification,
    channel_available,
    enqueue,
    send_one,
)

# ---------- 模型校验 ----------


def _valid_input(**overrides: Any) -> NotificationInput:
    values: dict[str, Any] = {
        "tenant_id": uuid4(),
        "recipient": "someone@example.invalid",
        "channel": Channel.EMAIL,
        "template": "approval_required",
        "payload": {"job_id": "123"},
        "idempotency_key": "approval:required:job-123",
    }
    return NotificationInput(**(values | overrides))


def test_model_rejects_empty_recipient() -> None:
    with pytest.raises(ValidationError):
        _valid_input(recipient="")


def test_model_rejects_too_long_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        _valid_input(idempotency_key="x" * 257)


def test_model_rejects_unknown_channel() -> None:
    with pytest.raises(ValidationError):
        NotificationInput.model_validate(
            _valid_input().model_dump() | {"channel": "WECHAT_PUSH"}
        )


def test_model_forbids_extra_fields_without_leaking() -> None:
    """Pydantic extra="forbid" 拒绝未知字段；错误类型是 extra_forbidden。"""
    with pytest.raises(ValidationError) as error:
        NotificationInput.model_validate(
            _valid_input().model_dump() | {"smtp_password": "dummy-value"}
        )
    # 确认是 extra_forbidden 错误，不是别的校验
    errors = error.value.errors()
    assert errors[0]["type"] == "extra_forbidden"
    assert errors[0]["loc"] == ("smtp_password",)


# ---------- channel_available ----------


def test_in_app_is_only_available_channel_in_scaffold() -> None:
    assert channel_available(Channel.IN_APP) is True


@pytest.mark.parametrize("channel", [Channel.EMAIL, Channel.SMS, Channel.PUSH])
def test_external_channels_not_available_in_scaffold(channel: Channel) -> None:
    assert channel_available(channel) is False


# ---------- _row_to_notification ----------


def _fake_row(
    *,
    channel: Channel = Channel.EMAIL,
    status: NotificationStatus = NotificationStatus.PENDING,
    recipient: str = "someone@example.invalid",
    template: str = "approval_required",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "recipient": recipient,
        "channel": channel.value,
        "template": template,
        "payload": payload if payload is not None else {"job_id": "123"},
        "status": status.value,
        "idempotency_key": "approval:required:job-123",
        "created_at": now,
        "sent_at": None,
        "last_error": None,
    }


def test_row_to_notification_round_trip() -> None:
    row = _fake_row(status=NotificationStatus.SENT, channel=Channel.IN_APP)
    notification = _row_to_notification(row)
    assert notification.status == NotificationStatus.SENT
    assert notification.channel == Channel.IN_APP
    assert notification.payload == {"job_id": "123"}


def test_row_to_notification_handles_jsonb_dict() -> None:
    """psycopg3 读 jsonb 列直接返回 dict；这是正常路径。"""
    row = _fake_row(payload={"nested": {"a": 1}})
    n = _row_to_notification(row)
    assert n.payload == {"nested": {"a": 1}}


def test_row_to_notification_handles_jsonb_none() -> None:
    """psycopg3 读 jsonb 列如果是 NULL 返回 None；_row_to_notification 应归一成 {}。"""
    row = _fake_row()
    row["payload"] = None  # 显式模拟数据库 NULL jsonb
    n = _row_to_notification(row)
    assert n.payload == {}


# ---------- enqueue（mock Session） ----------


class _FakeResult:
    """模拟 sqlalchemy Result，提供 mappings() 返回迭代的 MappingRow。"""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> "_FakeMappings":
        return _FakeMappings(self._rows)


class _FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = list(rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def one(self) -> dict[str, Any]:
        if not self._rows:
            raise Exception("one() on empty result")
        if len(self._rows) > 1:
            raise Exception("one() on multi result")
        return self._rows[0]


class _FakeSession:
    """可控的 fake Session，用 responses 列表模拟多次 execute 返回。

    也实现上下文管理器协议，与真实 sqlalchemy.orm.Session 兼容。
    """

    def __init__(self, responses: list[_FakeResult]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    def execute(self, _stmt: Any, params: Any = None) -> _FakeResult:  # noqa: ANN401
        self.calls.append(str(params.get("key") if isinstance(params, dict) else params))
        if not self._responses:
            raise Exception("no response configured")
        return self._responses.pop(0)

    def begin(self) -> "_FakeSession":
        return self

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


def test_enqueue_returns_existing_when_idempotency_key_hit() -> None:
    existing_row = _fake_row()
    session = _FakeSession([_FakeResult([existing_row])])
    result = enqueue(
        session,
        input=_valid_input(tenant_id=existing_row["tenant_id"]),
        now=datetime.now(UTC),
    )
    assert result.id == existing_row["id"]
    assert session.calls  # 只应执行了 SELECT，不执行 INSERT


def test_enqueue_inserts_when_no_existing() -> None:
    new_row = _fake_row()
    # 第一次 SELECT 返回空 → 走 INSERT；INSERT RETURNING 新行
    session = _FakeSession([_FakeResult([]), _FakeResult([new_row])])
    result = enqueue(
        session,
        input=_valid_input(tenant_id=new_row["tenant_id"]),
        now=datetime.now(UTC),
    )
    assert result.id == new_row["id"]


def test_enqueue_falls_back_to_select_after_conflict() -> None:
    # INSERT 返回 None（ON CONFLICT DO NOTHING 触发）→ 回退第二次 SELECT 拿到已有行
    existing_after = _fake_row()
    session = _FakeSession([_FakeResult([]), _FakeResult([]), _FakeResult([existing_after])])
    result = enqueue(
        session,
        input=_valid_input(tenant_id=existing_after["tenant_id"]),
        now=datetime.now(UTC),
    )
    assert result.id == existing_after["id"]


# ---------- send_one（mock Session） ----------


def test_send_one_raises_for_email_when_smtp_not_configured() -> None:
    row = _fake_row(channel=Channel.EMAIL, status=NotificationStatus.PENDING)
    session = _FakeSession([_FakeResult([row])])
    with pytest.raises(NotImplementedError, match="SMTP 账号未配置"):
        send_one(session, notification_id=row["id"], now=datetime.now(UTC))


def test_send_one_in_app_skips_without_error() -> None:
    row = _fake_row(channel=Channel.IN_APP, status=NotificationStatus.PENDING)
    # SELECT + UPDATE 两次 execute
    session = _FakeSession([_FakeResult([row]), _FakeResult([])])
    result = send_one(session, notification_id=row["id"], now=datetime.now(UTC))
    assert result.status == NotificationStatus.SKIPPED
    assert result.sent_at is not None


def test_send_one_is_idempotent_for_non_pending_status() -> None:
    row = _fake_row(channel=Channel.EMAIL, status=NotificationStatus.SENT)
    session = _FakeSession([_FakeResult([row])])
    result = send_one(session, notification_id=row["id"], now=datetime.now(UTC))
    # 已是 SENT，不应再触发 EMAIL 的 NotImplementedError
    assert result.status == NotificationStatus.SENT


def test_send_one_raises_for_missing_notification() -> None:
    session = _FakeSession([_FakeResult([])])
    with pytest.raises(NotificationServiceError, match="不存在"):
        send_one(session, notification_id=uuid4(), now=datetime.now(UTC))


# ---------- send_batch ----------


class _FakeSessionFactory:
    def __init__(self, responses_per_session: list[list[_FakeResult]]) -> None:
        self._remaining = list(responses_per_session)

    def __call__(self) -> _FakeSession:
        if not self._remaining:
            raise Exception("no session configured")
        return _FakeSession(self._remaining.pop(0))

    def __enter__(self) -> "_FakeSession":
        return self()

    def __exit__(self, *args: Any) -> None:
        pass


def test_send_batch_email_failed_does_not_block_in_app() -> None:
    email_row = _fake_row(channel=Channel.EMAIL, status=NotificationStatus.PENDING)
    inapp_row = _fake_row(channel=Channel.IN_APP, status=NotificationStatus.PENDING)
    now = datetime.now(UTC)
    from yuliang.modules.notify.notification_service import send_batch as _send_batch

    # EMAIL: send_one 内部 SELECT → raise，send_batch except 里 UPDATE → 共 2 次
    # IN_APP: send_one 内部 SELECT + UPDATE → 共 2 次
    factory = _FakeSessionFactory(
        responses_per_session=[
            [_FakeResult([email_row]), _FakeResult([])],   # email session
            [_FakeResult([inapp_row]), _FakeResult([])],   # in_app session
        ]
    )

    def session_factory() -> _FakeSession:
        return factory()

    results = _send_batch(
        ids=[email_row["id"], inapp_row["id"]],
        session_factory=session_factory,  # type: ignore[arg-type]
        now_provider=lambda: now,
    )
    assert len(results) == 2
    assert results[0].status == NotificationStatus.FAILED  # EMAIL raise
    assert results[1].status == NotificationStatus.SKIPPED  # IN_APP 正常


def test_send_batch_rejects_invalid_limit() -> None:
    with pytest.raises(NotificationServiceError):
        from yuliang.modules.notify.notification_service import send_batch as _send_batch

        _send_batch(
            ids=[uuid4()],
            session_factory=lambda: _FakeSession([]),  # type: ignore[arg-type]
            now_provider=lambda: datetime.now(UTC),
            limit=0,
        )


# ---------- 集成辅助：uuid 类型 ----------


def test_notification_id_type_is_uuid() -> None:
    row = _fake_row()
    n = _row_to_notification(row)
    assert isinstance(n.id, UUID)
    assert isinstance(n.tenant_id, UUID)
