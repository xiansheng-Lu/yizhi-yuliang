"""固定推广资产协议：删除状态须真实False，未知properties不进入存档。"""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.connectors.http import HttpExchange
from yuliang.modules.connectors.lookup_protocol import TargetParameters, target_page


def response(deleted: object, **changes: object) -> HttpExchange:
    row = (
        dict(
            marketing_asset_id=123,
            marketing_asset_name="实际推广资产",
            marketing_asset_type="MARKETING_ASSET_TYPE_TRAFFIC_CAR_MODEL",
            is_deleted=deleted,
            properties=[dict(private="DO_NOT_STORE")],
        )
        | changes
    )
    return HttpExchange(
        uuid4(),
        "adq.lookup.targets",
        200,
        1,
        datetime.now(UTC),
        {},
        json.dumps(
            dict(
                code=0,
                data=dict(
                    list=[row], page_info=dict(page=1, page_size=10, total_number=1, total_page=1)
                ),
            )
        ).encode(),
    )


@pytest.mark.parametrize("deleted", [True, False, None])
def test_deletion_status_is_preserved_not_guessed(deleted: object) -> None:
    page, _, _ = target_page(response(deleted), "12345", 1, 10)
    assert page.records[0].values["is_deleted"] is deleted
    assert "properties" not in page.records[0].values
    assert "DO_NOT_STORE" not in json.dumps(page.records[0].values)


@pytest.mark.parametrize("deleted", [0, 1, "false", {}, []])
def test_nonboolean_deletion_rejected(deleted: object) -> None:
    with pytest.raises(ValueError):
        target_page(response(deleted), "12345", 1, 10)


def test_arbitrary_marketing_target_type_rejected() -> None:
    with pytest.raises(ValidationError):
        TargetParameters(
            account_id="12345", fields=[], marketing_target_type="MARKETING_TARGET_TYPE_FAKE"
        )
