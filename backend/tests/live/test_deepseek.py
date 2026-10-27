"""仅合成样例、受限真实调用；授权文件先持久扣减次数和预占，不自动重置或重跑。"""

import json
import os
import time
from pathlib import Path
from typing import Any

import psycopg
from integration.test_ai_cost_gate import costs as costs
from integration.test_ai_ledger import ai_data as ai_data
from integration.test_deepseek_gateway import (
    authorize,
    invocation_row,
    prepare,
)
from integration.test_deepseek_gateway import (
    gateway_data as gateway_data,
)
from integration.test_identity_database import database as database
from integration.test_password_sessions import login_api as login_api
from pydantic import SecretStr

from yuliang.modules.aikernel.deepseek import DeepSeekPort

ROOT = Path(__file__).parents[3]
ALLOWANCE = ROOT / ".runtime/llm-live-allowance.json"
RESULT = ROOT / ".runtime/llm-live-result.json"


def claim_call(index: int, minor: int) -> None:
    import fcntl

    # 同一授权跨进程/重跑仍按已持久次数扣减；失败也不自动退次数/额度。
    with ALLOWANCE.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        value = json.load(handle)
        assert value["approved"] is True and value["currency"] == "CNY"
        assert value["max_calls"] <= 6 and value["budget_minor"] <= 20
        assert value["started_calls"] == index < value["max_calls"], "不可自动重放已用调用"
        assert value["reserved_minor"] + minor <= value["budget_minor"]
        value["started_calls"] += 1
        value["reserved_minor"] += minor
        handle.seek(0)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())


def test_m2_01b_six_authorized_synthetic_samples(gateway_data: dict[str, Any]) -> None:
    assert os.environ.get("YULIANG_LLM_LIVE_APPROVED") == "1", "BLOCKED: 需独立本地额度许可"
    assert ALLOWANCE.is_file(), "BLOCKED: 缺可核对授权文件"
    config = dict(
        line.split("=", 1)
        for line in (ROOT / "backend/deployment/.env.llm.local")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#")
    )
    assert config["YULIANG_LLM_PRIMARY_MODEL"] == "deepseek-v4-flash"
    assert config["YULIANG_LLM_PRIMARY_ENDPOINT"] == "https://api.deepseek.com"
    data = gateway_data
    output: dict[str, Any] = {
        "approval": "human_reply_2026-10-27_CNY_0.20_max6",
        "currency": "CNY",
        "budget_minor": 20,
        "max_calls": 6,
        "requested_model": "deepseek-v4-flash",
        "requests": [],
        "price_source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
        "input_cny_per_million": 2,
        "output_cny_per_million": 8,
        "database": data["api"]["database"]["name"],
        "temporary_database_cleanup": "fixture_finalizer",
    }
    # 此前的离线夹具价格仅合成；此处显式登记已核对官方高峰人民币价来源摘要。
    import hashlib

    price_proof = hashlib.sha256(
        json.dumps(
            {
                "url": output["price_source"],
                "verified_on": "2026-10-27",
                "currency": "CNY",
                "input_minor_per_million": 200,
                "output_minor_per_million": 800,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    with psycopg.connect(data["api"]["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "UPDATE ai_price_versions SET proof_hash=%s WHERE tenant_id=%s AND kind='LLM'",
            [price_proof, data["ledger"].identity.tenant_id],
        )
    try:
        for index, case_index in enumerate((0, 1, 2, 8, 10, 19)):
            prepared = prepare(data, case_index)
            assert prepared.quote.max_cost_minor == 3
            port = DeepSeekPort(config["YULIANG_LLM_PRIMARY_ENDPOINT"])
            gateway = authorize(data, prepared, port, SecretStr(config["YULIANG_LLM_PRIMARY_KEY"]))
            claim_call(index, prepared.quote.max_cost_minor)
            started = time.monotonic()
            candidate = gateway.generate(prepared.request)
            latency = round((time.monotonic() - started) * 1000)
            stored = invocation_row(data, prepared)
            item = {
                "case_id": data["bundle"].cases[case_index].id,
                "call_id": str(prepared.request.call_id),
                "request_id": str(prepared.request.request_id),
                "quote_id": str(prepared.quote.quote_id),
                "permit_id": str(gateway.permit.permit_id),
                "quote_max_minor": prepared.quote.max_cost_minor,
                "latency_ms": latency,
                "source": candidate.source,
                "status": candidate.status,
                "reason": candidate.reason,
                "upstream_status": candidate.upstream_status,
                "kind": candidate.interpretation.kind,
                "semantic_matches_candidate_expectation": (
                    candidate.interpretation.semantic()
                    == data["bundle"].cases[case_index].expected.semantic()
                ),
                "provider_model": stored["provider_model"],
                "external_request_id": stored["external_request_id"],
                "response_hash": stored["response_hash"],
                "usage_state": stored["usage_state"],
                "tokens_in": stored["tokens_in"],
                "tokens_out": stored["tokens_out"],
                "cost_state": stored["cost_state"],
                "cost_minor": stored["cost_minor"],
            }
            output["requests"].append(item)
            RESULT.write_text(
                json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            assert latency < 15000, "真实耗时超过客户端契约，需异步或调整方案"
            assert candidate.source == "MODEL", "真实通道不可用，停止后续调用，不重试"
        output["result"] = "PASS_CONTROLLED_STRUCTURE_ONLY"
    finally:
        output["started_calls"] = json.loads(ALLOWANCE.read_text(encoding="utf-8"))["started_calls"]
        output["reserved_minor"] = json.loads(ALLOWANCE.read_text(encoding="utf-8"))[
            "reserved_minor"
        ]
        RESULT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
