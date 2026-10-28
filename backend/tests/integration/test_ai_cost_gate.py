"""真实PG的费用许可/并发/未知核对；价格和回执均为合成夹具，不访问或计费供应商。"""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.aikernel.cost_gate import CostGate
from yuliang.modules.aikernel.cost_models import BillableRequest, BillingOutcome, CallPermit
from yuliang.modules.aikernel.models import Completion
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_ai_ledger import ai_data as ai_data
from .test_identity_database import database as database
from .test_password_sessions import login_api as login_api

PROOF = hashlib.sha256(b"synthetic price and receipt; no provider billing").hexdigest()


@pytest.fixture
def costs(ai_data: dict[str, Any]) -> dict[str, Any]:
    ledger = ai_data["ledger"]
    ai_data["gate"] = CostGate(ledger.engine, ledger.identity)
    with psycopg.connect(ai_data["api"]["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        row = control.execute(
            "INSERT INTO ai_cost_budgets(tenant_id,scenario,currency,period_start,period_end,"
            "limit_minor,status) VALUES(%s,'copywriting','CNY',now()-interval '1 day',"
            "now()+interval '1 day',7,'ACTIVE') RETURNING id",
            [ledger.identity.tenant_id],
        ).fetchone()
        assert row
        ai_data["budget"] = row[0]
        for kind, key, unit in (
            ("LLM", "example.primary", "TOKEN"),
            ("GENERATION", "example.generator", "JOB"),
        ):
            control.execute(
                "INSERT INTO ai_price_versions(tenant_id,price_key,revision,kind,currency,unit,"
                "unit_scale,input_rate_micro_minor,output_rate_micro_minor,valid_from,"
                "valid_until,verification,proof_hash) VALUES(%s,%s,1,%s,'CNY',%s,1,1000000,"
                "2000000,now()-interval '1 day',now()+interval '1 day','VERIFIED',%s)",
                [ledger.identity.tenant_id, key, kind, unit, PROOF],
            )
    return ai_data


def request(data: dict[str, Any], *, generation: bool = False) -> BillableRequest:
    invocation = data["request"].model_copy(update={"call_id": uuid4()})
    if not generation:
        data["ledger"].begin(invocation)
    data["last_invocation"] = invocation
    return BillableRequest(
        request_key=invocation.call_id,
        kind="GENERATION" if generation else "LLM",
        scenario="copywriting",
        price_key="example.generator" if generation else "example.primary",
        input_units=3,
        output_units=2,
        payload_hash=PROOF,
    )


def permit(data: dict[str, Any], req: BillableRequest) -> CallPermit:
    gate: CostGate = data["gate"]
    quote = gate.quote(req)
    gate.confirm(quote)
    return gate.acquire(quote)


def budget(data: dict[str, Any]) -> tuple[int, int]:
    gate: CostGate = data["gate"]
    with business_transaction(gate.engine, gate.identity) as session:
        row = session.execute(
            text("SELECT reserved_minor,settled_minor FROM ai_cost_budgets WHERE id=:id"),
            {"id": data["budget"]},
        ).one()
        return int(row[0]), int(row[1])


def record(data: dict[str, Any], current: CallPermit) -> dict[str, Any]:
    gate: CostGate = data["gate"]
    with business_transaction(gate.engine, gate.identity) as session:
        return dict(
            session.execute(
                text("SELECT * FROM ai_cost_permits WHERE id=:id"),
                {"id": current.permit_id},
            )
            .mappings()
            .one()
        )


def receipt(amount: int = 5, currency: str = "CNY") -> BillingOutcome:
    return BillingOutcome.model_validate(
        {"state": "KNOWN", "amount_minor": amount, "currency": currency, "proof_hash": PROOF}
    )


def test_fee_transaction_participants_rollback_reservation_dispatch_and_settlement(
    costs: dict[str, Any],
) -> None:
    gate: CostGate = costs["gate"]
    req = request(costs, generation=True)
    with pytest.raises(RuntimeError, match="quote rollback"):
        with business_transaction(gate.engine, gate.identity) as session:
            quote = gate.quote_in_transaction(session, req)
            gate.confirm_in_transaction(session, quote)
            raise RuntimeError("quote rollback")
    with business_transaction(gate.engine, gate.identity) as session:
        assert session.execute(
            text("SELECT count(*) FROM ai_cost_quotes WHERE request_key=:key"),
            dict(key=req.request_key),
        ).scalar_one() == 0
    quote = gate.quote(req)
    gate.confirm(quote)
    with pytest.raises(RuntimeError, match="dispatch rollback"):
        with business_transaction(gate.engine, gate.identity) as session:
            current = gate.acquire_in_transaction(session, quote)
            gate.start_in_transaction(session, current, req)
            raise RuntimeError("dispatch rollback")
    assert budget(costs) == (0, 0)
    with business_transaction(gate.engine, gate.identity) as session:
        assert session.execute(
            text("SELECT count(*) FROM ai_cost_permits WHERE request_key=:key"),
            dict(key=req.request_key),
        ).scalar_one() == 0
        current = gate.acquire_in_transaction(session, quote)
        gate.start_in_transaction(session, current, req)
    assert record(costs, current)["state"] == "IN_FLIGHT" and budget(costs) == (7, 0)
    with pytest.raises(RuntimeError, match="settlement rollback"):
        with business_transaction(gate.engine, gate.identity) as session:
            assert gate.finish_in_transaction(session, current, receipt()) == "SETTLED"
            raise RuntimeError("settlement rollback")
    assert record(costs, current)["state"] == "IN_FLIGHT" and budget(costs) == (7, 0)
    assert gate.finish(current, receipt()) == "SETTLED"
    assert budget(costs) == (0, 5)


def test_m5_02a_confirmation_exact_price_and_unsent_cancel(costs: dict[str, Any]) -> None:
    gate: CostGate = costs["gate"]
    req = request(costs)
    quote = gate.quote(req)
    assert quote.max_cost_minor == 7 and quote.currency == "CNY"
    with pytest.raises(DBAPIError):
        gate.acquire(quote)
    assert budget(costs) == (0, 0)
    for updates in ({"quote_hash": "a" * 64}, {"max_cost_minor": 8}, {"currency": "USD"}):
        with pytest.raises(ApiError):
            gate.confirm(quote.model_copy(update=updates))
    # 直接SQL的NULL同样不能绕过Python校验或确认。
    with business_transaction(gate.engine, gate.identity) as session:
        assert not session.execute(
            text("SELECT access.ai_fee_confirm(:member,:quote,NULL,NULL,NULL)"),
            {"member": gate.identity.membership_id, "quote": quote.quote_id},
        ).scalar_one()
    gate.confirm(quote)
    gate.confirm(quote)
    current = gate.acquire(quote)
    assert budget(costs) == (7, 0)
    row = record(costs, current)
    assert row["request_key"] == req.request_key and row["state"] == "RESERVED"
    assert row["token_hash"] != current.token.get_secret_value()
    assert current.token.get_secret_value() not in repr(current)
    assert gate.cancel_unsent(current)
    assert not gate.cancel_unsent(current)
    assert budget(costs) == (0, 0)
    with pytest.raises(ApiError):
        gate.start(current, req)


@pytest.mark.parametrize(
    "problem",
    [
        "draft",
        "revoked",
        "expired_price",
        "missing_budget",
        "overlapping_budget",
        "ledger_mismatch",
    ],
)
def test_m5_02a_missing_verified_input_never_quotes(costs: dict[str, Any], problem: str) -> None:
    req = request(costs)
    with psycopg.connect(costs["api"]["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        tenant = costs["gate"].identity.tenant_id
        if problem in ("draft", "revoked"):
            control.execute(
                "UPDATE ai_price_versions SET verification=%s WHERE tenant_id=%s",
                [problem.upper(), tenant],
            )
        elif problem == "expired_price":
            control.execute(
                "UPDATE ai_price_versions SET valid_until=now()-interval '1 second' "
                "WHERE tenant_id=%s",
                [tenant],
            )
        elif problem == "missing_budget":
            control.execute(
                "UPDATE ai_cost_budgets SET status='FROZEN' WHERE tenant_id=%s", [tenant]
            )
        elif problem == "overlapping_budget":
            control.execute(
                "INSERT INTO ai_cost_budgets(tenant_id,scenario,currency,period_start,period_end,"
                "limit_minor,status) VALUES(%s,'copywriting','CNY',now()-interval '2 days',"
                "now()+interval '1 day',7,'ACTIVE')",
                [tenant],
            )
        else:
            req = req.model_copy(update={"request_key": uuid4()})
    with pytest.raises(DBAPIError):
        costs["gate"].quote(req)
    assert budget(costs) == (0, 0)


def test_m5_02a_integer_quote_rounds_up_subminor_units(costs: dict[str, Any]) -> None:
    with psycopg.connect(costs["api"]["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "UPDATE ai_price_versions SET unit_scale=1000000,input_rate_micro_minor=1,"
            "output_rate_micro_minor=1 WHERE tenant_id=%s",
            [costs["gate"].identity.tenant_id],
        )
    quote = costs["gate"].quote(request(costs))
    assert quote.max_cost_minor == 1


def test_m5_02a_concurrent_budget_allows_one_outbound(costs: dict[str, Any]) -> None:
    gate: CostGate = costs["gate"]
    requests = [request(costs), request(costs)]
    quotes = [gate.quote(req) for req in requests]
    for quote in quotes:
        gate.confirm(quote)
    barrier = Barrier(2)
    called: list[str] = []

    def competing(index: int) -> str:
        barrier.wait(timeout=5)
        try:
            current = gate.acquire(quotes[index])
        except ApiError as error:
            assert error.code == ErrorCode.RISK_BLOCKED
            return "BLOCKED"

        def callback(key: str) -> tuple[str, BillingOutcome]:
            # 不同连接可见已提交IN_FLIGHT，供应商回调不持有预算锁。
            assert record(costs, current)["state"] == "IN_FLIGHT"
            assert budget(costs) == (7, 0)
            called.append(key)
            return "candidate", BillingOutcome()

        return gate.execute(current, requests[index], callback)

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(competing, range(2)))
    assert sorted(results) == ["BLOCKED", "candidate"]
    assert len(called) == 1 and called[0] in {str(req.request_key) for req in requests}
    assert budget(costs) == (7, 0)


def test_m5_02a_duplicate_request_or_start_never_double_calls(costs: dict[str, Any]) -> None:
    gate: CostGate = costs["gate"]
    req = request(costs)
    current = permit(costs, req)
    # 即使预算充足，同一请求另取报价也不能再领许可。
    with psycopg.connect(costs["api"]["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute("UPDATE ai_cost_budgets SET limit_minor=100 WHERE id=%s", [costs["budget"]])
    duplicate = gate.quote(req)
    gate.confirm(duplicate)
    with pytest.raises(DBAPIError):
        gate.acquire(duplicate)
    assert budget(costs) == (7, 0)
    barrier = Barrier(2)

    def start_once(_: int) -> bool:
        barrier.wait(timeout=5)
        try:
            gate.start(current, req)
            return True
        except ApiError:
            return False

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(start_once, range(2))) == [False, True]
    assert record(costs, current)["state"] == "IN_FLIGHT"


@pytest.mark.parametrize(
    "problem",
    [
        "changed_payload",
        "changed_units",
        "wrong_token",
        "null_fingerprint",
        "expired_quote",
        "revoked_price",
        "frozen_budget",
        "reduced_budget",
        "revoked_member",
        "terminal_ledger",
    ],
)
def test_m5_02a_last_gate_revalidates_without_dispatch(costs: dict[str, Any], problem: str) -> None:
    gate: CostGate = costs["gate"]
    req = request(costs)
    current = permit(costs, req)
    if problem == "changed_payload":
        req = req.model_copy(update={"payload_hash": "a" * 64})
    elif problem == "changed_units":
        req = req.model_copy(update={"output_units": 99})
    elif problem == "wrong_token":
        current = current.model_copy(update={"token": SecretStr("x" * 32)})
    elif problem == "null_fingerprint":
        with business_transaction(gate.engine, gate.identity) as session:
            assert not session.execute(
                text("SELECT access.ai_fee_start(:member,:permit,:token,NULL)"),
                {
                    "member": gate.identity.membership_id,
                    "permit": current.permit_id,
                    "token": record(costs, current)["token_hash"],
                },
            ).scalar_one()
        return
    elif problem == "terminal_ledger":
        costs["ledger"].finish(costs["last_invocation"], Completion(status="TIMEOUT", latency_ms=1))
    else:
        with psycopg.connect(costs["api"]["database"]["migration"]) as control:
            control.execute("SET LOCAL ROLE app_owner")
            if problem == "expired_quote":
                control.execute(
                    "UPDATE ai_cost_quotes SET expires_at=now()-interval '1 second' "
                    "WHERE request_key=%s",
                    [req.request_key],
                )
            elif problem == "revoked_price":
                control.execute(
                    "UPDATE ai_price_versions SET verification='REVOKED' WHERE tenant_id=%s",
                    [gate.identity.tenant_id],
                )
            elif problem == "frozen_budget":
                control.execute(
                    "UPDATE ai_cost_budgets SET status='FROZEN' WHERE id=%s", [costs["budget"]]
                )
            elif problem == "reduced_budget":
                control.execute(
                    "UPDATE ai_cost_budgets SET limit_minor=1 WHERE id=%s", [costs["budget"]]
                )
            else:
                control.execute(
                    "UPDATE memberships SET status='REMOVED' WHERE id=%s",
                    [gate.identity.membership_id],
                )
    called: list[str] = []

    def forbidden(key: str) -> tuple[str, BillingOutcome]:
        called.append(key)
        return "unexpected", BillingOutcome()

    with pytest.raises(ApiError):
        gate.execute(current, req, forbidden)
    assert not called and budget(costs) == (7, 0)


def test_m5_02a_timeout_unknown_keeps_hold_and_late_receipt_once(costs: dict[str, Any]) -> None:
    gate: CostGate = costs["gate"]
    req = request(costs)
    current = permit(costs, req)
    called: list[str] = []

    def timeout(key: str) -> tuple[str, BillingOutcome]:
        called.append(key)
        raise TimeoutError("private provider diagnostic must not escape")

    with pytest.raises(ApiError) as failure:
        gate.execute(current, req, timeout)
    assert failure.value.code == ErrorCode.RESULT_UNKNOWN
    assert "private" not in str(failure.value)
    assert record(costs, current)["state"] == "UNKNOWN" and budget(costs) == (7, 0)
    assert not gate.cancel_unsent(current)
    with pytest.raises(ApiError):
        gate.execute(current, req, timeout)
    assert len(called) == 1
    # 身份停用不丢失已经产生的费用事实；只能结算此actor持有的许可。
    with psycopg.connect(costs["api"]["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "UPDATE memberships SET status='REMOVED' WHERE id=%s", [gate.identity.membership_id]
        )
    barrier = Barrier(2)

    def settle_once(_: int) -> str:
        barrier.wait(timeout=5)
        return gate.finish(current, receipt())

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(settle_once, range(2))) == ["SETTLED", "SETTLED"]
    assert budget(costs) == (0, 5)
    with pytest.raises(DBAPIError):
        gate.finish(current, receipt(6))
    assert budget(costs) == (0, 5)


def test_m5_02a_crashed_inflight_expiry_never_refunds(costs: dict[str, Any]) -> None:
    gate: CostGate = costs["gate"]
    req = request(costs)
    current = permit(costs, req)
    gate.start(current, req)  # 模拟提交后进程消失，未调用finish。
    with psycopg.connect(costs["api"]["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            "UPDATE ai_cost_quotes SET expires_at=now()-interval '1 second' WHERE request_key=%s",
            [req.request_key],
        )
    assert not gate.cancel_unsent(current)
    assert budget(costs) == (7, 0) and record(costs, current)["state"] == "IN_FLIGHT"
    assert gate.finish(current, BillingOutcome()) == "UNKNOWN"
    assert gate.finish(current, receipt(0)) == "SETTLED"  # 明确零元也必须有回执。
    assert budget(costs) == (0, 0)


def test_m5_02a_currency_mismatch_hold_and_overage_blocks_next(costs: dict[str, Any]) -> None:
    gate: CostGate = costs["gate"]
    req = request(costs, generation=True)
    current = permit(costs, req)
    gate.start(current, req)
    assert gate.finish(current, receipt(5, "USD")) == "UNKNOWN"
    assert gate.finish(current, receipt(5, "USD")) == "UNKNOWN"
    row = record(costs, current)
    assert row["reported_cost_minor"] == 5 and row["reported_currency"] == "USD"
    assert row["settled_cost_minor"] is None and row["anomaly"] == "CURRENCY_MISMATCH"
    assert budget(costs) == (7, 0) and not gate.cancel_unsent(current)
    # 如实记录供应商超额账单，不伪称报价是实际扣费上限；后续许可被阻止。
    assert gate.finish(current, receipt(9)) == "SETTLED"
    assert budget(costs) == (0, 9) and record(costs, current)["anomaly"] == "OVERAGE"
    next_quote = gate.quote(request(costs, generation=True))
    gate.confirm(next_quote)
    with pytest.raises(ApiError):
        gate.acquire(next_quote)
    with business_transaction(gate.engine, gate.identity) as session:
        events = (
            session.execute(
                text("SELECT event FROM ai_cost_events WHERE permit_id=:id ORDER BY id"),
                {"id": current.permit_id},
            )
            .scalars()
            .all()
        )
        assert events == ["RESERVED", "IN_FLIGHT", "CURRENCY_MISMATCH", "OVERAGE"]
        facts = session.execute(
            text(
                "SELECT reported_cost_minor,reported_currency FROM ai_cost_events "
                "WHERE permit_id=:id AND proof_hash IS NOT NULL ORDER BY id"
            ),
            {"id": current.permit_id},
        ).all()
        assert [tuple(row) for row in facts] == [(5, "USD"), (9, "CNY")]


def test_m5_02a_null_billing_currency_cannot_settle(costs: dict[str, Any]) -> None:
    gate: CostGate = costs["gate"]
    req = request(costs)
    current = permit(costs, req)
    gate.start(current, req)
    with pytest.raises(DBAPIError), business_transaction(gate.engine, gate.identity) as session:
        session.execute(
            text("SELECT access.ai_fee_finish(:permit,:token,'KNOWN',0,NULL,:proof)"),
            {
                "permit": current.permit_id,
                "token": record(costs, current)["token_hash"],
                "proof": PROOF,
            },
        )
    assert budget(costs) == (7, 0)


def test_m5_02a_private_rls_and_no_runtime_writes(costs: dict[str, Any]) -> None:
    gate: CostGate = costs["gate"]
    req = request(costs)
    current = permit(costs, req)
    for identity in (
        VerifiedIdentity(gate.identity.tenant_id, gate.identity.membership_id, uuid4()),
        VerifiedIdentity(gate.identity.tenant_id + 10000, uuid4(), gate.identity.user_id),
    ):
        other = CostGate(gate.engine, identity)
        with business_transaction(gate.engine, identity) as session:
            assert session.execute(text("SELECT count(*) FROM ai_cost_permits")).scalar_one() == 0
            assert session.execute(text("SELECT count(*) FROM ai_cost_quotes")).scalar_one() == 0
            assert session.execute(text("SELECT count(*) FROM ai_cost_events")).scalar_one() == 0
        with pytest.raises(ApiError):
            other.start(current, req)
        with pytest.raises(DBAPIError):
            other.finish(current, receipt())
        assert not other.cancel_unsent(current)
    for table in (
        "ai_cost_budgets",
        "ai_price_versions",
        "ai_cost_quotes",
        "ai_cost_permits",
        "ai_cost_events",
    ):
        with pytest.raises(DBAPIError), business_transaction(gate.engine, gate.identity) as session:
            session.execute(text(f"DELETE FROM {table}"))  # noqa: S608 -- 上方固定表名白名单。
    assert budget(costs) == (7, 0)


def test_m5_02a_billing_write_failure_keeps_unknown(costs: dict[str, Any]) -> None:
    gate: CostGate = costs["gate"]
    req = request(costs)
    current = permit(costs, req)
    with psycopg.connect(costs["api"]["database"]["migration"]) as control:
        control.execute("SET LOCAL ROLE app_owner")
        control.execute(
            sql.SQL(
                "ALTER TABLE ai_cost_permits ADD CONSTRAINT fixture_billing_failure "
                "CHECK(id<>{} OR state<>'SETTLED')"
            ).format(sql.Literal(current.permit_id))
        )
    called: list[str] = []

    def callback(key: str) -> tuple[str, BillingOutcome]:
        called.append(key)
        return "candidate", receipt()

    try:
        with pytest.raises(ApiError) as failure:
            gate.execute(current, req, callback)
        assert failure.value.code == ErrorCode.RESULT_UNKNOWN
        assert budget(costs) == (7, 0) and record(costs, current)["state"] == "UNKNOWN"
        assert len(called) == 1
    finally:
        with psycopg.connect(costs["api"]["database"]["migration"]) as control:
            control.execute("SET LOCAL ROLE app_owner")
            control.execute("ALTER TABLE ai_cost_permits DROP CONSTRAINT fixture_billing_failure")
    assert gate.finish(current, receipt()) == "SETTLED"
    assert budget(costs) == (0, 5)
