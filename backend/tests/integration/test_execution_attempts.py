"""真实PG发送准备、重投与最后发送门禁；供应商仅显式MockTransport。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.command.jobs import CommandJob, MoneyValue
from yuliang.modules.connectors.archive import ArchiveUnavailable, RecordedPlatformClient
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.http import OperationSpec, PlatformHttpClient
from yuliang.modules.execution.attempt_models import BusinessKind, BusinessRef
from yuliang.modules.execution.attempts import ExecutionOrders, PreparedExecution
from yuliang.modules.policy.repository import install_policy
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_budget_initialization import budget_ready as budget_ready
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
from .test_risk_rules import CanonicalWrite
from .test_sync_checkpoints import sync_ready as sync_ready
from .test_target_guards import approved

pytestmark = pytest.mark.integration


class SyntheticBudgetSerializer:
    """测试明示的规范DTO，绝不声明为已核验的ADQ真实写协议。"""

    def __init__(self, spec: OperationSpec) -> None:
        self.spec = replace(spec, parameters_model=CanonicalWrite)

    def render(
        self, ctx: GatewayContext, job: CommandJob, platform_key: UUID
    ) -> tuple[OperationSpec, dict[str, Any]]:
        assert isinstance(job.new_value, MoneyValue) and platform_key
        return self.spec, {
            "account_id": ctx.native_account_id,
            "object_id": "456",
            "currency": "CNY",
            "budget_minor": job.new_value.amount_minor,
        }


def prepare(data: dict[str, Any]) -> tuple[ExecutionOrders, PreparedExecution, CommandJob]:
    job = approved(data)
    service = ExecutionOrders(data["applications"], SyntheticBudgetSerializer(data["spec"]))
    ticket = service.prepare(
        BusinessRef(kind=BusinessKind.COMMAND_JOB, id=UUID(job.job_id)),
        data["ctx"],
        job.version,
        0,
        uuid4(),
    )
    return service, ticket, job


def counts(data: dict[str, Any]) -> tuple[int, int, int]:
    with business_transaction(data["engine"], data["identity"]) as session:
        return tuple(
            session.execute(text(query)).scalar_one()
            for query in (
                "SELECT count(*) FROM execution_orders",
                "SELECT count(*) FROM execution_attempts",
                "SELECT count(*) FROM platform_requests",
            )
        )


async def send(
    data: dict[str, Any],
    service: ExecutionOrders,
    prepared: PreparedExecution,
    *,
    hooks: bool = True,
    actual: dict[str, Any] | None = None,
) -> tuple[Any, list[str]]:
    ticket = prepared.attempt
    calls: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        # 从另一个连接看到SENT，证明发送前短事务已提交。
        with business_transaction(data["engine"], data["identity"]) as session:
            state = session.execute(
                text("SELECT state FROM execution_attempts WHERE id=:id"),
                {"id": ticket.attempt_id},
            ).scalar_one()
            assert state == "SENT"
        calls.append(request.method)
        return httpx.Response(200, json={"code": 0, "trace_id": "synthetic-execution"})

    assert prepared.spec is not None and prepared.parameters is not None
    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(upstream)),
        data["archive"],
        decoder=decoder,
    )
    result = await client.send(
        data["ctx"],
        prepared.spec,
        ticket.outbound_id,
        actual if actual is not None else prepared.parameters,
        SecretStr("synthetic"),
        target=data["target"],
        execution_id=ticket.execution_id,
        attempt_id=ticket.attempt_id,
        before_verify=service.before_verify(ticket) if hooks else None,
        after_admission=service.after_admission(
            ticket, data["ctx"], prepared.spec, prepared.parameters
        )
        if hooks
        else None,
    )
    return result, calls


async def test_sent_committed_before_wire_and_same_root_replay(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    service, prepared, job = prepare(data)
    ticket = prepared.attempt
    assert ticket.send_allowed and ticket.attempt_state == "SENT"
    assert ticket.guard.jobVersion == 4 and "dispatch_secret" not in ticket.model_dump()
    replay = service.prepare(ticket.business, data["ctx"], job.version, 0, uuid4())
    assert replay.attempt.attempt_id == ticket.attempt_id
    assert not replay.attempt.send_allowed and replay.spec is None and replay.parameters is None
    assert replay.attempt.dispatch_secret is None
    assert (
        ExecutionOrders(data["applications"]).lookup(ticket.business).attempt_id
        == ticket.attempt_id
    )
    first, calls = await send(data, service, prepared)
    second, repeated = await send(data, service, prepared)
    assert calls == ["POST"] and repeated == []
    assert first.metadata["externalOutcome"] == second.metadata["externalOutcome"] == "ACCEPTED"
    with business_transaction(data["engine"], data["identity"]) as session:
        root = (
            session.execute(
                text("SELECT * FROM platform_requests WHERE id=:id"), {"id": ticket.outbound_id}
            )
            .mappings()
            .one()
        )
        assert root["execution_id"] == ticket.execution_id
        assert root["execution_attempt_id"] == ticket.attempt_id
        assert (
            session.execute(
                text("SELECT dispatch_consumed_at FROM execution_attempts WHERE id=:id"),
                {"id": ticket.attempt_id},
            ).scalar_one()
            is not None
        )
    if evidence := os.getenv("YULIANG_ATTEMPT_EVIDENCE"):
        Path(evidence).write_text(
            json.dumps(
                {
                    **ticket.model_dump(mode="json"),
                    "mockHttpCalls": 1,
                    "physicalPlatformCalls": 0,
                    "paidCalls": 0,
                    "stageSetup": "SYNTHETIC_APPROVAL_AND_SERIALIZER_REAL_PG_KERNEL",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def test_four_deliveries_share_one_order_attempt_and_first_secret(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    job = approved(data)
    service = ExecutionOrders(data["applications"], SyntheticBudgetSerializer(data["spec"]))
    ref = BusinessRef(kind=BusinessKind.COMMAND_JOB, id=UUID(job.job_id))
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(
            pool.map(lambda _: service.prepare(ref, data["ctx"], job.version, 0, uuid4()), range(4))
        )
    assert sum(value.attempt.send_allowed for value in values) == 1
    assert len({value.attempt.attempt_id for value in values}) == 1
    assert counts(data)[:2] == (1, 1)


@pytest.mark.parametrize("mode", ["missing_hooks", "metadata_only", "different_wire"])
async def test_no_dispatch_without_original_admission_or_frozen_wire(
    budget_ready: dict[str, Any], mode: str
) -> None:
    data = budget_ready
    service, prepared, _ = prepare(data)
    if mode == "metadata_only":
        prepared = PreparedExecution(
            service.lookup(prepared.attempt.business), prepared.spec, prepared.parameters
        )
    actual = (
        dict(prepared.parameters or {}) | {"budget_minor": 111}
        if mode == "different_wire"
        else None
    )
    with pytest.raises((ArchiveUnavailable, ApiError)):
        await send(data, service, prepared, hooks=mode != "missing_hooks", actual=actual)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM platform_requests WHERE execution_id=:id"),
                {"id": prepared.attempt.execution_id},
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                text("SELECT dispatch_consumed_at FROM execution_attempts WHERE id=:id"),
                {"id": prepared.attempt.attempt_id},
            ).scalar_one()
            is None
        )


@pytest.mark.parametrize(
    "mutation",
    ["fence", "expired", "authorization", "policy", "capability", "old_value", "contract"],
)
async def test_changed_eligibility_stops_before_root(
    budget_ready: dict[str, Any], mutation: str
) -> None:
    data = budget_ready
    service, prepared, _ = prepare(data)
    commands = {
        "fence": "UPDATE target_execution_guards SET fence=fence+1",
        "expired": "UPDATE target_execution_guards "
        "SET lease_until=clock_timestamp()-interval '1 second'",
        "authorization": "UPDATE provider_authorizations SET version=version+1",
        "capability": "UPDATE capability_bindings SET active=false WHERE operation_kind='WRITE'",
        "old_value": "UPDATE sync_resource_mirrors SET projection="
        "jsonb_set(projection,'{data,budget_minor}','101'::jsonb)",
        "contract": "UPDATE resource_freshness_policies SET revision=revision+1",
    }
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if mutation == "policy":
            install_policy(
                owner,
                data["tenant"],
                policy(),
                expected_version=1,
                proof_ref="synthetic/M2-13a/policy-v2",
            )
        else:
            owner.execute(text(commands[mutation]))
    with pytest.raises((ArchiveUnavailable, ApiError)):
        await send(data, service, prepared)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM platform_requests WHERE execution_id=:id"),
                {"id": prepared.attempt.execution_id},
            ).scalar_one()
            == 0
        )


def test_non_command_source_does_not_create_fake_application(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    service = ExecutionOrders(data["applications"])
    with pytest.raises(ApiError) as raised:
        service.prepare(
            BusinessRef(kind=BusinessKind.PLAN_CHANGE, id=uuid4()), data["ctx"], 1, 0, uuid4()
        )
    assert raised.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
    assert counts(data)[:2] == (0, 0)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM command_jobs")).scalar_one() == 0


def test_unregistered_serializer_closed_without_claim(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    job = approved(data)
    with pytest.raises(ApiError) as raised:
        ExecutionOrders(data["applications"]).prepare(
            BusinessRef(kind=BusinessKind.COMMAND_JOB, id=UUID(job.job_id)),
            data["ctx"],
            job.version,
            0,
            uuid4(),
        )
    assert raised.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
    assert counts(data)[:2] == (0, 0)


def test_runtime_and_owner_cannot_overwrite_old_attempt_identity(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    _, prepared, _ = prepare(data)
    with (
        pytest.raises(DBAPIError),
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(
            text("UPDATE execution_attempts SET platform_request_key=:key WHERE id=:id"),
            {"key": uuid4(), "id": prepared.attempt.attempt_id},
        )
    with pytest.raises(DBAPIError), data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE execution_attempts SET platform_request_key=:key WHERE id=:id"),
            {"key": uuid4(), "id": prepared.attempt.attempt_id},
        )


def test_prepare_audit_failure_rolls_back_claim_and_sent(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = budget_ready
    job = approved(data)

    def failed(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr("yuliang.modules.execution.attempts.record", failed)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        ExecutionOrders(data["applications"], SyntheticBudgetSerializer(data["spec"])).prepare(
            BusinessRef(kind=BusinessKind.COMMAND_JOB, id=UUID(job.job_id)),
            data["ctx"],
            job.version,
            0,
            uuid4(),
        )
    assert counts(data)[:2] == (0, 0)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT status FROM command_jobs WHERE id=:id"), {"id": job.job_id}
            ).scalar_one()
            == "APPROVED"
        )
        assert (
            session.execute(
                text("SELECT count(*) FROM job_budget_holds WHERE state='HELD'")
            ).scalar_one()
            == 1
        )


async def test_lost_fence_after_root_commit_blocks_physical_http(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = budget_ready
    service, prepared, _ = prepare(data)
    original = data["archive"].append
    called: list[str] = []

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        if args[4] == "ATTEMPT":
            called.append("BEFORE_HTTP")
            with data["maintenance"].begin() as owner:
                owner.execute(text("SET LOCAL ROLE app_owner"))
                owner.execute(text("UPDATE target_execution_guards SET fence=fence+1"))
        return original(*args, **kwargs)

    monkeypatch.setattr(data["archive"], "append", interrupted)
    with pytest.raises(ArchiveUnavailable) as raised:
        await send(data, service, prepared)
    assert raised.value.dispatch == "NOT_SENT" and called == ["BEFORE_HTTP"]
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM platform_requests WHERE id=:id"),
                {"id": prepared.attempt.outbound_id},
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text("SELECT count(*) FROM platform_raw_payloads WHERE outbound_id=:id"),
                {"id": prepared.attempt.outbound_id},
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize("constraint", ["ordinal", "platform_key", "tenant"])
def test_attempt_uniqueness_and_tenant_foreign_keys(
    budget_ready: dict[str, Any], constraint: str
) -> None:
    data = budget_ready
    _, prepared, _ = prepare(data)
    with pytest.raises(DBAPIError), data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO execution_attempts
        SELECT (jsonb_populate_record(NULL::execution_attempts,to_jsonb(a)||
          jsonb_build_object('id',CAST(:id AS text),'outbound_id',CAST(:outbound AS text),
           'attempt_no',CAST(:ordinal AS integer),'platform_request_key',CAST(:key AS text),
           'tenant_id',CAST(:tenant AS bigint)))).*
        FROM execution_attempts a WHERE a.id=:original"""),
            {
                "id": uuid4(),
                "outbound": uuid4(),
                "ordinal": 1 if constraint == "ordinal" else 2,
                "key": prepared.attempt.platform_request_key
                if constraint == "platform_key"
                else uuid4(),
                "tenant": data["tenant"] + 9999 if constraint == "tenant" else data["tenant"],
                "original": prepared.attempt.attempt_id,
            },
        )
