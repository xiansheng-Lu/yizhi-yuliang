"""真实PG规则与规范E2导入；预算读合同由维护身份布置，生产初始化归M2-10。"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
import pytest
import redis
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.command.applications import JobApplications
from yuliang.modules.command.jobs import CommandJob, MoneyValue
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.outcomes import BusinessVerdict
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.policy.action_facts import record_verified_action
from yuliang.modules.policy.repository import install_policy
from yuliang.platform.db import VerifiedIdentity, business_transaction

from .test_capability_evidence import capability as capability
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_platform_payloads import decoder, signer
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import policy
from .test_risk_framework import risk_ready as risk_ready
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


def application(data: dict[str, Any], amount: int = 110) -> CommandJob:
    repository: JobApplications = data["applications"]
    return repository.create(
        data["input"].model_copy(
            update={"new_value": MoneyValue(amount_minor=amount), "idempotency_key": uuid4().hex}
        )
    )


def values(job: CommandJob) -> dict[str, bool]:
    return {check.code: check.passed for check in job.risk_summary.checks}


def ledger(data: dict[str, Any], *, total: int = 9990, reserved: int = 0) -> UUID:
    """明确合成的预算组件输入：100活动+9890暂停；不假称已执行初始化生产者。"""
    identity = uuid4()
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO budget_ledgers(id,tenant_id,policy_id,policy_version,
        bucket_key,period_start,period_end,currency,limit_minor,confirmed_total_minor,
        reserved_increase_minor,data_state,coverage_kind,covered_account_ids,source_ref,
        fetched_at,valid_until) VALUES(:id,:tenant,:policy,1,'TENANT_DAILY',
        (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date,
        (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date+1,'CNY',10000,:total,:reserved,
        'CONFIRMED','ALL_CONFIGURED_INCLUDING_PAUSED',:accounts,
        'synthetic/rule-input/including-paused',clock_timestamp(),
        clock_timestamp()+interval '2 minutes')"""),
            {
                "id": identity,
                "tenant": data["tenant"],
                "policy": data["policy_id"],
                "total": total,
                "reserved": reserved,
                "accounts": [data["ctx"].account_id],
            },
        )
    return identity


class CanonicalWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    object_id: str
    currency: Literal["CNY"]
    budget_minor: int = Field(strict=True)


class CanonicalRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    object_id: str


async def receipts(
    data: dict[str, Any], job: CommandJob, *, mode: str = "matched"
) -> tuple[UUID, UUID]:
    """已有留档端口的真实PG记录，供应商响应通过MockTransport显式合成。"""
    from dataclasses import replace

    write, read = uuid4(), uuid4()
    expected = job.new_value
    assert isinstance(expected, MoneyValue)

    async def upstream(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0})
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "id": "other" if mode == "wrong_target" else "456",
                    "budget_minor": expected.amount_minor + 1
                    if mode == "mismatch"
                    else expected.amount_minor,
                    "currency": "BEAN" if mode == "wrong_currency" else "CNY",
                    **(
                        {}
                        if mode == "unknown_time"
                        else {"source_updated_at": datetime.now(UTC).isoformat()}
                    ),
                },
            },
        )

    safe = ArchivePolicy(ArchivePolicy().fields | {"source_updated_at"})
    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(upstream)),
        data["archive"],
        policy=safe,
        decoder=(lambda response: BusinessVerdict()) if mode == "unconfirmed" else decoder,
    )
    write_spec = replace(data["spec"], parameters_model=CanonicalWrite)
    read_spec = replace(data["readspec"], parameters_model=CanonicalRead, cursor_parameter=None)
    await client.send(
        data["ctx"],
        write_spec,
        write,
        {
            "account_id": data["ctx"].native_account_id,
            "object_id": "456",
            "currency": "CNY",
            "budget_minor": expected.amount_minor + 1
            if mode == "wrong_intent"
            else expected.amount_minor,
        },
        SecretStr("synthetic"),
        target=data["target"],
    )
    await client.send(
        data["readctx"],
        read_spec,
        read,
        {
            "account_id": data["readctx"].native_account_id,
            "object_id": "456",
        },
        SecretStr("synthetic"),
        target=data["target"],
    )
    return write, read


def test_rule_amount_boundary_includes_paused_basis(risk_ready: dict[str, Any]) -> None:
    data = risk_ready
    assert not values(application(data))["AMOUNT_LIMIT"]
    ledger(data)
    exact = application(data)
    assert values(exact)["AMOUNT_LIMIT"] and values(exact)["CHANGE_BOUND"]
    extra = application(data, 111)
    assert not values(extra)["AMOUNT_LIMIT"] and not values(extra)["CHANGE_BOUND"]
    assert not values(exact)["INTENT_BINDING"] and exact.status.value == "BLOCKED"


