"""真实PG/Redis独立GET回读；上游显式Mock，不改job/R、不宣称平台E2。"""

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.governance import PlatformGovernance, QuotaLimits, QuotaScope
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.execution.readback import IndependentReadback, ParameterReadPort
from yuliang.modules.execution.readback_values import ParameterReadContract
from yuliang.modules.execution.receipts import ExecutionReceipts
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_budget_initialization import budget_ready as budget_ready
from .test_budget_settlement import ledger
from .test_capability_evidence import capability as capability
from .test_execution_receipts import observation
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_platform_payloads import decoder, signer
from .test_resource_freshness import health_ready as health_ready
from .test_risk_framework import risk_ready as risk_ready
from .test_risk_rules import CanonicalRead
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


async def accepted(data: dict[str, Any]) -> Any:
    prepared, _, raw = await observation(data)
    ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    return prepared


def reader(
    data: dict[str, Any], governance: PlatformGovernance, mode: str = "matched"
) -> tuple[IndependentReadback, list[str]]:
    calls: list[str] = []
    contract = ParameterReadContract(
        action="CHANGE_DAILY_BUDGET",
        canonical_field="budget_minor",
        object_path=("data",),
        object_id_field="id",
        value_field="budget_minor",
        currency_field="currency",
        clock_kind="SOURCE" if mode == "old_source" else "FETCHED",
        source_updated_field="source_updated_at" if mode == "old_source" else None,
        max_age_seconds=30,
        proof_ref="synthetic/M2-17a/canonical-read",
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        assert (
            request.method == "GET" and request.url.params["object_id"] == data["target"].native_id
        )
        if mode == "timeout":
            raise httpx.ReadTimeout("synthetic readonly timeout", request=request)
        result: dict[str, Any] = {
            "id": "other" if mode == "wrong_target" else "456",
            "currency": "CNY",
        }
        if mode != "missing":
            result["budget_minor"] = 100 if mode == "mismatch" else 110
        if mode == "old_source":
            result["source_updated_at"] = (datetime.now(UTC) - timedelta(seconds=2)).isoformat()
        return httpx.Response(200, json={"code": 0, "data": result})

    recorded = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(upstream)),
        data["archive"],
        decoder=decoder,
        policy=ArchivePolicy(ArchivePolicy().fields | contract.archive_fields),
    )
    port = ParameterReadPort(
        replace(data["readspec"], parameters_model=CanonicalRead, cursor_parameter=None),
        contract,
        lambda ctx, target: {"account_id": ctx.native_account_id, "object_id": target.native_id},
    )
    return IndependentReadback(
        data["applications"],
        GovernedPlatformClient(recorded, governance, data["application"].id),
        {"CHANGE_DAILY_BUDGET": port},
    ), calls


