"""第三方中转站视频生成适配器：Web只登记并投递，阻塞生成由后台Worker执行。

中转站同步生成耗时可达十分钟以上，因此start_task绝不发起HTTP调用，只写入
relay_video_calls并投递ai-media队列；poll/fetch只读本地记录，不重复计费。
"""

import hashlib
import json
from collections.abc import Callable
from typing import Literal
from uuid import UUID

from yuliang.modules.aikernel.cost_models import BillingOutcome
from yuliang.modules.content.relay_video_calls import RelayVideoCall, VideoCallStore
from yuliang.modules.content.video_generation_config import (
    ASPECT_RATIOS,
    RESOLUTIONS,
    VideoProviderConfig,
)
from yuliang.modules.content.video_generation_models import (
    VideoStartRequest,
    VideoTaskPoll,
    VideoTaskResult,
    VideoTaskStart,
)
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.errors import ApiError, ErrorCode

TASK_NAME = "yuliang.ai_media.relay_video_generate"
MAX_DURATION_SECONDS = 15
MAX_1080P_DURATION_SECONDS = 10
_MICRO_PER_MINOR = 1000000

VideoTaskSender = Callable[[UUID, VerifiedIdentity], None]


def _celery_sender(call_id: UUID, identity: VerifiedIdentity) -> None:
    from yuliang.worker.app import app

    payload = {
        "event_id": str(call_id),
        "tenant_id": identity.tenant_id,
        "job_id": str(call_id),
        "membership_id": str(identity.membership_id),
        "user_id": str(identity.user_id),
    }
    app.send_task(
        TASK_NAME,
        task_id=str(call_id),
        kwargs={"payload": payload},
        kwargsrepr="<ids-only>",
        mandatory=True,
        retry=False,
    )


