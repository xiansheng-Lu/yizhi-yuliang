import pytest
from pydantic import ValidationError

from yuliang.modules.workspace.models import MessageSend, ProjectCreate


def payload() -> dict[str, object]:
    return {
        "text": "普通输入",
        "source": "PC_MANUAL",
        "idempotencyKey": "test-key",
        "scope": {
            "shop_id": "all",
            "live_session_id": "all",
            "connector_id": "all",
            "date": "2026-10-27",
            "timezone": "Asia/Shanghai",
        },
    }


@pytest.mark.parametrize(
    "change",
    [
        {"text": " "},
        {"text": "x" * 501},
        {"text": "😀" * 251},
        {"source": "AUTOPILOT"},
        {"tenantId": "never-reflect-this-value"},
        {"idempotencyKey": ""},
        {
            "scope": {
                "shop_id": "all",
                "live_session_id": "all",
                "connector_id": "all",
                "date": "2026-02-30",
                "timezone": "Asia/Shanghai",
            }
        },
        {
            "scope": {
                "shop_id": "all",
                "live_session_id": None,
                "connector_id": "all",
                "date": "2026-10-27",
                "timezone": "Asia/Shanghai",
            }
        },
        {
            "scope": {
                "shop_id": "all",
                "live_session_id": "all",
                "connector_id": "all",
                "date": "2026-10-27",
                "timezone": "UTC",
            }
        },
    ],
)
def test_frozen_message_contract_rejects_invalid_or_forged_input(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as error:
        MessageSend.model_validate(payload() | change)
    assert "never-reflect-this-value" not in str(error.value)


def test_message_and_project_lengths_follow_frontend_utf16_and_trim() -> None:
    assert MessageSend.model_validate(payload() | {"text": "😀" * 250}).text == "😀" * 250
    assert ProjectCreate(name="  项目  ").name == "项目"
    with pytest.raises(ValidationError):
        ProjectCreate(name="😀" * 21)
