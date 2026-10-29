"""真实中转站生图调用；默认排除且需显式开关，未配置单价时直接跳过。"""

import os
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from yuliang.modules.content.relay_image_generation_provider import (
    RelayImageGenerationProvider,
)

pytestmark = pytest.mark.live


class RecordingWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str]] = []

    def write(self, request_key: str, document_id: str, data: bytes, mime: str) -> UUID:
        self.calls.append((data, mime))
        return UUID("00000000-0000-4000-8000-0000000000cc")


@pytest.mark.skipif(
    os.environ.get("YULIANG_TEST_REAL_IMAGEGEN") != "1",
    reason="真实付费生图需显式设置YULIANG_TEST_REAL_IMAGEGEN=1",
)
def test_real_relay_image_generation_once() -> None:
    endpoint = os.environ.get("YULIANG_IMAGEGEN_PRIMARY_ENDPOINT", "").strip()
    model = os.environ.get("YULIANG_IMAGEGEN_PRIMARY_MODEL", "").strip()
    key = os.environ.get("YULIANG_IMAGEGEN_PRIMARY_KEY", "").strip()
    price = os.environ.get("YULIANG_IMAGEGEN_PRIMARY_PRICE_PER_CALL", "").strip()
    if not endpoint or not model or not key or not price:
        pytest.skip("真实生图配置不完整；单价为空视为未配置，不按0元发起调用")
    minor = Decimal(price) * 100
    assert minor == minor.to_integral_value() and minor > 0
    writer = RecordingWriter()
    provider = RelayImageGenerationProvider(
        endpoint=endpoint,
        model=model,
        api_key=SecretStr(key),
        price_minor=int(minor),
        writer=writer,
        transport=None,
        timeout_seconds=180.0,
    )
    result = provider.submit(
        str(uuid4()),
        "e" * 64,
        {
            "kind": "IMAGE",
            "prompt": "一张纯白背景的简洁产品图，居中构图",
            "documentId": str(uuid4()),
        },
    )
    assert result.state == "READY_FOR_REVIEW"
    assert result.billing.state == "KNOWN" and result.billing.amount_minor == int(minor)
    assert len(writer.calls) == 1
    assert writer.calls[0][1] in {"image/png", "image/jpeg", "image/webp"}
    assert len(writer.calls[0][0]) > 0