def _amount_minor(micro_minor: int) -> int:
    # 账本只保存整数分；不足一分按一分计，避免低估已发生费用。
    return max(1, -(-micro_minor // _MICRO_PER_MINOR))


def _identifier(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise ApiError(ErrorCode.NO_DATA, "视频本地任务标识非法") from None


class RelayVideoGenerationProvider:
    """start_task返回本地UUID；真实阻塞调用与产物落私有存储由Worker承接。"""

    def __init__(
        self,
        *,
        config: VideoProviderConfig,
        calls: VideoCallStore,
        sender: VideoTaskSender | None = None,
    ) -> None:
        if config.resolution not in RESOLUTIONS:
            raise ValueError("视频分辨率不在供应商白名单")
        if config.aspect_ratio not in ASPECT_RATIOS:
            raise ValueError("视频画幅不在供应商白名单")
        if not 0 < config.price.micro_minor_per_unit <= 9223372036854775807:
            raise ValueError("视频单价缺失或非法，禁止按0元计价")
        self.config = config
        self.calls = calls
        self.sender = sender or _celery_sender

    def start_task(self, request_key: str, request: VideoStartRequest) -> VideoTaskStart:
        try:
            key = UUID(request_key)
        except ValueError:
            raise ApiError(ErrorCode.INVALID_INPUT, "视频生成请求键非法") from None
        duration = request.duration_seconds
        if duration is None or type(duration) is not int:
            raise ApiError(ErrorCode.INVALID_INPUT, "视频生成时长必填且须为整数秒")
        if duration > MAX_DURATION_SECONDS:
            raise ApiError(ErrorCode.INVALID_INPUT, "视频生成时长不得超过15秒")
        if self.config.resolution == "1080p" and duration > MAX_1080P_DURATION_SECONDS:
            raise ApiError(ErrorCode.INVALID_INPUT, "1080p视频时长不得超过10秒")
        shop = self.calls.shop_for_request(key)
        if shop is None:
            raise ApiError(ErrorCode.RESULT_UNKNOWN, "生成任务账本缺失，未发起视频调用")
        call, created = self.calls.create(
            request_key=key,
            shop_id=shop,
            prompt=request.prompt,
            duration_seconds=duration,
            resolution=self.config.resolution,
            aspect_ratio=self.config.aspect_ratio,
            pricing_mode=self.config.price.mode,
            unit_price_micro_minor=self.config.price.micro_minor_per_unit,
            currency=self.config.price.currency,
        )
        if not created:
            expected = (
                request.prompt,
                duration,
                self.config.resolution,
                self.config.aspect_ratio,
            )
            actual = (
                call.prompt,
                call.duration_seconds,
                call.resolution,
                call.aspect_ratio,
            )
            if actual != expected:
                raise ApiError(ErrorCode.IDEMPOTENCY_MISMATCH, "原视频请求键已用于其他参数")
            return VideoTaskStart(external_id=str(call.id))
        try:
            self.sender(call.id, self.calls.identity)
        except Exception:
            # 记录已提交但队列未确认；不在这里重投，结果按未知等待人工核对。
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN, "视频调用已登记但后台投递失败，结果待核对"
            ) from None
        return VideoTaskStart(external_id=str(call.id))

    def poll_task(self, external_task_id: str) -> VideoTaskPoll:
        call = self._call(external_task_id)
        # 本地PENDING表示Worker尚未领取；对外仍是进行中，不能回到编排前的PENDING。
        state = "RUNNING" if call.state in {"PENDING", "RUNNING"} else call.state
        if state not in {"RUNNING", "SUCCEEDED", "FAILED"}:
            raise ApiError(ErrorCode.RESULT_UNKNOWN, "视频调用记录状态非法")
        return VideoTaskPoll(external_id=str(call.id), state=state)

    def fetch_result(self, external_task_id: str) -> VideoTaskResult:
        call = self._call(external_task_id)
        if (
            call.state != "SUCCEEDED"
            or call.mime != "video/mp4"
            or call.size_bytes is None
            or call.sha256 is None
            or call.measured_duration_seconds is None
            or call.settled_amount_micro_minor is None
        ):
            raise ApiError(ErrorCode.RESULT_UNKNOWN, "视频任务尚未成功，不能取回结果")
        if call.currency not in {"CNY", "USD"}:
            raise ApiError(ErrorCode.RESULT_UNKNOWN, "视频调用币种非法，不能入账")
        currency: Literal["CNY", "USD"] = "CNY" if call.currency == "CNY" else "USD"
        amount = _amount_minor(call.settled_amount_micro_minor)
        return VideoTaskResult(
            external_id=str(call.id),
            duration_seconds=call.measured_duration_seconds,
            mime="video/mp4",
            size_bytes=call.size_bytes,
            sha256=call.sha256,
            billing=BillingOutcome(
                state="KNOWN",
                amount_minor=amount,
                currency=currency,
                proof_hash=self._proof(call, amount),
            ),
        )

    def reconcile_unknown(self, older_than_seconds: int = 1800) -> int:
        return self.calls.reconcile(older_than_seconds)

    def _call(self, external_task_id: str) -> RelayVideoCall:
        call = self.calls.get(_identifier(external_task_id))
        if call is None:
            raise ApiError(ErrorCode.NO_DATA, "视频调用记录不存在")
        return call

    def _proof(self, call: RelayVideoCall, amount_minor: int) -> str:
        receipt = {
            "provider": "relay-video",
            "endpoint": self.config.endpoint,
            "model": self.config.model,
            "requestKey": str(call.request_key),
            "externalId": str(call.id),
            "sha256": call.sha256,
            "seconds": call.measured_duration_seconds,
            "resolution": call.resolution,
            "aspectRatio": call.aspect_ratio,
            "amountMinor": amount_minor,
            "currency": call.currency,
        }
        return hashlib.sha256(
            json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()


def reconcile_abandoned_calls(store: VideoCallStore, older_than_seconds: int = 1800) -> int:
    """可复用对账入口：只把超时的RUNNING标为结果未知，不重跑外部调用。"""
    return store.reconcile(older_than_seconds)
