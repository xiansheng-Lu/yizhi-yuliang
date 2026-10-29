"""真实中转站视频调用；默认排除，只有显式付费许可且配置完整时才执行一次。"""

import os

import pytest

from yuliang.modules.content.relay_video_generation_worker import request_artifact
from yuliang.modules.content.video_generation_config import load_video_provider_config
from yuliang.platform.errors import ApiError

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    os.environ.get("YULIANG_TEST_REAL_VIDEOGEN") != "1",
    reason="真实付费视频需显式设置YULIANG_TEST_REAL_VIDEOGEN=1",
)
def test_real_relay_video_generation_once() -> None:
    try:
        config = load_video_provider_config(os.environ)
    except ApiError:
        pytest.skip("真实视频配置不完整；单价为空视为未配置，不按0元发起调用")
    artifact = request_artifact(
        config=config,
        prompt="一段纯白背景的简洁产品展示，固定机位",
        duration_seconds=2,
        resolution=config.resolution,
        aspect_ratio=config.aspect_ratio,
    )
    assert artifact.provider_task_id
    assert artifact.measured_duration_seconds >= 1
    assert 0 < len(artifact.data) <= 104857600
    assert len(artifact.sha256) == 64