@pytest.mark.parametrize(
    "mode,status",
    [
        ("matched", "MATCHED"),
        ("mismatch", "MISMATCH"),
        ("missing", "UNAVAILABLE"),
        ("wrong_target", "UNAVAILABLE"),
        ("old_source", "UNREFRESHED"),
        ("timeout", "UNAVAILABLE"),
    ],
)
async def test_independent_get_classification_never_finalizes_job_or_money(
    budget_ready: dict[str, Any],
    governance: PlatformGovernance,
    mode: str,
    status: str,
) -> None:
    data = budget_ready
    prepared = await accepted(data)
    before = data["applications"].get(prepared.attempt.business.id)
    service, calls = reader(data, governance, mode)
    result = await service.inspect(
        prepared.attempt.attempt_id, data["readctx"], SecretStr("synthetic"), uuid4()
    )
    assert calls == ["GET"] and result.comparison.status == status
    assert (
        result.read_outbound_id is not None
        and result.read_outbound_id != prepared.attempt.outbound_id
    )
    assert result.raw_id is not None and result.comparison.evidence_level == "E0"
    assert data["applications"].get(prepared.attempt.business.id) == before
    assert ledger(data)[:2] == (9990, 10)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert session.execute(text("SELECT count(*) FROM verified_action_facts")).scalar_one() == 0
        assert (
            session.execute(text("SELECT count(*) FROM budget_job_finalizations")).scalar_one() == 0
        )
    if mode == "matched" and (evidence := os.getenv("YULIANG_READBACK_EVIDENCE")):
        Path(evidence).write_text(
            json.dumps(
                {
                    **result.model_dump(mode="json"),
                    "input": "EXPLICIT_MOCK_WRITE_AND_INDEPENDENT_GET_REAL_PG_REDIS",
                    "jobId": str(prepared.attempt.business.id),
                    "jobStatusBeforeAndAfter": before.status.value,
                    "jobVersionBeforeAndAfter": before.version,
                    "heldMinorBeforeAndAfter": 10,
                    "httpMethods": calls,
                    "verifiedActionFacts": 0,
                    "budgetFinalizations": 0,
                    "platformCalls": 0,
                    "paidCalls": 0,
                    "humanSignoff": "PENDING",
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )


async def test_duplicate_check_uses_original_archive_without_second_get(
    budget_ready: dict[str, Any],
    governance: PlatformGovernance,
) -> None:
    data = budget_ready
    prepared = await accepted(data)
    service, calls = reader(data, governance)
    check = uuid4()
    first = await service.inspect(
        prepared.attempt.attempt_id, data["readctx"], SecretStr("synthetic"), check
    )
    second = await service.inspect(
        prepared.attempt.attempt_id, data["readctx"], SecretStr("synthetic"), check
    )
    assert calls == ["GET"] and second.read_outbound_id == first.read_outbound_id
    assert second.comparison == first.comparison and second.raw_id == first.raw_id


async def test_default_missing_port_and_forged_scope_make_no_get(
    budget_ready: dict[str, Any],
    governance: PlatformGovernance,
) -> None:
    data = budget_ready
    prepared = await accepted(data)
    with pytest.raises(ApiError) as unavailable:
        await IndependentReadback(data["applications"]).inspect(
            prepared.attempt.attempt_id, data["readctx"], SecretStr("synthetic"), uuid4()
        )
    assert unavailable.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
    service, calls = reader(data, governance)
    with pytest.raises(ApiError) as wrong:
        await service.inspect(
            prepared.attempt.attempt_id,
            replace(data["readctx"], account_id=uuid4()),
            SecretStr("synthetic"),
            uuid4(),
        )
    assert wrong.value.code == ErrorCode.CAPABILITY_UNAVAILABLE and calls == []


async def test_readback_quota_deferred_has_no_fake_fetch_or_archive(
    budget_ready: dict[str, Any],
    governance: PlatformGovernance,
) -> None:
    data = budget_ready
    prepared = await accepted(data)
    limited = PlatformGovernance(
        governance.client, QuotaLimits(1, 1, 0, 0, 0, 0, 60000), namespace=governance.namespace
    )
    assert limited.admit(QuotaScope(data["application"].id, data["readctx"]), "READBACK").allowed
    service, calls = reader(data, limited)
    result = await service.inspect(
        prepared.attempt.attempt_id, data["readctx"], SecretStr("synthetic"), uuid4()
    )
    assert calls == [] and result.unavailable_cause == "QUOTA" and result.retry_at is not None
    assert result.read_outbound_id is None and result.raw_id is None
    assert result.comparison.fetched_at is None and result.comparison.status == "UNAVAILABLE"


async def test_matching_read_without_accepted_receipt_cannot_prove_write_success(
    budget_ready: dict[str, Any], governance: PlatformGovernance
) -> None:
    data = budget_ready
    prepared, _, raw = await observation(data, "timeout")
    ExecutionReceipts(data["applications"]).record(prepared.attempt.attempt_id, raw, 1)
    before = data["applications"].get(prepared.attempt.business.id)
    assert before.status.value == "EXECUTING" and before.execution_receipt is None
    service, calls = reader(data, governance)
    result = await service.inspect(
        prepared.attempt.attempt_id, data["readctx"], SecretStr("synthetic"), uuid4()
    )
    assert calls == ["GET"] and result.comparison.status == "MATCHED"
    assert result.comparison.evidence_level == "E0"
    assert data["applications"].get(prepared.attempt.business.id) == before
    assert ledger(data)[:2] == (9990, 10)


@pytest.mark.parametrize("mode", ["authorization_revoked", "sync_paused"])
async def test_current_authorization_required_but_sync_pause_does_not_disable_critical_read(
    budget_ready: dict[str, Any], governance: PlatformGovernance, mode: str
) -> None:
    data = budget_ready
    prepared = await accepted(data)
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if mode == "authorization_revoked":
            owner.execute(
                text("UPDATE provider_authorizations SET status='REVOKED' WHERE id=:id"),
                {"id": data["readctx"].authorization_id},
            )
        else:
            owner.execute(
                text("UPDATE sync_sources SET enabled=false WHERE id=:id"),
                {"id": data["source"]},
            )
    service, calls = reader(data, governance)
    if mode == "authorization_revoked":
        with pytest.raises(ApiError):
            await service.inspect(
                prepared.attempt.attempt_id, data["readctx"], SecretStr("synthetic"), uuid4()
            )
        assert calls == []
    else:
        result = await service.inspect(
            prepared.attempt.attempt_id, data["readctx"], SecretStr("synthetic"), uuid4()
        )
        assert calls == ["GET"] and result.comparison.status == "MATCHED"
    assert ledger(data)[:2] == (9990, 10)
