"""内容专用输出、PII边界及不重试JSON端口；无真实模型调用。"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from yuliang.modules.aikernel.content_gateway import ContentPort, ContentPrepared, decode_content
from yuliang.modules.aikernel.content_prompts import load_content_prompt
from yuliang.modules.aikernel.cost_models import BillableRequest, CostQuote
from yuliang.modules.aikernel.models import InvocationRequest, ModelSpec
from yuliang.modules.aikernel.prompts.registry import FrozenRoute
from yuliang.modules.content.ai_content_context import context_from, safe_text
from yuliang.modules.content.ai_content_models import ContentContext, Scenario, output_patch
from yuliang.modules.content.models import Document
from yuliang.platform.errors import ApiError


def value() -> dict[str, Any]:
    return dict(
        id=str(uuid4()),
        kind="planning",
        title="草稿",
        productId="",
        parentId=None,
        version=1,
        updatedAt="2026-10-28T00:00:00Z",
        status="draft",
        brief={},
        body="",
        scenes=[],
        boards=[],
        nodes=[],
        edges=[],
        clips=[],
        prompt="",
        settings=dict(ratio="9:16", duration=15, resolution="1080P", skill=""),
    )


def prepared(scenario: Scenario = "content_planning") -> ContentPrepared:
    prompt = load_content_prompt(scenario)
    call = uuid4()
    spec = ModelSpec(
        model_key="synthetic.content",
        provider="deepseek",
        model="synthetic-model",
        endpoint_ref="SYNTHETIC_ENDPOINT",
        secret_ref="SYNTHETIC_KEY",
        billing_currency="CNY",
    )
    request = InvocationRequest(
        call_id=call,
        request_id=uuid4(),
        scenario="copywriting",
        capability_alias="content.copywriting",
        prompt_key=scenario,
        prompt_version=1,
    )
    return ContentPrepared(
        request=request,
        scenario=scenario,
        billable=BillableRequest(
            request_key=call,
            kind="LLM",
            scenario="copywriting",
            price_key=spec.model_key,
            input_units=8192,
            output_units=2048,
            payload_hash="a" * 64,
        ),
        quote=CostQuote(
            quote_id=uuid4(),
            request_key=call,
            max_cost_minor=1,
            currency="CNY",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            quote_hash="b" * 64,
        ),
        route=FrozenRoute(
            config_id=uuid4(),
            revision=1,
            primary=spec,
            backup=None,
            selected=spec,
            prompt_hash=prompt.digest,
            output_schema_ref=prompt.schema,
        ),
        body="{}",
    )


def response(output: dict[str, Any]) -> dict[str, Any]:
    return dict(
        id="synthetic-reply",
        model="synthetic-model",
        usage=dict(prompt_tokens=20, completion_tokens=10, total_tokens=30),
        choices=[dict(finish_reason="stop", message=dict(content=json.dumps(output)))],
    )


@pytest.mark.parametrize(
    "scenario,output",
    [
        ("content_planning", {"body": "策划候选"}),
        ("content_script", {"body": "脚本候选"}),
        (
            "content_storyboard",
            {"scenes": [dict(title="镜头", narration="旁白", visual="画面", duration=3.0)]},
        ),
    ],
)
def test_distinct_content_schemas_and_server_scene_ids(
    scenario: Scenario, output: dict[str, Any]
) -> None:
    result = decode_content(json.dumps(response(output)).encode(), prepared(scenario), 1)
    assert result.completion.status == "OK" and result.reason == "MODEL_CANDIDATE"
    assert result.patch is not None
    if scenario == "content_storyboard":
        assert result.patch["scenes"][0]["id"]
    assert load_content_prompt(scenario).prompt_key != "command_parse"


@pytest.mark.parametrize(
    "text",
    [
        "联系人13812345678",
        "a@example.invalid",
        "110101199001011234",
        "access_token=not-a-real-secret",
        "Bearer synthetic-secret",
        "api_key: synthetic",
        "aVeryLongSyntheticTokenThatMustNeverLeaveThisMachine12345",
        "收货地址：合成私密地址",
    ],
)
def test_sensitive_free_text_never_serialized_to_model(text: str) -> None:
    document = Document.model_validate(value() | dict(body=text))
    with pytest.raises(ApiError) as error:
        context_from(document, [])
    assert text not in json.dumps(error.value.details, ensure_ascii=False)


def test_context_only_declared_fields_and_operator_source() -> None:
    document = Document.model_validate(value())
    context = context_from(document, [dict(kind="text", name="授权说明")]).model_dump()
    assert set(context) == {"brief", "body", "assets", "productSource"}
    assert "productId" not in json.dumps(context) and context["productSource"] == "OPERATOR_BRIEF"
    with pytest.raises(ValidationError):
        ContentContext.model_validate(context | {"cookie": "hidden"})


@pytest.mark.parametrize("mutation", ["identity", "tool", "thinking", "truncated", "usage", "pii"])
def test_invalid_or_sensitive_model_output_has_no_candidate(mutation: str) -> None:
    raw = response({"body": "候选"})
    if mutation == "identity":
        raw = response({"body": "候选", "productId": "another"})
    if mutation == "tool":
        raw["choices"][0]["message"]["tool_calls"] = [{"name": "execute"}]
    if mutation == "thinking":
        raw["choices"][0]["message"]["reasoning_content"] = "private thoughts"
    if mutation == "truncated":
        raw["choices"][0]["finish_reason"] = "length"
    if mutation == "usage":
        raw["usage"] = dict(prompt_tokens=99999, completion_tokens=10, total_tokens=100009)
    if mutation == "pii":
        raw = response({"body": "联系电话13812345678"})
    result = decode_content(json.dumps(raw).encode(), prepared(), 1)
    assert result.patch is None and result.reason == "INVALID_RESPONSE"


@pytest.mark.parametrize("status", [302, 429, 503])
def test_http_failures_no_retry_or_redirect(status: int) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://untrusted.invalid"})

    result = ContentPort(
        "http://127.0.0.1:9999", allow_loopback=True, transport=httpx.MockTransport(handle)
    ).send(prepared(), SecretStr("synthetic-only"))
    assert len(calls) == 1 and result.patch is None
    assert result.completion.dispatch_state == "RESPONDED"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://remote.invalid",
        "http://127.0.0.2",
        "https://user:password@host.invalid",
        "https://host.invalid/?key=bad",
    ],
)
def test_model_source_cannot_be_arbitrary_insecure_url(endpoint: str) -> None:
    with pytest.raises(ValueError):
        ContentPort(endpoint)


def test_model_cannot_supply_scene_ids_or_unbounded_output() -> None:
    with pytest.raises(ValidationError):
        output_patch(
            "content_storyboard",
            dict(
                scenes=[dict(id=str(uuid4()), title="x", narration="x", visual="x", duration=1.0)]
            ),
        )
    with pytest.raises(ApiError):
        safe_text({"body": "cookie: synthetic"})
