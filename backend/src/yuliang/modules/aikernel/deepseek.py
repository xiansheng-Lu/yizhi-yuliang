"""显式注入的DeepSeek非流式端口；许可前置，无工具/思考/自动重试或隐式切备。"""

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypedDict

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from yuliang.modules.aikernel.cost_gate import CostGate
from yuliang.modules.aikernel.cost_models import (
    BillableRequest,
    BillingOutcome,
    CallPermit,
    CostQuote,
)
from yuliang.modules.aikernel.ledger import InvocationLedger
from yuliang.modules.aikernel.models import (
    Candidate,
    Completion,
    InvocationRequest,
    ServerSecrets,
    Usage,
)
from yuliang.modules.aikernel.prompts import PromptBundle
from yuliang.modules.aikernel.prompts.registry import (
    CaseContext,
    FrozenRoute,
    Interpretation,
    validate_context,
)
from yuliang.platform.errors import ApiError, ErrorCode

MAX_INPUT = 8192
MAX_OUTPUT = 512
MAX_RESPONSE_BYTES = 65536


class PreparedCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    request: InvocationRequest
    billable: BillableRequest
    quote: CostQuote
    route: FrozenRoute
    body: str = Field(repr=False)
    context: CaseContext


class StructuredCandidate(Candidate):
    interpretation: Interpretation
    upstream_status: int | None = None
    reason: Literal[
        "MODEL_CANDIDATE",
        "TIMEOUT",
        "RATE_LIMITED",
        "UPSTREAM_ERROR",
        "INVALID_RESPONSE",
        "COST_BLOCKED",
        "RESULT_UNKNOWN",
    ]


@dataclass(frozen=True)
class ProviderResult:
    status: Literal["OK", "FAILED", "TIMEOUT", "RATE_LIMITED"]
    reason: Literal[
        "MODEL_CANDIDATE", "TIMEOUT", "RATE_LIMITED", "UPSTREAM_ERROR", "INVALID_RESPONSE"
    ]
    dispatch: Literal["UNKNOWN", "RESPONDED"]
    interpretation: Interpretation | None = None
    usage: Usage = field(default_factory=Usage)
    external_request_id: str | None = None
    provider_model: str | None = None
    response_hash: str | None = None
    http_status: int | None = None


class CompletionPort(Protocol):
    def send(self, prepared: PreparedCall, key: SecretStr, request_key: str) -> ProviderResult: ...


class ProviderMetadata(TypedDict):
    usage: Usage
    external_request_id: str | None
    provider_model: str | None
    response_hash: str | None


class DeepSeekPort:
    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 8,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # 服务端显式白名单；不接受客户端URL，不跨重定向发送Authorization。
        if endpoint.rstrip("/") not in ("https://api.deepseek.com", "https://api.deepseek.com/v1"):
            raise ValueError("未授权模型出口")
        if not 0 < timeout_seconds <= 8:
            raise ValueError("模型总网络预算最多8秒")
        self.endpoint = endpoint.rstrip("/") + "/chat/completions"
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def send(self, prepared: PreparedCall, key: SecretStr, request_key: str) -> ProviderResult:
        return asyncio.run(self._send(prepared, key, request_key))

    async def _send(
        self, prepared: PreparedCall, key: SecretStr, request_key: str
    ) -> ProviderResult:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with httpx.AsyncClient(
                    trust_env=False,
                    follow_redirects=False,
                    transport=self.transport,
                    timeout=httpx.Timeout(self.timeout_seconds, connect=2),
                ) as client:
                    async with client.stream(
                        "POST",
                        self.endpoint,
                        content=prepared.body.encode(),
                        headers={
                            "Authorization": "Bearer " + key.get_secret_value(),
                            "Content-Type": "application/json",
                            "X-Request-ID": request_key,
                        },
                    ) as response:
                        if response.status_code != 200:
                            rate = response.status_code == 429
                            return ProviderResult(
                                status="RATE_LIMITED" if rate else "FAILED",
                                reason="RATE_LIMITED" if rate else "UPSTREAM_ERROR",
                                dispatch="RESPONDED",
                                http_status=response.status_code,
                            )
                        chunks = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(chunks) + len(chunk) > MAX_RESPONSE_BYTES:
                                return ProviderResult("FAILED", "INVALID_RESPONSE", "RESPONDED")
                            chunks.extend(chunk)
                        return decode_response(bytes(chunks), prepared)
        except (TimeoutError, httpx.TimeoutException):
            return ProviderResult("TIMEOUT", "TIMEOUT", "UNKNOWN")
        except httpx.HTTPError:
            return ProviderResult("FAILED", "UPSTREAM_ERROR", "UNKNOWN")


