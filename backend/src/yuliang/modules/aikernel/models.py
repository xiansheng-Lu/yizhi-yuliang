"""模型与账本协议；仅保存服务端引用和受控事实，未知用量/费用保持NULL。"""

from collections.abc import Mapping
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    model_key: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,79}$")
    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,29}$")
    model: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}$")
    endpoint_ref: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    secret_ref: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    billing_currency: Literal["CNY", "USD"]


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    usage_state: Literal["UNKNOWN", "KNOWN"] = "UNKNOWN"
    cost_state: Literal["UNKNOWN", "KNOWN"] = "UNKNOWN"
    tokens_in: int | None = Field(default=None, strict=True, ge=0, le=9223372036854775807)
    tokens_out: int | None = Field(default=None, strict=True, ge=0, le=9223372036854775807)
    cost_minor: int | None = Field(default=None, strict=True, ge=0, le=9223372036854775807)
    cost_currency: Literal["CNY", "USD"] | None = None

    @model_validator(mode="after")
    def unknown_is_not_zero(self) -> "Usage":
        tokens_known = self.tokens_in is not None and self.tokens_out is not None
        if self.usage_state == "KNOWN" and not tokens_known:
            raise ValueError("已知用量必须包含输入与输出数量")
        if self.usage_state == "UNKNOWN" and (
            self.tokens_in is not None or self.tokens_out is not None
        ):
            raise ValueError("未知用量不能填数值")
        if (self.cost_state == "KNOWN") != (self.cost_minor is not None):
            raise ValueError("未知费用必须为空，已知费用必须明确数值")
        if (self.cost_state == "KNOWN") != (self.cost_currency is not None):
            raise ValueError("已知费用需明确币种，未知费用不登记结算币种")
        return self


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    status: Literal["OK", "FAILED", "TIMEOUT", "RATE_LIMITED", "FALLBACK"]
    dispatch_state: Literal["NOT_SENT", "UNKNOWN", "RESPONDED"] = "UNKNOWN"
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = Field(strict=True, ge=0, le=2147483647)
    external_request_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9._:/-]{1,128}$")

    @model_validator(mode="after")
    def success_has_response(self) -> "Completion":
        if self.status == "OK" and self.dispatch_state != "RESPONDED":
            raise ValueError("成功状态需要已收到供应商响应的事实")
        return self


class InvocationRequest(BaseModel):
    """由服务端场景用例构造，标识与路由不从模型输出读取。"""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    call_id: UUID
    request_id: UUID
    scenario: Literal["command_parse", "decision_diagnose", "report_narrate", "copywriting"]
    capability_alias: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    prompt_key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    prompt_version: int = Field(strict=True, gt=0, le=2147483647)
    model_role: Literal["PRIMARY", "BACKUP"] = "PRIMARY"
    parent_call_id: UUID | None = None

    @model_validator(mode="after")
    def backup_has_trace(self) -> "InvocationRequest":
        if (self.model_role == "BACKUP") != (self.parent_call_id is not None):
            raise ValueError("备用调用需关联主调用，主调用不能指定父调用")
        if self.parent_call_id == self.call_id:
            raise ValueError("调用不能自引用")
        return self


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    call_id: UUID
    source: Literal["MODEL", "RULES"]
    status: Literal["CANDIDATE", "UNAVAILABLE"]
    requires_review: Literal[True] = True


class ModelGateway(Protocol):
    """场景唯一入口；候选不包含执行连接器或审批通道。"""

    def generate(self, request: InvocationRequest) -> Candidate: ...


class ServerSecrets:
    """只由服务端容器注入；不按客户端传入的任意环境变量名读系统配置。"""

    def __init__(self, values: Mapping[str, SecretStr]) -> None:
        self._values = dict(values)

    def resolve(self, spec: ModelSpec) -> SecretStr:
        value = self._values.get(spec.secret_ref)
        if value is None or not value.get_secret_value():
            raise ValueError("模型服务端密钥未配置")
        return value
