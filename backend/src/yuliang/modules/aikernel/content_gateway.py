"""受报价/许可保护的内容JSON端口；无自动重试/切备/修复。"""

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from sqlalchemy.orm import Session

from yuliang.modules.aikernel.content_prompts import ContentPrompt
from yuliang.modules.aikernel.cost_gate import CostGate
from yuliang.modules.aikernel.cost_models import (
    BillableRequest,
    BillingOutcome,
    CallPermit,
    CostQuote,
)
from yuliang.modules.aikernel.ledger import InvocationLedger
from yuliang.modules.aikernel.models import Completion, InvocationRequest, Usage
from yuliang.modules.aikernel.prompts.registry import FrozenRoute
from yuliang.modules.content.ai_content_context import safe_text
from yuliang.modules.content.ai_content_models import ContentContext, Scenario, output_patch
from yuliang.platform.errors import ApiError, ErrorCode

MAX_INPUT = 8192
MAX_OUTPUT = 2048
MAX_RESPONSE = 65536


class ContentPrepared(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    request: InvocationRequest
    scenario: Scenario
    billable: BillableRequest
    quote: CostQuote
    route: FrozenRoute
    body: str = Field(repr=False)


@dataclass(frozen=True)
class ContentResult:
    completion: Completion
    patch: dict[str, Any] | None = field(default=None, repr=False)
    reason: str = "MODEL_UNAVAILABLE"


def prepare_content(
    ledger: InvocationLedger,
    gate: CostGate,
    prompt: ContentPrompt,
    request: InvocationRequest,
    context: ContentContext,
    *,
    session: Session | None = None,
) -> ContentPrepared:
    if (
        ledger.identity != gate.identity
        or request.scenario != "copywriting"
        or request.prompt_key != prompt.prompt_key
    ):
        raise ValueError("内容场景/调用身份不一致")
    safe_text(context.model_dump())
    snapshot = (
        ledger.begin_in_transaction(session, request)
        if session is not None
        else ledger.begin(request)
    )
    try:
        route = prompt.bind(snapshot["model_snapshot"])
        if route.selected.provider != "deepseek":
            raise ValueError("未接入该内容供应商")
        user = context.model_dump_json()
        if len(prompt.template.encode()) + len(user.encode()) + 256 > MAX_INPUT:
            raise ApiError(ErrorCode.INVALID_INPUT, "内容输入超过本次模型预算")
        body = json.dumps(
            dict(
                model=route.selected.model,
                messages=[
                    dict(role="system", content=prompt.template),
                    dict(role="user", content=user),
                ],
                thinking=dict(type="disabled"),
                max_tokens=MAX_OUTPUT,
                temperature=0,
                response_format=dict(type="json_object"),
                stream=False,
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        billable = BillableRequest(
            request_key=request.call_id,
            kind="LLM",
            scenario="copywriting",
            price_key=route.selected.model_key,
            input_units=MAX_INPUT,
            output_units=MAX_OUTPUT,
            payload_hash=hashlib.sha256(body.encode()).hexdigest(),
        )
        quote = (
            gate.quote_in_transaction(session, billable)
            if session is not None
            else gate.quote(billable)
        )
        return ContentPrepared(
            request=request,
            scenario=prompt.scenario,
            billable=billable,
            quote=quote,
            route=route,
            body=body,
        )
    except Exception:
        if session is None:
            ledger.finish(
                request, Completion(status="FALLBACK", dispatch_state="NOT_SENT", latency_ms=0)
            )
        raise


class ContentPort:
    def __init__(
        self,
        endpoint: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        allow_loopback: bool = False,
    ) -> None:
        parsed = urlparse(endpoint)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
        ):
            raise ValueError("内容模型源不合法")
        if parsed.scheme != "https" and not (
            allow_loopback
            and parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("内容模型源必须HTTPS或显式本地测试源")
        self.endpoint = endpoint.rstrip("/") + "/chat/completions"
        self.transport = transport

    def send(self, prepared: ContentPrepared, key: SecretStr) -> ContentResult:
        return asyncio.run(self._send(prepared, key))

    async def _send(self, prepared: ContentPrepared, key: SecretStr) -> ContentResult:
        started = time.monotonic()
        try:
            async with asyncio.timeout(8):
                async with httpx.AsyncClient(
                    transport=self.transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx.Timeout(8, connect=2),
                ) as client:
                    async with client.stream(
                        "POST",
                        self.endpoint,
                        content=prepared.body.encode(),
                        headers={
                            "Authorization": "Bearer " + key.get_secret_value(),
                            "Content-Type": "application/json",
                        },
                    ) as response:
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > MAX_RESPONSE:
                                return self._failed(started, "INVALID_RESPONSE", True)
                        if response.status_code != 200:
                            result = self._failed(
                                started,
                                "RATE_LIMITED" if response.status_code == 429 else "UPSTREAM_ERROR",
                                True,
                            )
                            return result
            return decode_content(bytes(data), prepared, int((time.monotonic() - started) * 1000))
        except (TimeoutError, httpx.TimeoutException):
            return self._failed(started, "TIMEOUT", False)
        except httpx.HTTPError:
            return self._failed(started, "UPSTREAM_ERROR", False)

    @staticmethod
    def _failed(started: float, reason: str, responded: bool) -> ContentResult:
        return ContentResult(
            Completion(
                status="TIMEOUT"
                if reason == "TIMEOUT"
                else "RATE_LIMITED"
                if reason == "RATE_LIMITED"
                else "FAILED",
                dispatch_state="RESPONDED" if responded else "UNKNOWN",
                latency_ms=min(int((time.monotonic() - started) * 1000), 2147483647),
            ),
            reason=reason,
        )


def decode_content(raw: bytes, prepared: ContentPrepared, latency: int) -> ContentResult:
    metadata: dict[str, Any] = dict(status="FAILED", dispatch_state="RESPONDED", latency_ms=latency)
    try:
        value = json.loads(raw)
        model = value.get("model")
        external = value.get("id")
        if not isinstance(model, str) or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,119}", model
        ):
            raise ValueError("model")
        metadata.update(provider_model=model, response_hash=hashlib.sha256(raw).hexdigest())
        if isinstance(external, str) and re.fullmatch(r"[a-zA-Z0-9._:/-]{1,128}", external):
            metadata["external_request_id"] = external
        reported = value.get("usage", {})
        input_count = reported.get("prompt_tokens")
        output_count = reported.get("completion_tokens")
        if (
            type(input_count) is int
            and type(output_count) is int
            and 0 <= input_count <= 9223372036854775807
            and 0 <= output_count <= 9223372036854775807
            and reported.get("total_tokens") == input_count + output_count
        ):
            metadata["usage"] = Usage(
                usage_state="KNOWN", tokens_in=input_count, tokens_out=output_count
            )
        choices = value.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("choices")
        choice = choices[0]
        message = choice.get("message", {})
        if (
            choice.get("finish_reason") != "stop"
            or message.get("tool_calls")
            or message.get("reasoning_content")
        ):
            raise ValueError("unsupported output")
        if (
            type(input_count) is int
            and input_count > MAX_INPUT
            or type(output_count) is int
            and output_count > MAX_OUTPUT
        ):
            raise ValueError("usage bound")
        content = json.loads(message["content"])
        safe_text(content, "candidate")
        patch = output_patch(prepared.scenario, content)
        metadata["status"] = "OK"
        return ContentResult(Completion(**metadata), patch, "MODEL_CANDIDATE")
    except (ValueError, TypeError, KeyError, AttributeError, ValidationError, ApiError):
        return ContentResult(Completion(**metadata), reason="INVALID_RESPONSE")


def generate_content(
    ledger: InvocationLedger,
    gate: CostGate,
    port: ContentPort,
    prepared: ContentPrepared,
    permit: CallPermit,
    key: SecretStr,
) -> ContentResult:
    if (
        ledger.identity != gate.identity
        or hashlib.sha256(prepared.body.encode()).hexdigest() != prepared.billable.payload_hash
    ):
        raise ApiError(ErrorCode.FORBIDDEN, "内容载荷/调用身份与费用许可不一致")
    try:
        result = gate.execute(
            permit, prepared.billable, lambda _: (port.send(prepared, key), BillingOutcome())
        )
    except ApiError as error:
        if error.code != ErrorCode.RESULT_UNKNOWN:
            raise
        result = ContentResult(
            Completion(status="FAILED", dispatch_state="UNKNOWN", latency_ms=0),
            reason="RESULT_UNKNOWN",
        )
    ledger.finish(prepared.request, result.completion)
    return result
