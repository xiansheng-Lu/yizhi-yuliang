from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from yuliang.modules.execution.approval_outbox import ApprovedEvent
from yuliang.modules.execution.dispatch_runtime import DispatchRuntime
from yuliang.modules.execution.dispatch_service import ApprovedExecutionDispatch
from yuliang.platform.messages import WorkMessage


def source() -> ApprovedEvent:
    return ApprovedEvent(
        tenant_id=1,
        event_id=uuid4(),
        job_id=uuid4(),
        approved_job_version=3,
        decision_id=uuid4(),
        approval_id=uuid4(),
        subject_user_id=uuid4(),
        subject_membership_id=uuid4(),
    )


def test_default_runtime_never_constructs_executor() -> None:
    event = source()
    session = Mock()
    session.execute.return_value.first.return_value = ("CHANGE_DAILY_BUDGET", "tencent", "adq")
    assert DispatchRuntime().resolve(session, event) is None


def test_registry_rejects_silent_override_and_wrong_actor_context() -> None:
    registry = DispatchRuntime()
    factory = Mock()
    registry.register("CHANGE_DAILY_BUDGET", "tencent", "adq", factory)
    with pytest.raises(ValueError):
        registry.register("CHANGE_DAILY_BUDGET", "tencent", "adq", factory)
    event = source()
    session = Mock()
    session.execute.return_value.first.return_value = ("CHANGE_DAILY_BUDGET", "tencent", "adq")
    factory.return_value.ctx.identity = object()
    with pytest.raises(ValueError):
        registry.resolve(session, event)


def test_celery_return_drops_internal_credentials_and_payload() -> None:
    service = ApprovedExecutionDispatch(Mock(), SecretStr("synthetic"))
    event = source()
    item = WorkMessage(
        event_id=event.event_id,
        tenant_id=1,
        job_id=event.job_id,
        membership_id=event.subject_membership_id,
        user_id=event.subject_user_id,
    )
    service._handle = Mock(
        return_value=dict(
            event_id=str(event.event_id),
            job_id=str(event.job_id),
            state="BLOCKED_EXECUTOR",
            attempt_id=None,
            reason="EXECUTOR_UNAVAILABLE",
            secret="must-not-return",
            parameters={"secret": "must-not-return"},
            recovery=dict(recovery_id=str(uuid4()), state="DUE", outcome="must-not-return"),
        )
    )
    result = service.handle(item)
    assert set(result) == {"event_id", "job_id", "state", "attempt_id", "reason", "recovery"}
    assert set(result["recovery"]) == {
        "recovery_id",
        "attempt_id",
        "state",
        "next_event_id",
        "next_due",
    }
    assert "must-not-return" not in str(result)
