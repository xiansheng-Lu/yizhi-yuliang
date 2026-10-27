"""真实PG全量页生成B；上游为显式规范合成DTO，未验收真实平台计划列表。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.freshness import ResourcePolicy, configure_policy
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.connectors.sync_store import MirrorRecord, SyncLease, SyncPage, SyncStore
from yuliang.modules.policy.budget import BudgetInitialization, register_budget_account
from yuliang.modules.policy.repository import install_policy
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

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
from .test_risk_rules import application, values
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


class FullBudgetParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    cursor: str | None = None


async def full_snapshot(
    data: dict[str, Any],
    *,
    paused: int = 9890,
    empty: bool = False,
    active: int = 100,
    source_at: datetime | None = None,
) -> UUID:
    ctx = data["readctx"]
    spec = replace(data["readspec"], parameters_model=FullBudgetParameters, target_parameter=None)
    safe = ArchivePolicy(ArchivePolicy().fields | {"enabled", "source_updated_at"})
    store = SyncStore(data["engine"], data["codec"], policy=safe)
    lease = store.begin(ctx, spec, "plans.budgets", uuid4(), {"account_id": ctx.native_account_id})
    assert isinstance(lease, SyncLease)
    source = lease.source_id
    pages = (
        [[]]
        if empty
        else [
            [{"id": "456", "budget_minor": active, "enabled": True, "currency": "CNY"}],
            [{"id": "789", "budget_minor": paused, "enabled": False, "currency": "CNY"}],
        ]
    )
    for index, rows in enumerate(pages):
        permit = store.prepare(ctx, spec, lease)

        async def upstream(
            request: httpx.Request, page_rows: list[dict[str, Any]] = rows
        ) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"list": page_rows},
                    "source_updated_at": (source_at or datetime.now(UTC)).isoformat(),
                },
            )

        client = RecordedPlatformClient(
            PlatformHttpClient(signer, transport=httpx.MockTransport(upstream)),
            data["archive"],
            policy=safe,
            decoder=decoder,
        )
        payload = {
            "account_id": ctx.native_account_id,
            "cursor": permit.cursor.get_secret_value() if permit.cursor else None,
        }
        result = await client.send(
            ctx,
            spec,
            permit.outbound_id,
            payload,
            SecretStr("synthetic"),
            before_persist=partial(
                store.dispatch_guard, ctx=ctx, spec=spec, permit=permit, parameters=payload
            ),
        )
        assert result.exchange is not None
        response = json.loads(result.exchange.body)
        terminal = index == len(pages) - 1
        stored = store.commit_page(
            ctx,
            spec,
            permit,
            SyncPage(
                tuple(
                    MirrorRecord(native_id=row["id"], values=row)
                    for row in response["data"]["list"]
                ),
                None if terminal else SecretStr("synthetic-next-page"),
                terminal,
                result.exchange.received_at,
                datetime.fromisoformat(response["source_updated_at"]),
            ),
        )
        if not terminal:
            assert isinstance(stored, SyncLease)
            lease = stored
    return source


def configure(data: dict[str, Any], source: UUID) -> None:
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        configure_policy(
            owner,
            source,
            ResourcePolicy(
                resource_key="all_plan_budgets",
                max_age_seconds=60,
                refresh_seconds=10,
                target_mode="ACCOUNT",
                required_fields=(
                    ("budget_minor", "INTEGER"),
                    ("currency", "CODE"),
                    ("enabled", "BOOLEAN"),
                ),
            ),
            "synthetic/M2-10a/full-fields",
        )
        register_budget_account(
            owner,
            data["ctx"].account_id,
            "CNY",
            "synthetic/M2-10a/full-including-paused",
            source_id=source,
            authorization_id=data["ctx"].authorization_id,
            empty_cursor_digest=data["codec"].digest(None),
        )


@pytest.fixture
async def budget_ready(risk_ready: dict[str, Any]) -> dict[str, Any]:
    data = risk_ready
    source = await full_snapshot(data)
    configure(data, source)
    identity = VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"])
    return data | {
        "budget_source": source,
        "budget_identity": identity,
        "budget_service": BudgetInitialization(data["engine"], identity, uuid4()),
    }


def rows(data: dict[str, Any]) -> tuple[Any, Any]:
    with business_transaction(data["engine"], data["identity"]) as session:
        ledger = session.execute(text("SELECT * FROM budget_ledgers")).mappings().one()
        observations = (
            session.execute(text("SELECT * FROM budget_observations ORDER BY recorded_at"))
            .mappings()
            .all()
        )
        return ledger, observations


def test_full_paused_basis_and_repeated_initialization_idempotent(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    first = data["budget_service"].refresh(1)
    second = data["budget_service"].refresh(1)
    ledger, observations = rows(data)
    assert (
        first["ledgerId"] == second["ledgerId"]
        and first["observationId"] == second["observationId"]
    )
    assert second["replayed"] and ledger["confirmed_total_minor"] == 9990 and len(observations) == 1
    assert ledger["currency"] == "CNY" and ledger["covered_account_ids"] == [data["ctx"].account_id]
    assert {item["enabled"] for item in observations[0]["components"]} == {True, False}
    if evidence := os.getenv("YULIANG_BUDGET_EVIDENCE"):
        Path(evidence).write_text(
            json.dumps(
                {
                    **first,
                    "confirmedTotalMinor": 9990,
                    "components": observations[0]["components"],
                    "sources": observations[0]["sources"],
                    "realPG": True,
                    "upstream": "SYNTHETIC_CANONICAL_DTO",
                    "platformCalls": 0,
                    "paidCalls": 0,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


async def test_over_limit_truth_and_source_delta_preserves_reservations(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    initial = data["budget_service"].refresh(1)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE budget_ledgers SET reserved_increase_minor=10 WHERE id=:id"),
            {"id": initial["ledgerId"]},
        )
    assert await full_snapshot(data, paused=10000) == data["budget_source"]
    updated = data["budget_service"].refresh(1)
    ledger, observations = rows(data)
    assert updated["ledgerId"] == initial["ledgerId"] and ledger["confirmed_total_minor"] == 10100
    assert ledger["reserved_increase_minor"] == 10 and ledger["freeze_reason"] == "OVER_LIMIT"
    assert len(observations) == 2 and observations[-1]["delta_minor"] == 110
    assert observations[-1]["components"][0]["runId"] != observations[0]["components"][0]["runId"]


async def test_empty_complete_snapshot_is_known_zero(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    await full_snapshot(data, empty=True)
    data["budget_service"].refresh(1)
    ledger, observations = rows(data)
    assert ledger["confirmed_total_minor"] == 0 and observations[0]["components"] == []


@pytest.mark.parametrize(
    "fault",
    [
        "partial",
        "stale",
        "no_source_clock",
        "wrong_currency",
        "missing_state",
        "missing_component",
        "wrong_start",
        "wrong_policy_contract",
        "no_auth",
    ],
)
def test_incomplete_unknown_or_wrong_unit_cannot_become_budget(
    budget_ready: dict[str, Any], fault: str
) -> None:
    data = budget_ready
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        queries = {
            "partial": "UPDATE sync_runs SET state='PARTIAL' WHERE source_id=:id",
            "stale": (
                "UPDATE sync_pages SET source_updated_at=source_updated_at-interval '1 hour' "
                "WHERE run_id IN (SELECT id FROM sync_runs WHERE source_id=:id)"
            ),
            "no_source_clock": (
                "UPDATE sync_pages SET source_updated_at=NULL "
                "WHERE run_id IN (SELECT id FROM sync_runs WHERE source_id=:id)"
            ),
            "wrong_currency": (
                "UPDATE sync_resource_mirrors SET projection=jsonb_set(projection,"
                "'{data,currency}','\"BEAN\"'::jsonb) WHERE source_id=:id"
            ),
            "missing_state": (
                "UPDATE sync_resource_mirrors SET projection=projection#-"
                "'{data,enabled}' WHERE source_id=:id"
            ),
            "missing_component": (
                "DELETE FROM sync_resource_mirrors WHERE source_id=:id AND native_id='789'"
            ),
            "wrong_start": (
                "UPDATE budget_account_contracts SET empty_cursor_digest=repeat('a',64) "
                "WHERE source_id=:id"
            ),
            "wrong_policy_contract": (
                "UPDATE resource_freshness_policies SET revision=revision+1 WHERE source_id=:id"
            ),
            "no_auth": (
                "UPDATE provider_authorizations SET status='REVOKED' "
                "WHERE id=(SELECT authorization_id FROM budget_account_contracts "
                "WHERE source_id=:id)"
            ),
        }
        owner.execute(text(queries[fault]), {"id": data["budget_source"]})
    with pytest.raises(ApiError) as error:
        data["budget_service"].refresh(1)
    assert error.value.code == ErrorCode.DATA_COVERAGE_INSUFFICIENT
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM budget_ledgers")).scalar_one() == 0


def test_unknown_new_account_denies_and_explicit_bean_classification_stays_separate(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        account = owner.execute(
            text(
                "INSERT INTO ad_accounts(tenant_id,provider,product_line,native_id,name) "
                "VALUES(:tenant,'beans','beans',:native,'Synthetic bean') RETURNING id"
            ),
            {"tenant": data["tenant"], "native": uuid4().hex},
        ).scalar_one()
    with pytest.raises(ApiError):
        data["budget_service"].refresh(1)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        register_budget_account(owner, account, "BEAN", "synthetic/M2-10a/separate-bean-unit")
    data["budget_service"].refresh(1)
    ledger, observations = rows(data)
    assert ledger["confirmed_total_minor"] == 9990 and account not in ledger["covered_account_ids"]
    assert any(item.get("currency") == "BEAN" for item in observations[0]["sources"])


def test_runtime_cannot_configure_facts_or_modify_observations_and_requires_policy_owner(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    for query in (
        "UPDATE budget_account_contracts SET currency='BEAN'",
        "DELETE FROM budget_observations",
    ):
        with (
            pytest.raises(DBAPIError),
            business_transaction(data["engine"], data["identity"]) as session,
        ):
            session.execute(text(query))
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE data_scopes SET permissions_mask=ARRAY['overview.read'] "
                "WHERE membership_id=:id"
            ),
            {"id": data["identity"].membership_id},
        )
    with pytest.raises(ApiError) as denied:
        BudgetInitialization(data["engine"], data["identity"], uuid4()).refresh(1)
    assert denied.value.code == ErrorCode.FORBIDDEN
    with pytest.raises(ValueError), data["engine"].begin() as runtime:
        register_budget_account(runtime, data["ctx"].account_id, "BEAN", "synthetic/forbidden")


def test_policy_version_roll_keeps_bucket_and_r(budget_ready: dict[str, Any]) -> None:
    data = budget_ready
    first = data["budget_service"].refresh(1)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE budget_ledgers SET reserved_increase_minor=7 WHERE id=:id"),
            {"id": first["ledgerId"]},
        )
        install_policy(
            owner,
            data["tenant"],
            policy().model_copy(update={"daily_budget_minor": 15000}),
            expected_version=1,
            proof_ref="synthetic/M2-10a/policy-v2",
        )
    with pytest.raises(ApiError) as error:
        data["budget_service"].refresh(1)
    assert error.value.code == ErrorCode.POLICY_VERSION_CHANGED
    second = data["budget_service"].refresh(2)
    ledger, observations = rows(data)
    assert second["ledgerId"] == first["ledgerId"] and ledger["reserved_increase_minor"] == 7
    assert ledger["policy_version"] == 2 and len(observations) == 2


def test_concurrent_initialization_serializes_one_observation(budget_ready: dict[str, Any]) -> None:
    data = budget_ready

    def refresh(_: int) -> Any:
        return BudgetInitialization(data["engine"], data["budget_identity"], uuid4()).refresh(1)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(refresh, range(4)))
    assert len({result["observationId"] for result in results}) == 1
    assert len(rows(data)[1]) == 1


@pytest.mark.parametrize(
    "fault", ["new_account", "policy_contract", "paused_source", "latest_incomplete"]
)
def test_existing_basis_loses_qualification_when_coverage_changes(
    budget_ready: dict[str, Any], fault: str
) -> None:
    data = budget_ready
    receipt = data["budget_service"].refresh(1)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if fault == "new_account":
            owner.execute(
                text(
                    "INSERT INTO ad_accounts(tenant_id,provider,product_line,native_id,name) "
                    "VALUES(:tenant,'beans','beans',:native,'Synthetic unclassified')"
                ),
                {"tenant": data["tenant"], "native": uuid4().hex},
            )
        else:
            query = {
                "policy_contract": (
                    "UPDATE resource_freshness_policies SET revision=revision+1 WHERE source_id=:id"
                ),
                "paused_source": "UPDATE sync_sources SET enabled=false WHERE id=:id",
                "latest_incomplete": "UPDATE sync_runs SET state='PARTIAL' WHERE source_id=:id",
            }[fault]
            owner.execute(text(query), {"id": data["budget_source"]})
    with business_transaction(data["engine"], data["identity"]) as session:
        assert not session.execute(
            text("SELECT access.budget_basis_current(:id)"), {"id": receipt["ledgerId"]}
        ).scalar_one()


def test_audit_failure_rolls_back_ledger_and_observation(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = budget_ready

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic budget audit failure")

    monkeypatch.setattr("yuliang.modules.policy.budget.record", fail)
    with pytest.raises(RuntimeError):
        data["budget_service"].refresh(1)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM budget_ledgers")).scalar_one() == 0
        assert session.execute(text("SELECT count(*) FROM budget_observations")).scalar_one() == 0


def test_risk_consumes_produced_basis_and_denies_changed_coverage(
    budget_ready: dict[str, Any],
) -> None:
    data = budget_ready
    data["budget_service"].refresh(1)
    exact = application(data)
    assert values(exact)["AMOUNT_LIMIT"] and exact.status.value == "DRAFT"
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE sync_sources SET enabled=false WHERE id=:id"),
            {"id": data["budget_source"]},
        )
    assert not values(application(data))["AMOUNT_LIMIT"]
