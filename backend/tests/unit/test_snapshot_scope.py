"""冻结scope不接受客户端身份、非法日期或其他时区。"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.snapshot.models import ReportingScope


def test_scope_normalizes_ids_and_retains_date() -> None:
    shop = uuid4()
    value = ReportingScope(
        shop_id=str(shop).upper(),
        live_session_id="all",
        connector_id="adq",
        date="2026-10-28",
        timezone="Asia/Shanghai",
    )
    assert value.shop_id == str(shop)
    assert value.model_dump(mode="json")["date"] == "2026-10-28"


@pytest.mark.parametrize(
    "change",
    [
        {"tenant_id": "1"},
        {"timezone": "UTC"},
        {"date": "2026-02-30"},
        {"shop_id": "invalid"},
        {"connector_id": "fake"},
    ],
)
def test_scope_rejects_untrusted_fields(change: dict[str, str]) -> None:
    value = (
        dict(
            shop_id="all",
            live_session_id="all",
            connector_id="all",
            date="2026-10-28",
            timezone="Asia/Shanghai",
        )
        | change
    )
    with pytest.raises(ValidationError):
        ReportingScope.model_validate(value)
