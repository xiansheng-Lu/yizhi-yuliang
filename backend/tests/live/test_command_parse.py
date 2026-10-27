"""用户确认的28条真实解析回归；独立持久额度、无重试、无平台写入。"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import psycopg
from integration.test_ai_cost_gate import costs as costs
from integration.test_ai_ledger import ai_data as ai_data
from integration.test_deepseek_gateway import authorize, invocation_row, prepare
from integration.test_deepseek_gateway import gateway_data as gateway_data
from integration.test_identity_database import database as database
from integration.test_password_sessions import login_api as login_api
from integration.test_workspace import message
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.modules.aikernel.deepseek import DeepSeekPort
from yuliang.modules.aikernel.prompts.registry import CaseContext, Interpretation
from yuliang.modules.command.models import ParseInput
from yuliang.modules.command.parser import RULE_VERSION, guard_model
from yuliang.modules.command.service import CommandParser
from yuliang.modules.workspace.models import ReportingScope
from yuliang.modules.workspace.service import WorkspaceService
from yuliang.platform.db import business_transaction

ROOT = Path(__file__).parents[3]
RECHECK = os.environ.get("YULIANG_COMMAND_LIVE_RUN") == "recheck"
SUFFIX = "-recheck" if RECHECK else ""
INDICES = (3, 7, 25) if RECHECK else tuple(range(28))
MAX_CALLS = len(INDICES)
BUDGET = 10 if RECHECK else 100
ALLOWANCE = ROOT / f".runtime/command-parse-live-allowance{SUFFIX}.json"
RESULT = ROOT / f".runtime/command-parse-live-result{SUFFIX}.json"


def claim_call(index: int, minor: int) -> None:
    import fcntl

    # 新许可独立于历史6次验证；跨进程和重跑不重置已开始次数或未知费用预占。
    with ALLOWANCE.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        value = json.load(handle)
        assert value["approved"] is True and value["currency"] == "CNY"
        assert value["max_calls"] == MAX_CALLS and value["budget_minor"] <= BUDGET
        assert value["started_calls"] == index < value["max_calls"], "已用许可不能自动重放"
        assert minor == 3 and value["reserved_minor"] + minor <= value["budget_minor"]
        value["started_calls"] += 1
        value["reserved_minor"] += minor
        handle.seek(0)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())


def save_result(output: dict[str, Any]) -> None:
    allowance = json.loads(ALLOWANCE.read_text(encoding="utf-8"))
    output["started_calls"] = allowance["started_calls"]
    output["reserved_minor"] = allowance["reserved_minor"]
    RESULT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_m2_04_authorized_confirmed_cases(gateway_data: dict[str, Any]) -> None:
    assert os.environ.get("YULIANG_COMMAND_LIVE_APPROVED") == "1", "BLOCKED: 真实回归未显式启用"
    assert ALLOWANCE.is_file(), "BLOCKED: 缺本轮独立持久额度许可"
    allowance = json.loads(ALLOWANCE.read_text(encoding="utf-8"))
    assert allowance["approved"] is True and allowance["currency"] == "CNY"
    assert allowance["max_calls"] == MAX_CALLS and allowance["budget_minor"] <= BUDGET
    assert allowance["started_calls"] == 0 and allowance["reserved_minor"] == 0
    price = allowance["price_evidence"]
    assert price["url"] == "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
    assert price["verified_on"] == "2026-10-27"
    assert price["input_minor_per_million"] == 200
    assert price["output_minor_per_million"] == 800
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
    bundle = data["bundle"]
    bundle.require_human_labels()
    assert len(bundle.cases) == 28 and bundle.confirmation_source() == "USER"
    proof_hash = hashlib.sha256(json.dumps(price, sort_keys=True).encode()).hexdigest()
    with psycopg.connect(data["api"]["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "UPDATE ai_cost_budgets SET limit_minor=%s WHERE id=%s",
            [allowance["budget_minor"], data["budget"]],
        )
        control.execute(
            "UPDATE ai_price_versions SET proof_hash=%s WHERE tenant_id=%s AND kind='LLM'",
            [proof_hash, data["ledger"].identity.tenant_id],
        )
    output: dict[str, Any] = {
        "approval": allowance["approval"],
        "currency": "CNY",
        "budget_minor": allowance["budget_minor"],
        "max_calls": MAX_CALLS,
        "case_ids": [bundle.cases[index].id for index in INDICES],
        "run": "recheck_after_deterministic_resolution_fix" if RECHECK else "initial_baseline",
        "max_input_tokens": 8192,
        "max_output_tokens": 512,
        "requested_model": config["YULIANG_LLM_PRIMARY_MODEL"],
        "price_evidence": price,
        "price_proof_hash": proof_hash,
        "prompt_version": bundle.manifest.prompt_version,
        "rules_version": RULE_VERSION,
        "rules_sha256_utf8_lf": hashlib.sha256(
            (ROOT / "backend/src/yuliang/modules/command/parser.py")
            .read_text(encoding="utf-8")
            .encode()
        ).hexdigest(),
        "prompt_hash": bundle.manifest.template_sha256,
        "cases_hash": bundle.manifest.cases_sha256,
        "confirmation_source": bundle.confirmation_source(),
        "database": data["api"]["database"]["name"],
        "temporary_database_cleanup": "fixture_finalizer",
        "requests": [],
        "result": "IN_PROGRESS",
        "platform_write_calls": 0,
        "actual_bill_reconciled": False,
    }
    save_result(output)
    try:
        for index, case_index in enumerate(INDICES):
            case = bundle.cases[case_index]
            prepared = prepare(data, case_index)
            assert prepared.quote.max_cost_minor == 3
            port = DeepSeekPort(config["YULIANG_LLM_PRIMARY_ENDPOINT"])
            gateway = authorize(data, prepared, port, SecretStr(config["YULIANG_LLM_PRIMARY_KEY"]))
            claim_call(index, prepared.quote.max_cost_minor)
            item: dict[str, Any] = {
                "case_id": case.id,
                "call_id": str(prepared.request.call_id),
                "request_id": str(prepared.request.request_id),
                "quote_id": str(prepared.quote.quote_id),
                "permit_id": str(gateway.permit.permit_id),
                "quote_max_minor": prepared.quote.max_cost_minor,
                "expected": case.expected.semantic(),
            }
            output["requests"].append(item)
            started = time.monotonic()
            try:
                candidate = gateway.generate(prepared.request)
                item["latency_ms"] = round((time.monotonic() - started) * 1000)
                stored = invocation_row(data, prepared)
                guarded = guard_model(case.input, case.context, candidate.interpretation)
                item.update(
                    source=candidate.source,
                    status=candidate.status,
                    reason=candidate.reason,
                    upstream_status=candidate.upstream_status,
                    raw=candidate.interpretation.semantic(),
                    guarded=guarded.semantic(),
                    raw_matches_expected=(
                        candidate.interpretation.semantic() == case.expected.semantic()
                    ),
                    guarded_matches_expected=(guarded.semantic() == case.expected.semantic()),
                )
                for key in (
                    "provider_model",
                    "external_request_id",
                    "response_hash",
                    "usage_state",
                    "tokens_in",
                    "tokens_out",
                    "cost_state",
                    "cost_minor",
                ):
                    item[key] = stored[key]
                current = WorkspaceService(
                    data["ledger"].engine, data["ledger"].identity, prepared.request.request_id
                )
                context: CaseContext = case.context

                def synthetic_catalog(
                    session: Session, scope: ReportingScope, context: CaseContext = context
                ) -> CaseContext:
                    return context

                parser = CommandParser(current, catalog=synthetic_catalog)
                value = ParseInput.model_validate({"text": case.input, "scope": message()["scope"]})
                with current.transaction() as session:
                    persisted = parser.interpret(
                        session,
                        value,
                        proposed=candidate.interpretation,
                        call_id=prepared.request.call_id,
                        prepared=prepared,
                    )
                assert Interpretation.model_validate(persisted).semantic() == guarded.semantic()
                with business_transaction(current.engine, current.identity) as session:
                    trace = session.execute(
                        text("SELECT id,source,call_id FROM interpretations WHERE call_id=:call"),
                        {"call": prepared.request.call_id},
                    ).one()
                    item["interpretation_id"] = str(trace.id)
                    item["interpretation_source"] = trace.source
                    assert trace.source == "MODEL_GUARDED"
                    assert trace.call_id == prepared.request.call_id
                    assert (
                        session.execute(text("SELECT count(*) FROM command_jobs")).scalar_one() == 0
                    )
                assert item["latency_ms"] < 15000, "耗时超过15秒，停止且不重试"
                assert candidate.source == "MODEL", "模型通道不可用，停止且不重试"
            except Exception as error:
                # 只保留受控异常类型，绝不把原始供应商正文或本地配置写进证据。
                item["error_type"] = type(error).__name__
                item.setdefault("latency_ms", round((time.monotonic() - started) * 1000))
                output["result"] = "STOPPED_NO_RETRY"
                raise
            finally:
                save_result(output)
        raw_failed = [
            item["case_id"] for item in output["requests"] if not item["raw_matches_expected"]
        ]
        guarded_failed = [
            item["case_id"] for item in output["requests"] if not item["guarded_matches_expected"]
        ]
        output["raw_failed_ids"] = raw_failed
        output["guarded_failed_ids"] = guarded_failed
        output["raw_matched"] = MAX_CALLS - len(raw_failed)
        output["guarded_matched"] = MAX_CALLS - len(guarded_failed)
        output["result"] = "PASS_GUARDED" if not guarded_failed else "FAIL_GUARDED_SEMANTICS"
        assert not guarded_failed, "保留全部badcase，确定性守卫后的语义仍未匹配"
    except Exception as error:
        if output["result"] == "IN_PROGRESS":
            output["result"] = "STOPPED_NO_RETRY"
            output["error_type"] = type(error).__name__
        raise
    finally:
        save_result(output)
