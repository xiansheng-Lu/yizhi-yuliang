"""视频生成协议、状态机与环境配置的纯单元验证；不做任何网络调用。"""

import pytest
from pydantic import ValidationError

from yuliang.modules.aikernel.cost_models import BillingOutcome
from yuliang.modules.content.generation_models import GenerationInput
from yuliang.modules.content.video_generation_config import (
    load_video_provider_config,
    video_generation_priced,
)
from yuliang.modules.content.video_generation_models import (
    VideoStartRequest,
    VideoTaskPoll,
    VideoTaskResult,
    can_transition,
)
from yuliang.platform.errors import ApiError, ErrorCode

BASE = {
    "YULIANG_VIDEOGEN_PRIMARY_ENDPOINT": "https://video.example.com/v1",
    "YULIANG_VIDEOGEN_PRIMARY_MODEL": "synthetic-video-model",
    "YULIANG_VIDEOGEN_PRIMARY_KEY": "test-only-key",
    "YULIANG_VIDEOGEN_PRIMARY_PRICE_PER_SECOND": "0.5",
    "YULIANG_VIDEOGEN_PRIMARY_RESOLUTION": "480p",
    "YULIANG_VIDEOGEN_PRIMARY_ASPECT_RATIO": "portrait",
}


def test_config_requires_endpoint_model_and_key() -> None:
    for missing in (
        "YULIANG_VIDEOGEN_PRIMARY_ENDPOINT",
        "YULIANG_VIDEOGEN_PRIMARY_MODEL",
        "YULIANG_VIDEOGEN_PRIMARY_KEY",
    ):
        values = dict(BASE)
        values.pop(missing)
        with pytest.raises(ApiError) as error:
            load_video_provider_config(values)
        assert error.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
        assert "视频生成供应商未配置" in error.value.message


def test_config_requires_explicit_resolution_and_aspect_ratio() -> None:
    for missing in (
        "YULIANG_VIDEOGEN_PRIMARY_RESOLUTION",
        "YULIANG_VIDEOGEN_PRIMARY_ASPECT_RATIO",
    ):
        values = dict(BASE)
        values.pop(missing)
        with pytest.raises(ApiError) as error:
            load_video_provider_config(values)
        assert error.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
        assert "视频生成供应商未配置" in error.value.message


def test_config_rejects_unlisted_resolution_and_aspect_ratio() -> None:
    for name, value in (
        ("YULIANG_VIDEOGEN_PRIMARY_RESOLUTION", "720p"),
        ("YULIANG_VIDEOGEN_PRIMARY_ASPECT_RATIO", "square"),
    ):
        with pytest.raises(ApiError) as error:
            load_video_provider_config(BASE | {name: value})
        assert error.value.code == ErrorCode.CAPABILITY_UNAVAILABLE


def test_config_per_second_price_converts_to_micro_minor() -> None:
    config = load_video_provider_config(BASE)
    assert config.endpoint == "https://video.example.com/v1"
    assert config.model == "synthetic-video-model"
    assert config.resolution == "480p"
    assert config.aspect_ratio == "portrait"
    assert config.price.mode == "PER_SECOND"
    assert config.price.micro_minor_per_unit == 50000000
    assert config.price.currency == "CNY"
    assert config.key.get_secret_value() == "test-only-key"


def test_config_per_call_price() -> None:
    values = dict(BASE)
    values.pop("YULIANG_VIDEOGEN_PRIMARY_PRICE_PER_SECOND")
    values["YULIANG_VIDEOGEN_PRIMARY_PRICE_PER_CALL"] = "2"
    config = load_video_provider_config(values)
    assert config.price.mode == "PER_CALL"
    assert config.price.micro_minor_per_unit == 200000000


def test_config_without_price_is_not_assembled() -> None:
    values = dict(BASE)
    values.pop("YULIANG_VIDEOGEN_PRIMARY_PRICE_PER_SECOND")
    assert video_generation_priced(values) is False
    with pytest.raises(ApiError) as error:
        load_video_provider_config(values)
    assert error.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
    assert video_generation_priced(BASE) is True


@pytest.mark.parametrize(
    "values",
    [
        BASE | {"YULIANG_VIDEOGEN_PRIMARY_PRICE_PER_CALL": "1"},
        BASE | {"YULIANG_VIDEOGEN_PRIMARY_PRICE_PER_SECOND": "0"},
        BASE | {"YULIANG_VIDEOGEN_PRIMARY_PRICE_PER_SECOND": "-1"},
        BASE | {"YULIANG_VIDEOGEN_PRIMARY_PRICE_PER_SECOND": "abc"},
        BASE | {"YULIANG_VIDEOGEN_PRIMARY_ENDPOINT": "http://video.example.com/v1"},
        BASE | {"YULIANG_VIDEOGEN_PRIMARY_ENDPOINT": "https://user@video.example.com/v1"},
    ],
)
def test_config_rejects_ambiguous_or_unsafe(values: dict[str, str]) -> None:
    with pytest.raises(ApiError) as error:
        load_video_provider_config(values)
    assert error.value.code == ErrorCode.CAPABILITY_UNAVAILABLE


def test_state_machine_transitions() -> None:
    assert can_transition("PENDING", "RUNNING")
    assert can_transition("RUNNING", "SUCCEEDED")
    assert can_transition("RUNNING", "FAILED")
    assert can_transition("RUNNING", "TIMEOUT")
    assert can_transition("RUNNING", "CANCELLED")
    assert can_transition("RUNNING", "RUNNING")
    assert not can_transition("PENDING", "SUCCEEDED")
    assert not can_transition("RUNNING", "PENDING")
    for terminal in ("SUCCEEDED", "FAILED", "TIMEOUT", "CANCELLED"):
        for target in ("PENDING", "RUNNING", "SUCCEEDED"):
            assert not can_transition(terminal, target)


def test_protocol_models_validate_bounds() -> None:
    with pytest.raises(ValidationError):
        VideoStartRequest(prompt="   ")
    with pytest.raises(ValidationError):
        VideoStartRequest(prompt="ok", duration_seconds=0)
    with pytest.raises(ValidationError):
        VideoTaskPoll(external_id="bad\nid", state="RUNNING")
    with pytest.raises(ValidationError):
        VideoTaskResult(
            external_id="x",
            duration_seconds=0,
            mime="video/mp4",
            size_bytes=1024,
            sha256="d" * 64,
            billing=BillingOutcome(),
        )
    with pytest.raises(ValidationError):
        VideoTaskResult(
            external_id="x",
            duration_seconds=5,
            mime="video/mp4",
            size_bytes=1024,
            sha256="D" * 64,
            billing=BillingOutcome(),
        )


def test_image_input_rejects_duration() -> None:
    with pytest.raises(ValidationError):
        GenerationInput(
            requestKey="00000000-0000-4000-8000-000000000001",
            runId="00000000-0000-4000-8000-000000000002",
            stepCode="generate",
            expectedRunVersion=1,
            expectedAttempt=1,
            documentId="00000000-0000-4000-8000-000000000003",
            documentVersion=1,
            kind="IMAGE",
            provider="synthetic",
            priceKey="synthetic.job",
            prompt="合成",
            durationSeconds=5,
        )
