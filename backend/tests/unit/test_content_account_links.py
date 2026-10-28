"""客户端不能提供身份/原生ID/文件证明，也不能把MOCK转为授权。"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.content.account_links_models import LinkCancel, LinkChange


@pytest.mark.parametrize(
    "change",
    [
        {"nativeId": "forged"},
        {"linkedBy": "forged"},
        {"mode": "MOCK"},
        {"objectVersion": "forged"},
        {"accountId": "-" * 36},
        {"expectedVersion": True},
        {"productLine": "wxshop"},
    ],
)
def test_untrusted_fields_rejected(change: dict[str, object]) -> None:
    payload = dict(productLine="adq", accountId=str(uuid4()), expectedVersion=0) | change
    with pytest.raises(ValidationError):
        LinkChange.model_validate(payload)


def test_initial_and_cancel_version_boundaries() -> None:
    assert (
        LinkChange(productLine="adq", accountId=str(uuid4()), expectedVersion=0).expectedVersion
        == 0
    )
    with pytest.raises(ValidationError):
        LinkCancel(expectedVersion=0)
