"""真实PG资金原语：合成平台读与已证明B；不代替完整confirm/审批/执行装配。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.command.applications import register_value_source
from yuliang.modules.command.jobs import CommandJob, MoneyValue
from yuliang.modules.command.state import JobEvent
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.freshness import ResourcePolicy, configure_policy
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.sync_store import MirrorRecord, SyncLease, SyncPage
from yuliang.modules.policy.models import RiskStage
from yuliang.modules.policy.repository import install_policy
from yuliang.modules.policy.reservations import BudgetReservations
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_budget_initialization import budget_ready as budget_ready
from .test_budget_initialization import rows
from .test_capability_evidence import capability as capability
from .test_identity_database import database as database
from .test_intent_binding import assess, stage_only
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_platform_payloads import decoder, parameters, signer
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import policy
from .test_risk_framework import risk_ready as risk_ready
from .test_risk_rules import application, receipts
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


def hold(data: dict[str, Any], job: CommandJob, key: str | None = None) -> dict[str, str | bool]:
    with business_transaction(data["engine"], data["identity"]) as session:
        return BudgetReservations(data["applications"]).hold(
            session, UUID(job.job_id), job.version, key or f"hold:{job.job_id}"
        )


async def second_plan(data: dict[str, Any]) -> CommandJob:
    ctx, spec, store = data["readctx"], data["readspec"], data["store"]
    filters = parameters(ctx) | {"object_id": "789"}
    lease = store.begin(ctx, spec, "plan.state", uuid4(), filters)
    assert isinstance(lease, SyncLease)
    permit = store.prepare(ctx, spec, lease)
    payload = filters | {"cursor": None}
    client = RecordedPlatformClient(
        PlatformHttpClient(
            signer,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"code": 0, "data": {"id": "789", "budget_minor": 9890}}
                )
            ),
        ),
        data["archive"],
        decoder=decoder,
    )
    result = await client.send(
        ctx,
        spec,
        permit.outbound_id,
        payload,
        SecretStr("synthetic"),
        target=data["target"].model_copy(update={"native_id": "789"}),
        before_persist=partial(
            store.dispatch_guard, ctx=ctx, spec=spec, permit=permit, parameters=payload
        ),
    )
    assert result.exchange is not None
    store.commit_page(
        ctx,
        spec,
        permit,
        SyncPage(
            (MirrorRecord(native_id="789", values=json.loads(result.exchange.body)["data"]),),
            None,
            True,
            result.exchange.received_at,
            result.exchange.received_at,
        ),
    )
    campaign = uuid4()
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO campaigns(id,tenant_id,shop_id,account_id,"
                "provider,product_line,native_id) "
                "VALUES(:id,:tenant,:shop,:account,'tencent','adq','789')"
            ),
            {
                "id": campaign,
                "tenant": data["tenant"],
                "shop": data["shop"],
                "account": ctx.account_id,
            },
        )
        configure_policy(
            owner,
            lease.source_id,
            ResourcePolicy(
                resource_key="plan_state",
                max_age_seconds=60,
                refresh_seconds=10,
                target_mode="OBJECT",
                required_fields=(("budget_minor", "INTEGER"),),
            ),
            "synthetic/M2-10b/plan-789",
        )
        register_value_source(
            owner,
            campaign,
            lease.source_id,
            ctx.authorization_id,
            "CHANGE_DAILY_BUDGET",
            "budget_minor",
            "synthetic/M2-10b/value-789",
        )
    value = data["input"].model_copy(
        update={
            "target_id": campaign,
            "new_value": MoneyValue(amount_minor=9900),
            "idempotency_key": uuid4().hex,
        }
    )
    job: CommandJob = data["applications"].create(value)
    return job


def initialize(data: dict[str, Any]) -> None:
    data["budget_service"].refresh(1)


def test_exact_limit_hold_and_replay_once(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    initialize(data)
    job = application(data)
    first = hold(data, job)
    second = hold(data, job)
    ledger, _ = rows(data)
    assert first["holdId"] == second["holdId"] and second["replayed"]
    assert ledger["confirmed_total_minor"] == 9990 and ledger["reserved_increase_minor"] == 10
    with business_transaction(data["engine"], data["identity"]) as session:
        reservation = session.execute(text("SELECT * FROM budget_reservations")).mappings().one()
        assert reservation["amount_minor"] == 10 and reservation["state"] == "HELD"
    if evidence := os.getenv("YULIANG_RESERVATION_EVIDENCE"):
        Path(evidence).write_text(
            json.dumps(
                {
                    **first,
                    "jobId": job.job_id,
                    "ledgerId": str(ledger["id"]),
                    "reservationId": str(reservation["id"]),
                    "B": 9990,
                    "R": 10,
                    "limit": 10000,
                    "stage": "PRIMITIVE_ONLY_NO_CONFIRM_HTTP_OR_EXECUTION",
                    "platformCalls": 0,
                    "paidCalls": 0,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


async def test_two_plans_compete_same_bucket_only_one_increase(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    initialize(data)
    jobs = [application(data), await second_plan(data)]
    assert all(job.status.value == "DRAFT" for job in jobs)

    def attempt(job: CommandJob) -> str:
        try:
            hold(data, job)
            return "HELD"
        except ApiError as error:
            return error.code.value

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, jobs))
    assert outcomes.count("HELD") == 1 and "RISK_BLOCKED" in outcomes
    ledger, _ = rows(data)
    assert (
        ledger["confirmed_total_minor"] + ledger["reserved_increase_minor"] == ledger["limit_minor"]
    )


def test_concurrent_repeated_confirmation_reserves_once(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    initialize(data)
    job = application(data)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: hold(data, job), range(4)))
    assert len({result["holdId"] for result in results}) == 1
    assert rows(data)[0]["reserved_increase_minor"] == 10


def test_own_hold_not_counted_twice_for_approval_or_execute(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    initialize(data)
    draft = application(data)
    hold(data, draft)
    pending = stage_only(data, draft, JobEvent.CONFIRM)
    approved = stage_only(data, pending, JobEvent.APPROVE)
    assess(data, approved, RiskStage.EXECUTE).require_allowed()
    assert rows(data)[0]["reserved_increase_minor"] == 10


@pytest.mark.parametrize("fault", ["policy", "expired", "stale_basis", "unknown_r", "upper_limit"])
def test_old_or_incomplete_or_over_parent_limit_never_holds(
    budget_ready: dict[str, Any], fault: str
) -> None:
    data = budget_ready
    initialize(data)
    job = application(data)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if fault == "policy":
            install_policy(
                owner,
                data["tenant"],
                policy(),
                expected_version=1,
                proof_ref="synthetic/M2-10b/new-policy",
            )
        elif fault == "expired":
            job = job.model_copy(update={"expires_at": job.created_at + timedelta(microseconds=1)})
            owner.execute(
                text(
                    "UPDATE command_jobs SET expires_at=:at,document=CAST(:document AS jsonb) "
                    "WHERE id=:id"
                ),
                {"id": job.job_id, "at": job.expires_at, "document": job.model_dump_json()},
            )
        else:
            query = {
                "stale_basis": (
                    "UPDATE budget_ledgers SET fetched_at=fetched_at-interval '1 hour',"
                    "source_updated_at=source_updated_at-interval '1 hour',"
                    "valid_until=valid_until-interval '1 hour'"
                ),
                "unknown_r": "UPDATE budget_ledgers SET reserved_increase_minor=1",
                "upper_limit": "UPDATE budget_ledgers SET limit_minor=9999",
            }[fault]
            owner.execute(text(query))
    with pytest.raises(ApiError):
        hold(data, job)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM job_budget_holds")).scalar_one() == 0
        assert session.execute(text("SELECT count(*) FROM budget_reservations")).scalar_one() == 0


async def test_unknown_platform_write_cannot_open_new_hold(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    initialize(data)
    job = application(data)
    await receipts(data, job, mode="unconfirmed")
    with pytest.raises(ApiError):
        hold(data, job)
    assert rows(data)[0]["reserved_increase_minor"] == 0


def test_same_target_conflict_denied_even_with_remaining_capacity(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        install_policy(
            owner,
            data["tenant"],
            policy().model_copy(update={"daily_budget_minor": 20000}),
            expected_version=1,
            proof_ref="synthetic/M2-10b/headroom",
        )
    data["budget_service"].refresh(2)
    first, second = application(data), application(data)
    hold(data, first)
    with pytest.raises(ApiError) as conflict:
        hold(data, second)
    assert conflict.value.code == ErrorCode.TARGET_QUARANTINED
    assert rows(data)[0]["reserved_increase_minor"] == 10


def test_rollback_and_runtime_cannot_forge_or_clear_holds(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    initialize(data)
    job = application(data)
    with (
        pytest.raises(RuntimeError),
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        BudgetReservations(data["applications"]).hold(
            session, UUID(job.job_id), 1, f"hold:{job.job_id}"
        )
        raise RuntimeError("synthetic later confirm participant failure")
    assert rows(data)[0]["reserved_increase_minor"] == 0
    for query in (
        "UPDATE budget_ledgers SET reserved_increase_minor=0",
        "DELETE FROM budget_reservations",
        "UPDATE job_budget_holds SET state='RELEASED'",
    ):
        with (
            pytest.raises(DBAPIError),
            business_transaction(data["engine"], data["identity"]) as session,
        ):
            session.execute(text(query))


def test_hold_key_bound_to_original_request(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    initialize(data)
    job = application(data)
    hold(data, job, "fixed-hold-key")
    with pytest.raises(ApiError) as error:
        hold(data, job, "changed-hold-key")
    assert error.value.code == ErrorCode.IDEMPOTENCY_MISMATCH
    assert rows(data)[0]["reserved_increase_minor"] == 10


def test_decrease_does_not_create_zero_or_negative_hold(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    initialize(data)
    job = application(data, 90)
    result = hold(data, job)
    assert result["state"] == "NOT_REQUIRED" and rows(data)[0]["reserved_increase_minor"] == 0
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM budget_reservations")).scalar_one() == 0
