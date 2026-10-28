"""默认不可用的生成端口；合成端口只用于技术验证，不能加载为真实供应商。"""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from yuliang.modules.aikernel.cost_models import BillingOutcome
from yuliang.platform.errors import ApiError, ErrorCode


@dataclass(frozen=True)
class ProviderResult:
    state: Literal["ACKNOWLEDGED", "UNKNOWN", "READY_FOR_REVIEW", "FAILED"]
    external_id: str | None = None
    asset_id: UUID | None = None
    billing: BillingOutcome = BillingOutcome()

    def __post_init__(self) -> None:
        if self.state not in {"ACKNOWLEDGED", "UNKNOWN", "READY_FOR_REVIEW", "FAILED"}:
            raise ValueError("未知供应商生成回执状态")
        if self.external_id is not None and (
            not 1 <= len(self.external_id) <= 128 or any(ord(c) < 32 for c in self.external_id)
        ):
            raise ValueError("供应商任务标识非法")
        if self.asset_id is not None and not isinstance(self.asset_id, UUID):
            raise ValueError("产物需内部已确认素材UUID")


class GenerationProvider(Protocol):
    def submit(
        self, request_key: str, payload_hash: str, parameters: dict[str, object]
    ) -> ProviderResult: ...
    def query(self, request_key: str) -> ProviderResult: ...


class UnavailableGenerationProvider:
    def submit(
        self, request_key: str, payload_hash: str, parameters: dict[str, object]
    ) -> ProviderResult:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "真实生成供应商尚未配置，未发起付费调用")

    def query(self, request_key: str) -> ProviderResult:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "供应商按原键查询尚未配置，保持待核对")