@pytest.mark.parametrize(
    "fault", ["old_period", "stale", "no_paused_coverage", "BEAN", "missing_component"]
)
def test_missing_or_inconsistent_budget_contract_denied(
    risk_ready: dict[str, Any], fault: str
) -> None:
    data = risk_ready
    key = ledger(data)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        query = {
            "old_period": (
                "UPDATE budget_ledgers SET period_start=period_start-1,"
                "period_end=period_end-1 WHERE id=:id"
            ),
            "stale": (
                "UPDATE budget_ledgers SET fetched_at=fetched_at-interval '1 hour',"
                "valid_until=valid_until-interval '1 hour' WHERE id=:id"
            ),
            "no_paused_coverage": (
                "UPDATE budget_ledgers SET data_state='UNINITIALIZED',"
                "coverage_kind='UNKNOWN' WHERE id=:id"
            ),
            "BEAN": "UPDATE budget_ledgers SET currency='BEAN' WHERE id=:id",
            "missing_component": "UPDATE budget_ledgers SET confirmed_total_minor=0 WHERE id=:id",
        }[fault]
        owner.execute(text(query), {"id": key})
    assert not values(application(data))["AMOUNT_LIMIT"]


async def test_success_fact_requires_real_archive_pair_and_is_idempotent(
    risk_ready: dict[str, Any],
) -> None:
    data = risk_ready
    job = application(data)
    write, read = await receipts(data, job)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        fact = record_verified_action(
            owner, UUID(job.job_id), write, read, "synthetic/M2-09a/matched"
        )
        assert (
            record_verified_action(owner, UUID(job.job_id), write, read, "synthetic/M2-09a/matched")
            == fact
        )
    blocked = application(data)
    assert not values(blocked)["FREQUENCY"]
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM verified_action_facts")).scalar_one() == 1
    reviewer = VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"])
    with business_transaction(data["engine"], reviewer) as session:
        assert session.execute(text("SELECT count(*) FROM verified_action_facts")).scalar_one() == 0
    if evidence := os.getenv("YULIANG_RULE_EVIDENCE"):
        Path(evidence).write_text(
            json.dumps(
                {
                    "layer": "REAL_PG_SYNTHETIC_PLATFORM_RESPONSES",
                    "jobId": job.job_id,
                    "writeOutboundId": str(write),
                    "readOutboundId": str(read),
                    "verifiedFactId": str(fact),
                    "nextJobFrequencyPassed": values(blocked)["FREQUENCY"],
                    "jobStatus": job.status.value,
                    "platformCalls": 0,
                    "paidCalls": 0,
                    "productionBudgetInitialization": "NOT_RUN_M2_10",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


@pytest.mark.parametrize(
    "mode",
    ["mismatch", "wrong_currency", "wrong_target", "unknown_time", "unconfirmed", "wrong_intent"],
)
async def test_http_200_or_wrong_semantics_not_success_fact(
    risk_ready: dict[str, Any], mode: str
) -> None:
    data = risk_ready
    job = application(data)
    write, read = await receipts(data, job, mode=mode)
    with pytest.raises(ValueError), data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        record_verified_action(owner, UUID(job.job_id), write, read, "synthetic/M2-09a/reject")
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM verified_action_facts")).scalar_one() == 0
    assert not values(application(data))["FREQUENCY"]  # ACK/UNKNOWN尚未定性，不按0次解锁。


async def test_redis_read_failure_does_not_clear_pg_cooldown(
    risk_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = risk_ready
    job = application(data)
    write, read = await receipts(data, job)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        record_verified_action(owner, UUID(job.job_id), write, read, "synthetic/M2-09a/cooldown")

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise redis.ConnectionError("injected cache read failure")

    monkeypatch.setattr(redis.Redis, "get", unavailable)
    assert not values(application(data))["FREQUENCY"]


async def test_day_count_exact_boundary_from_pg_facts(risk_ready: dict[str, Any]) -> None:
    data = risk_ready
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        install_policy(
            owner,
            data["tenant"],
            policy().model_copy(update={"cooldown_minutes": 0, "max_changes_per_day": 2}),
            expected_version=1,
            proof_ref="synthetic/M2-09a/count-policy",
        )
    for index in range(2):
        job = application(data)
        assert values(job)["FREQUENCY"]  # 第2次后恰好达到2，仍可提交本次。
        write, read = await receipts(data, job)
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            record_verified_action(
                owner, UUID(job.job_id), write, read, f"synthetic/M2-09a/day-{index}"
            )
    assert not values(application(data))["FREQUENCY"]


async def test_runtime_cannot_forge_or_delete_verified_facts(risk_ready: dict[str, Any]) -> None:
    data = risk_ready
    job = application(data)
    write, read = await receipts(data, job)
    with pytest.raises(ValueError), data["engine"].begin() as runtime:
        record_verified_action(runtime, UUID(job.job_id), write, read, "synthetic/forbidden")
    for query in (
        "DELETE FROM verified_action_facts",
        "UPDATE budget_ledgers SET reserved_increase_minor=0",
    ):
        with (
            pytest.raises(DBAPIError),
            business_transaction(data["engine"], data["identity"]) as session,
        ):
            session.execute(text(query))
