"""真实PG费用/调用账本配合明确HTTP夹具；不访问DeepSeek或产生付费。"""

import asyncio
import json
import time
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.modules.aikernel.deepseek import (
    AuthorizedGateway,
    DeepSeekPort,
    PreparedCall,
    prepare_call,
)
from yuliang.modules.aikernel.models import InvocationRequest, ModelGateway, ServerSecrets
from yuliang.modules.aikernel.prompts import load_bundle
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError

from .test_ai_cost_gate import budget
from .test_ai_cost_gate import costs as costs
from .test_ai_ledger import ai_data as ai_data
from .test_identity_database import database as database
from .test_password_sessions import login_api as login_api


@pytest.fixture
def gateway_data(costs: dict[str, Any]) -> dict[str, Any]:
    bundle = load_bundle()
    ledger = costs["ledger"]
    spec = costs["primary"].model_copy(
        update={
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
        }
    )
    with psycopg.connect(costs["api"]["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "INSERT INTO model_configs(tenant_id,capability_alias,revision,primary_spec) "
            "VALUES(%s,'command.parse',1,%s::jsonb)",
            [ledger.identity.tenant_id, spec.model_dump_json()],
        )
        control.execute(
            "INSERT INTO prompt_versions(tenant_id,prompt_key,version,capability_alias,template,"
            "content_hash,output_schema_ref,status,created_by) "
            "VALUES(%s,'command_parse',1,'command.parse',%s,%s,%s,'ACTIVE',%s)",
            [
                ledger.identity.tenant_id,
                bundle.template,
                bundle.manifest.template_sha256,
                bundle.manifest.output_schema_ref,
                ledger.identity.user_id,
            ],
        )
        control.execute(
            "UPDATE ai_cost_budgets SET scenario='command_parse',limit_minor=20 WHERE id=%s",
            [costs["budget"]],
        )
        control.execute(
            "UPDATE ai_price_versions SET unit_scale=1000000,input_rate_micro_minor=200000000,"
            "output_rate_micro_minor=800000000 WHERE tenant_id=%s AND kind='LLM'",
            [ledger.identity.tenant_id],
        )
    costs["bundle"] = bundle
    return costs


def prepare(data: dict[str, Any], case_index: int = 0) -> PreparedCall:
    case = data["bundle"].cases[case_index]
    invocation = InvocationRequest(
        call_id=uuid4(),
        request_id=uuid4(),
        scenario="command_parse",
        capability_alias="command.parse",
        prompt_key="command_parse",
        prompt_version=1,
    )
    return prepare_call(
        data["ledger"], data["gate"], data["bundle"], invocation, case.input, case.context
    )


def authorize(
    data: dict[str, Any],
    prepared: PreparedCall,
    port: DeepSeekPort,
    key: SecretStr | None = None,
) -> AuthorizedGateway:
    gate = data["gate"]
    gate.confirm(prepared.quote)
    current = gate.acquire(prepared.quote)
    return AuthorizedGateway(
        data["ledger"],
        gate,
        port,
        ServerSecrets({"PRIMARY_KEY": key or SecretStr("synthetic-key")}),
        prepared,
        current,
    )


def response_body(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "synthetic-response-001",
        "model": "deepseek-flash",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": data["bundle"].cases[0].expected.model_dump_json(),
                },
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
    }


def invocation_row(data: dict[str, Any], prepared: PreparedCall) -> dict[str, Any]:
    ledger = data["ledger"]
    with business_transaction(ledger.engine, ledger.identity) as session:
        return dict(
            session.execute(
                text("SELECT * FROM ai_invocations WHERE call_id=:id"),
                {"id": prepared.request.call_id},
            )
            .mappings()
            .one()
        )


