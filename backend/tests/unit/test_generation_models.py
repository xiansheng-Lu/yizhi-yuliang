"""生成请求不接受自报费用/供应商状态，七态与提交态不混合。"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from yuliang.modules.content.generation_models import GenerationInput
from yuliang.modules.content.generation_provider import UnavailableGenerationProvider
from yuliang.platform.errors import ApiError


def value() -> dict[str, object]:
    return dict(
        requestKey=str(uuid4()),
        runId=str(uuid4()),
        stepCode="generate",
        expectedRunVersion=1,
        expectedAttempt=1,
        documentId=str(uuid4()),
        documentVersion=1,
        kind="IMAGE",
        provider="synthetic",
        priceKey="synthetic.job",
        prompt="明确生成输入",
    )


@pytest.mark.parametrize(
    "change",
    [
        {"actualCostMinor": 0},
        {"status": "SUCCEEDED"},
        {"externalJobId": "forged"},
        {"requestKey": "invalid"},
        {"expectedAttempt": True},
        {"prompt": " "},
    ],
)
def test_invalid_or_untrusted_generation_fields(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GenerationInput.model_validate(value() | change)


def test_canonical_input_digest_and_default_closed() -> None:
    request = GenerationInput.model_validate(value())
    assert request.digest() == GenerationInput.model_validate(request.model_dump()).digest()
    with pytest.raises(ApiError):
        UnavailableGenerationProvider().submit(request.requestKey, request.digest(), {})