def decode_response(raw: bytes, prepared: PreparedCall) -> ProviderResult:
    try:
        data = json.loads(raw)
        actual_model = data.get("model")
        external = data.get("id")
        model = (
            actual_model
            if isinstance(actual_model, str)
            and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}", actual_model)
            else None
        )
        external = (
            external
            if isinstance(external, str) and re.fullmatch(r"[a-zA-Z0-9._:/-]{1,128}", external)
            else None
        )
        response_hash = hashlib.sha256(raw).hexdigest() if model else None
        usage = Usage()
        reported = data.get("usage", {})
        input_count, output_count = reported.get("prompt_tokens"), reported.get("completion_tokens")
        if (
            type(input_count) is int
            and type(output_count) is int
            and 0 <= input_count <= 9223372036854775807
            and 0 <= output_count <= 9223372036854775807
            and reported.get("total_tokens") == input_count + output_count
        ):
            usage = Usage(usage_state="KNOWN", tokens_in=input_count, tokens_out=output_count)
        metadata: ProviderMetadata = {
            "usage": usage,
            "external_request_id": external,
            "provider_model": model,
            "response_hash": response_hash,
        }
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or model is None:
            return ProviderResult("FAILED", "INVALID_RESPONSE", "RESPONDED", **metadata)
        choice = choices[0]
        message = choice.get("message", {})
        if (
            choice.get("finish_reason") != "stop"
            or message.get("tool_calls")
            or message.get("reasoning_content")
            or (
                usage.usage_state == "KNOWN"
                and (
                    input_count > prepared.billable.input_units
                    or output_count > prepared.billable.output_units
                )
            )
        ):
            return ProviderResult("FAILED", "INVALID_RESPONSE", "RESPONDED", **metadata)
        try:
            parsed = Interpretation.model_validate_json(message["content"])
            validate_context(parsed, prepared.context)
        except (ValueError, KeyError, TypeError):
            return ProviderResult("FAILED", "INVALID_RESPONSE", "RESPONDED", **metadata)
        return ProviderResult(
            "OK",
            "MODEL_CANDIDATE",
            "RESPONDED",
            interpretation=parsed,
            **metadata,
        )
    except (ValueError, TypeError, AttributeError):
        return ProviderResult("FAILED", "INVALID_RESPONSE", "RESPONDED")


def prepare_call(
    ledger: InvocationLedger,
    gate: CostGate,
    bundle: PromptBundle,
    request: InvocationRequest,
    statement: str,
    context: CaseContext,
) -> PreparedCall:
    if ledger.identity != gate.identity or request.scenario != "command_parse":
        raise ValueError("解析网关身份/场景不一致")
    if not statement.strip() or len(statement.encode("utf-16-le")) > 1000:
        raise ValueError("指令长度不合法")
    # 先提交账本，再固定路由；原始输入仅进入受控内存请求，不写调用账本。
    snapshot = ledger.begin(request)
    try:
        route = bundle.bind_route(snapshot["model_snapshot"])
        if route.selected.provider != "deepseek":
            raise ValueError("缺少对应供应商适配")
        user = json.dumps(
            {"statement": statement, "context": context.model_dump(mode="json")},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        # 字节级保守输入界限加固定消息开销；实际token以服务商回报为准并检查越界。
        if len(bundle.template.encode()) + len(user.encode()) + 256 > MAX_INPUT:
            raise ValueError("解析上下文超过本次输入预算")
        body = json.dumps(
            {
                "model": route.selected.model,
                "messages": [
                    {"role": "system", "content": bundle.template},
                    {"role": "user", "content": user},
                ],
                "thinking": {"type": "disabled"},
                "max_tokens": MAX_OUTPUT,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "stream": False,
                "user_id": hashlib.sha256(
                    f"{ledger.identity.tenant_id}:{ledger.identity.user_id}".encode()
                ).hexdigest(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        billable = BillableRequest(
            request_key=request.call_id,
            kind="LLM",
            scenario=request.scenario,
            price_key=route.selected.model_key,
            input_units=MAX_INPUT,
            output_units=MAX_OUTPUT,
            payload_hash=hashlib.sha256(body.encode()).hexdigest(),
        )
        quote = gate.quote(billable)
        return PreparedCall(
            request=request, billable=billable, quote=quote, route=route, body=body, context=context
        )
    except Exception:
        ledger.finish(
            request, Completion(status="FALLBACK", dispatch_state="NOT_SENT", latency_ms=0)
        )
        raise


class AuthorizedGateway:
    """符合ModelGateway；构造时显式提供已确认许可，不隐式确认或发起额外调用。"""

    def __init__(
        self,
        ledger: InvocationLedger,
        gate: CostGate,
        port: CompletionPort,
        secrets: ServerSecrets,
        prepared: PreparedCall,
        permit: CallPermit,
    ) -> None:
        if ledger.identity != gate.identity:
            raise ValueError("费用与模型账本身份不一致")
        if hashlib.sha256(prepared.body.encode()).hexdigest() != prepared.billable.payload_hash:
            raise ValueError("请求载荷摘要不一致")
        self.ledger, self.gate, self.port = ledger, gate, port
        self.secrets, self.prepared, self.permit = secrets, prepared, permit

    def generate(self, request: InvocationRequest) -> StructuredCandidate:
        if request != self.prepared.request:
            raise ApiError(ErrorCode.FORBIDDEN, "费用许可与调用请求不符")
        # 密钥缺失在许可开始前拒绝，不把未发请求记作已发送。
        key = self.secrets.resolve(self.prepared.route.selected)
        started = time.monotonic()

        def call(request_key: str) -> tuple[ProviderResult, BillingOutcome]:
            return self.port.send(self.prepared, key, request_key), BillingOutcome()

        try:
            result = self.gate.execute(self.permit, self.prepared.billable, call)
        except ApiError as error:
            if error.code != ErrorCode.RESULT_UNKNOWN:
                raise
            result = ProviderResult("FAILED", "UPSTREAM_ERROR", "UNKNOWN")
        latency = min(int((time.monotonic() - started) * 1000), 2147483647)
        self.ledger.finish(
            request,
            Completion(
                status=result.status,
                dispatch_state=result.dispatch,
                usage=result.usage,
                latency_ms=latency,
                external_request_id=result.external_request_id,
                provider_model=result.provider_model,
                response_hash=result.response_hash,
            ),
        )
        available = result.interpretation is not None
        return StructuredCandidate(
            call_id=request.call_id,
            source="MODEL" if available else "RULES",
            status="CANDIDATE" if available else "UNAVAILABLE",
            interpretation=result.interpretation
            or Interpretation(
                kind="UNRECOGNIZED",
                text="模型暂不可用，请明确对象和单一动作后稍后重试；尚未执行操作。",
            ),
            reason=result.reason,
            upstream_status=result.http_status,
        )