def test_m2_01b_protocol_route_usage_cost_and_one_request(gateway_data: dict[str, Any]) -> None:
    data = gateway_data
    prepared = prepare(data)
    seen: list[str] = []
    private_key = SecretStr("synthetic-private-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer " + private_key.get_secret_value()
        body = json.loads(request.content)
        assert body["max_tokens"] == 512 and body["thinking"] == {"type": "disabled"}
        assert not body["stream"] and "tools" not in body
        assert body["model"] == "deepseek-v4-flash"
        assert len(body["user_id"]) == 64
        seen.append(request.headers["x-request-id"])
        return httpx.Response(200, json=response_body(data))

    port = DeepSeekPort("https://api.deepseek.com", transport=httpx.MockTransport(handler))
    gateway = authorize(data, prepared, port, private_key)
    protocol: ModelGateway = gateway
    result = protocol.generate(prepared.request)
    assert result.source == "MODEL" and result.status == "CANDIDATE" and result.requires_review
    assert len(seen) == 1 and seen[0] == str(prepared.request.call_id)
    assert prepared.quote.max_cost_minor == 3
    row = invocation_row(data, prepared)
    assert row["status"] == "OK" and row["tokens_in"] == 100 and row["tokens_out"] == 30
    assert row["provider_model"] == "deepseek-flash" and len(row["response_hash"]) == 64
    assert row["model_key"] == "example.primary" and row["cost_state"] == "UNKNOWN"
    assert row["cost_minor"] is None and budget(data) == (3, 0)
    assert private_key.get_secret_value() not in json.dumps(row, default=str)
    with pytest.raises(ApiError):
        gateway.generate(prepared.request)
    assert len(seen) == 1


@pytest.mark.parametrize(
    "problem",
    [
        "rate_limit",
        "server_error",
        "redirect",
        "malformed",
        "wrong_units",
        "outside_context",
        "approval",
        "truncated",
        "tool",
        "unexpected_thinking",
        "over_limit_usage",
    ],
)
def test_m2_01b_bad_provider_response_falls_back_without_retry(
    gateway_data: dict[str, Any],
    problem: str,
) -> None:
    data = gateway_data
    prepared = prepare(data)
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        status = {"rate_limit": 429, "server_error": 503, "redirect": 302}.get(problem, 200)
        if problem == "malformed":
            return httpx.Response(200, text="private raw provider diagnostic")
        body = response_body(data)
        message = body["choices"][0]["message"]
        candidate = json.loads(message["content"])
        if problem == "wrong_units":
            candidate["new_value"] = {"kind": "RATIO", "value": 5000}
        elif problem == "outside_context":
            candidate["candidate_ids"] = [str(uuid4())]
        elif problem == "approval":
            candidate["approval"] = "APPROVED"
        elif problem == "truncated":
            body["choices"][0]["finish_reason"] = "length"
        elif problem == "tool":
            message["tool_calls"] = [{"function": {"name": "execute"}}]
        elif problem == "unexpected_thinking":
            message["reasoning_content"] = "unexpected thinking"
        elif problem == "over_limit_usage":
            body["usage"] = {"prompt_tokens": 9000, "completion_tokens": 30, "total_tokens": 9030}
        message["content"] = json.dumps(candidate)
        return httpx.Response(status, json=body, headers={"Location": "https://untrusted.invalid"})

    port = DeepSeekPort("https://api.deepseek.com", transport=httpx.MockTransport(handler))
    gateway = authorize(data, prepared, port)
    result = gateway.generate(prepared.request)
    assert result.source == "RULES" and result.status == "UNAVAILABLE"
    assert result.interpretation.kind == "UNRECOGNIZED" and len(seen) == 1
    assert "private raw" not in result.model_dump_json()
    row = invocation_row(data, prepared)
    assert row["status"] == ("RATE_LIMITED" if problem == "rate_limit" else "FAILED")
    assert row["cost_state"] == "UNKNOWN" and budget(data) == (3, 0)


def test_m2_01b_total_network_timeout_returns_fast(gateway_data: dict[str, Any]) -> None:
    data = gateway_data
    prepared = prepare(data)
    seen: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        await asyncio.sleep(1)
        return httpx.Response(200, json=response_body(data))

    port = DeepSeekPort(
        "https://api.deepseek.com",
        timeout_seconds=0.02,
        transport=httpx.MockTransport(handler),
    )
    gateway = authorize(data, prepared, port)
    started = time.monotonic()
    result = gateway.generate(prepared.request)
    assert time.monotonic() - started < 1
    assert result.reason == "TIMEOUT" and result.source == "RULES" and len(seen) == 1
    assert invocation_row(data, prepared)["status"] == "TIMEOUT" and budget(data) == (3, 0)


def test_m2_01b_budget_denial_is_before_http(gateway_data: dict[str, Any]) -> None:
    data = gateway_data
    prepared = prepare(data)
    with psycopg.connect(data["api"]["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute("UPDATE ai_cost_budgets SET limit_minor=0 WHERE id=%s", [data["budget"]])
    data["gate"].confirm(prepared.quote)
    with pytest.raises(ApiError):
        data["gate"].acquire(prepared.quote)
    assert budget(data) == (0, 0)


def test_m2_01b_endpoint_and_timeout_are_server_bounded() -> None:
    for endpoint in (
        "http://api.deepseek.com",
        "https://api.deepseek.com.evil.invalid",
        "https://127.0.0.1",
        "https://api.deepseek.com@evil.invalid",
    ):
        with pytest.raises(ValueError):
            DeepSeekPort(endpoint)
    with pytest.raises(ValueError):
        DeepSeekPort("https://api.deepseek.com", timeout_seconds=15)
