"""视频生成供应商无关协议：异步任务状态机、按秒/按次计价和结果描述。

本模块不连接真实供应商，也不定义任何默认价格；状态推进只允许本表列出的迁移。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from yuliang.modules.aikernel.cost_models import BillingOutcome

VideoTaskState = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "TIMEOUT", "CANCELLED"]
ProviderTaskState = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED"]
PricingMode = Literal["PER_SECOND", "PER_CALL"]

# 本地任务账本状态机：终态不可复活，取消只能发生在继续轮询之前。
VIDEO_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"RUNNING", "FAILED", "TIMEOUT", "CANCELLED"}),
    "RUNNING": frozenset({"SUCCEEDED", "FAILED", "TIMEOUT", "CANCELLED"}),
    "SUCCEEDED": frozenset(),
    "FAILED": frozenset(),
    "TIMEOUT": frozenset(),
    "CANCELLED": frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    """进行中状态可继续更新轮询计数；终态不可再写，跨状态必须走显式迁移。"""
    if current not in VIDEO_TRANSITIONS or target not in VIDEO_TRANSITIONS:
        return False
    if current == target:
        return current in {"PENDING", "RUNNING"}
    return target in VIDEO_TRANSITIONS[current]


def _external(value: str) -> str:
    if not 1 <= len(value) <= 128 or any(ord(character) < 32 for character in value):
        raise ValueError("供应商任务标识非法")
    return value


class _VideoModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class VideoStartRequest(_VideoModel):
    prompt: str = Field(min_length=1, max_length=20000)
    duration_seconds: int | None = Field(default=None, strict=True, ge=1, le=600)

    @field_validator("prompt")
    @classmethod
    def prompt_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("视频生成提示不可为空")
        return value


class VideoTaskStart(_VideoModel):
    external_id: str

    @field_validator("external_id")
    @classmethod
    def valid_external(cls, value: str) -> str:
        return _external(value)


class VideoTaskPoll(_VideoModel):
    external_id: str
    state: ProviderTaskState
    billing: BillingOutcome = BillingOutcome()

    @field_validator("external_id")
    @classmethod
    def valid_external(cls, value: str) -> str:
        return _external(value)


class VideoTaskResult(_VideoModel):
    external_id: str
    duration_seconds: int = Field(strict=True, ge=1, le=600)
    mime: Literal["video/mp4", "video/webm", "video/quicktime"]
    size_bytes: int = Field(strict=True, ge=1, le=104857600)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    billing: BillingOutcome

    @field_validator("external_id")
    @classmethod
    def valid_external(cls, value: str) -> str:
        return _external(value)


class VideoPrice(_VideoModel):
    """环境变量声明的计价模式与单价；金额仍须由数据库可信价目来源支撑。"""

    mode: PricingMode
    currency: Literal["CNY", "USD"] = "CNY"
    micro_minor_per_unit: int = Field(strict=True, ge=1, le=9223372036854775807)
