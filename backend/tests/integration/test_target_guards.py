"""真实PG目标竞争/状态条件；上游、策略与前置审批阶段明确为合成技术输入。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import redis
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.capability.service import register_binding
from yuliang.modules.catalog.service import link_authorization
from yuliang.modules.command.applications import JobApplications, register_value_source
from yuliang.modules.command.jobs import CommandJob
from yuliang.modules.command.state import JobEvent, StateFacts, evolve
from yuliang.modules.execution.guards import GuardLease, TargetGuards, controlled
from yuliang.modules.policy.repository import install_policy
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_budget_initialization import budget_ready as budget_ready
from .test_budget_initialization import configure as configure_budget
from .test_budget_initialization import full_snapshot
from .test_budget_reservations import hold
from .test_capability_evidence import capability as capability
from .test_capability_evidence import populate
from .test_identity_database import database as database
from .test_intent_binding import stage_only
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import configure, snapshot
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import policy
from .test_risk_framework import risk_ready as risk_ready
from .test_risk_rules import application
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


def approved(data: dict[str, Any]) -> Any:
    data["budget_service"].refresh(data.get("guardPolicyVersion", 1))
    draft = application(data)
    hold(data, draft)
    return stage_only(data, stage_only(data, draft, JobEvent.CONFIRM), JobEvent.APPROVE)


def claim(data: dict[str, Any], job: Any, *, fence: int = 0) -> GuardLease:
    with business_transaction(data["engine"], data["identity"]) as session:
        return TargetGuards(data["applications"]).claim(
            session, UUID(job.job_id), job.version, fence, uuid4()
        )


def state(data: dict[str, Any], job: Any) -> Any:
    with business_transaction(data["engine"], data["identity"]) as session:
        return session.execute(
            text("SELECT status,version,claimed_at FROM command_jobs WHERE id=:id"),
            {"id": job.job_id},
        ).one()


def test_atomic_job_guard_claim_and_lease_assertion(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    job = approved(data)
    lease = claim(data, job)
    assert lease.fence == 1 and lease.jobVersion == 4
    assert state(data, job)[:2] == ("EXECUTING", 4) and state(data, job)[2] is not None
    with business_transaction(data["engine"], data["identity"]) as session:
        guards = TargetGuards(data["applications"])
        guards.assert_current(session, lease)
        assert guards.describe(session, UUID(job.job_id))["state"] == "LEASED"
        assert (
            session.execute(
                text("SELECT count(*) FROM target_guard_claims WHERE job_id=:id"),
                {"id": job.job_id},
            ).scalar_one()
            == 1
        )
    if evidence := os.getenv("YULIANG_GUARD_EVIDENCE"):
        Path(evidence).write_text(
            json.dumps(
                {
                    **lease.model_dump(mode="json"),
                    "status": "EXECUTING",
                    "stageSetup": "SYNTHETIC_APPROVAL_COMPONENT_NO_EXECUTION_ATTEMPT",
                    "platformCalls": 0,
                    "paidCalls": 0,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def test_same_target_two_workers_only_one_claim(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    job = approved(data)

    def attempt(_: int) -> str:
        try:
            claim(data, job)
            return "CLAIMED"
        except ApiError as error:
            return error.code.value

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert results.count("CLAIMED") == 1 and "VERSION_CONFLICT" in results
    assert state(data, job)[:2] == ("EXECUTING", 4)


async def other_account(data: dict[str, Any]) -> dict[str, Any]:
    account = uuid4()
    binding = data["binding"].model_copy(update={"id": uuid4(), "account_id": account})
    read_binding = binding.model_copy(
        update={
            "id": uuid4(),
            "operation_kind": "READ",
            "http_method": "GET",
            "normalized_path": data["readspec"].path,
            "catalog_id": data["readctx"].catalog_id,
            "permission_code": "overview.read",
            "action": "analyze",
        }
    )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO ad_accounts(id,tenant_id,provider,product_line,native_id,name,status) "
                "VALUES(:id,:tenant,'tencent','adq',:native,"
                "'Synthetic second account','AUTHORIZED')"
            ),
            {"id": account, "tenant": data["tenant"], "native": str(account)},
        )
        owner.execute(
            text(
                "INSERT INTO entity_links(tenant_id,shop_id,account_id) "
                "VALUES(:tenant,:shop,:account)"
            ),
            {"tenant": data["tenant"], "shop": data["shop"], "account": account},
        )
        register_binding(owner, binding)
        register_binding(owner, read_binding)
        link_authorization(
            owner, data["ctx"].authorization_id, account, "synthetic/M2-11a/second-discovery"
        )
    populate(data, execution=True, target=binding)
    second = data | {
        "binding": binding,
        "ctx": replace(
            data["ctx"], account_id=account, native_account_id=str(account), binding_id=binding.id
        ),
        "readctx": replace(
            data["readctx"],
            account_id=account,
            native_account_id=str(account),
            binding_id=read_binding.id,
        ),
        "target": data["target"].model_copy(update={"account_id": account}),
        "applications": JobApplications(
            data["engine"], data["identity"], uuid4(), data["applications"].key
        ),
    }
    full = await full_snapshot(second)
    configure_budget(second, full)
    lease, _ = await snapshot(second)
    second["source"] = lease.source_id
    configure(second, data["policy"])
    campaign = uuid4()
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO campaigns(id,tenant_id,shop_id,account_id,"
                "provider,product_line,native_id) "
                "VALUES(:id,:tenant,:shop,:account,'tencent','adq','456')"
            ),
            {"id": campaign, "tenant": data["tenant"], "shop": data["shop"], "account": account},
        )
        register_value_source(
            owner,
            campaign,
            lease.source_id,
            data["ctx"].authorization_id,
            "CHANGE_DAILY_BUDGET",
            "budget_minor",
            "synthetic/M2-11a/second-account",
        )
    second["input"] = data["input"].model_copy(
        update={"target_id": campaign, "idempotency_key": uuid4().hex}
    )
    second["campaign"] = campaign
    return second


async def test_two_accounts_same_native_id_have_distinct_guards(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    second = await other_account(data)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        install_policy(
            owner,
            data["tenant"],
            policy().model_copy(update={"daily_budget_minor": 40000}),
            expected_version=1,
            proof_ref="synthetic/M2-11a/two-account-limit",
        )
    data["guardPolicyVersion"] = second["guardPolicyVersion"] = 2
    first_job, second_job = approved(data), approved(second)
    first_lease, second_lease = claim(data, first_job), claim(second, second_job)
    assert (
        first_lease.guardId != second_lease.guardId and first_lease.fence == second_lease.fence == 1
    )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        actual = owner.execute(
            text(
                "SELECT account_id,native_id FROM target_execution_guards WHERE tenant_id=:tenant"
            ),
            {"tenant": data["tenant"]},
        ).all()
    assert {item.native_id for item in actual} == {"456"} and len(
        {item.account_id for item in actual}
    ) == 2


def test_old_fence_or_owner_cannot_write_under_current_lease(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    job = approved(data)
    lease = claim(data, job)
    for update in ({"fence": lease.fence - 1}, {"owner": uuid4()}):
        with (
            pytest.raises(ApiError) as error,
            business_transaction(data["engine"], data["identity"]) as session,
        ):
            TargetGuards(data["applications"]).assert_current(
                session, lease.model_copy(update=update)
            )
        assert error.value.code == ErrorCode.VERSION_CONFLICT


def test_quarantined_refuses_new_claim_with_state_rollback(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    job = approved(data)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO target_execution_guards(id,tenant_id,account_id,provider,
            product_line,target_type,native_id,fence,state,job_id,lease_owner,lease_until)
            SELECT :id,tenant_id,account_id,provider,product_line,'AD_PLAN',native_id,1,
            'QUARANTINED',:job,:owner,clock_timestamp()+interval '30 seconds'
            FROM campaigns WHERE id=:campaign"""),
            {"id": uuid4(), "job": job.job_id, "owner": uuid4(), "campaign": data["campaign"]},
        )
    with pytest.raises(ApiError) as error:
        claim(data, job, fence=1)
    assert error.value.code == ErrorCode.TARGET_QUARANTINED and state(data, job) == (
        "APPROVED",
        3,
        None,
    )


