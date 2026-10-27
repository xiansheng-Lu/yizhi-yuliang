from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.platform.messages import WorkMessage, parse_message


def test_internal_message_rejects_secret_fields_without_reflecting_input() -> None:
    value = {
        "event_id": str(uuid4()),
        "tenant_id": 1,
        "job_id": str(uuid4()),
        "membership_id": str(uuid4()),
        "user_id": str(uuid4()),
    }
    with pytest.raises(ValueError) as error:
        parse_message(value | {"password": "never-echo-this-secret"})
    assert "never-echo" not in str(error.value)
    valid = parse_message(value)
    with pytest.raises(ValidationError):
        # 验证运行时拒绝修改冻结消息。
        valid.tenant_id = 2  # type: ignore[misc]
    assert set(valid.model_dump()) == {
        "event_id",
        "tenant_id",
        "job_id",
        "membership_id",
        "user_id",
    }
    assert isinstance(valid, WorkMessage)
