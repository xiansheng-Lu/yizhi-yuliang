"""视频供应商适配器接口；默认不可用端口只拒绝，不伪造任何生成结果。"""

from typing import Protocol

from yuliang.modules.content.video_generation_models import (
    VideoStartRequest,
    VideoTaskPoll,
    VideoTaskResult,
    VideoTaskStart,
)
from yuliang.platform.errors import ApiError, ErrorCode


class VideoGenerationProvider(Protocol):
    """视频异步轮询最小集：提交、查询、取回结果三段式。"""

    def start_task(self, request_key: str, request: VideoStartRequest) -> VideoTaskStart: ...

    def poll_task(self, external_task_id: str) -> VideoTaskPoll: ...

    def fetch_result(self, external_task_id: str) -> VideoTaskResult: ...


class UnconfiguredVideoProvider:
    def start_task(self, request_key: str, request: VideoStartRequest) -> VideoTaskStart:
        raise ApiError(
            ErrorCode.CAPABILITY_UNAVAILABLE, "视频生成供应商未配置，未发起付费调用"
        )

    def poll_task(self, external_task_id: str) -> VideoTaskPoll:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "视频生成供应商未配置，未轮询")

    def fetch_result(self, external_task_id: str) -> VideoTaskResult:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "视频生成供应商未配置，未取回结果")