def test_cache_failure_does_not_change_pg_claim(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = budget_ready
    job = approved(data)

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise redis.ConnectionError("synthetic cache failure")

    monkeypatch.setattr(redis.Redis, "get", unavailable)
    lease = claim(data, job)
    with business_transaction(data["engine"], data["identity"]) as session:
        TargetGuards(data["applications"]).assert_current(session, lease)


def test_claim_failure_rolls_back_guard_state_and_event(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = budget_ready
    job = approved(data)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic claim audit failure")

    monkeypatch.setattr("yuliang.modules.execution.guards.record", fail)
    with pytest.raises(RuntimeError):
        claim(data, job)
    assert state(data, job) == ("APPROVED", 3, None)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM target_guard_claims")).scalar_one() == 0


def test_runtime_cannot_mutate_guard_or_expire_into_new_write(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    job = approved(data)
    lease = claim(data, job)
    with (
        pytest.raises(DBAPIError),
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(text("UPDATE target_execution_guards SET state='IDLE'"))
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE target_execution_guards SET lease_until=clock_timestamp()-"
                "interval '1 second' WHERE id=:id"
            ),
            {"id": lease.guardId},
        )
    with pytest.raises(ApiError), business_transaction(data["engine"], data["identity"]) as session:
        TargetGuards(data["applications"]).assert_current(session, lease)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            TargetGuards(data["applications"]).describe(session, UUID(job.job_id))["state"]
            == "LEASED"
        )


def test_cancel_and_claim_share_version_and_unclaimed_predicate(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    job = approved(data)

    def compete(action: str) -> str:
        try:
            if action == "claim":
                claim(data, job)
                return "EXECUTING"
            # 仅测试状态内核取消竞争；资金释放参与者尚未实现，R保守保留。
            with business_transaction(data["engine"], data["identity"]) as session:
                actor = data["identity"]
                before = session.execute(
                    text("SELECT access.job_state_context(:member,:user,:job,'CANCEL')"),
                    {"member": actor.membership_id, "user": actor.user_id, "job": job.job_id},
                ).scalar_one()
                if before["version"] != job.version:
                    raise ApiError(ErrorCode.VERSION_CONFLICT, "合成状态竞争版本变化")
                at = session.execute(text("SELECT clock_timestamp()")).scalar_one()
                after = evolve(
                    CommandJob.model_validate(before["document"]),
                    JobEvent.CANCEL,
                    StateFacts(),
                    at=at,
                    actor_id=actor.user_id,
                    requester_id=actor.user_id,
                )
                controlled(
                    lambda: session.execute(
                        text("""SELECT access.job_apply_transition(
                    :member,:user,:job,:version,'CANCEL',CAST(:document AS jsonb),
                    :request,NULL)"""),
                        {
                            "member": actor.membership_id,
                            "user": actor.user_id,
                            "job": job.job_id,
                            "version": job.version,
                            "document": after.model_dump_json(),
                            "request": uuid4(),
                        },
                    ).scalar_one()
                )
                return "CANCELLED"
        except ApiError as error:
            return error.code.value

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, ["claim", "cancel"]))
    assert sum(result in {"EXECUTING", "CANCELLED"} for result in results) == 1
    actual = state(data, job)
    assert actual.version == 4
    assert (actual.status == "EXECUTING") == (actual.claimed_at is not None)
