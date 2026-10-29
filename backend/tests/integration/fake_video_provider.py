"""测试专用合成视频供应商；不发起网络调用，只按脚本推进异步状态。"""

from yuliang.modules.aikernel.cost_models import BillingOutcome
from yuliang.modules.content.video_generation_models import (
    ProviderTaskState,
    VideoStartRequest,
    VideoTaskPoll,
    VideoTaskResult,
    VideoTaskStart,
)


class FakeVideoProvider:
    def __init__(self) -> None:
        self.starts: list[tuple[str, VideoStartRequest]] = []
        self.polls: list[str] = []
        self.fetches: list[str] = []
        self.start_error: Exception | None = None
        self.poll_states: list[ProviderTaskState] = []
        self.poll_state: ProviderTaskState = "RUNNING"
        self.poll_billing = BillingOutcome()
        self.result: VideoTaskResult | None = None

    def start_task(self, request_key: str, request: VideoStartRequest) -> VideoTaskStart:
        if self.start_error is not None:
            raise self.start_error
        self.starts.append((request_key, request))
        return VideoTaskStart(external_id="fake-" + request_key.replace("-", "")[:12])

    def poll_task(self, external_task_id: str) -> VideoTaskPoll:
        self.polls.append(external_task_id)
        state: ProviderTaskState = (
            self.poll_states.pop(0) if self.poll_states else self.poll_state
        )
        if state == "FAILED":
            return VideoTaskPoll(
                external_id=external_task_id, state=state, billing=self.poll_billing
            )
        return VideoTaskPoll(external_id=external_task_id, state=state)

    def fetch_result(self, external_task_id: str) -> VideoTaskResult:
        self.fetches.append(external_task_id)
        if self.result is None:
            raise RuntimeError("测试未配置视频结果")
        return self.result
