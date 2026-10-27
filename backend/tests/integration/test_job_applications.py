"""真实PG申请与确认页证据；供应商HTTP/账户均为合成测试输入。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.modules.catalog.service import link_authorization
from yuliang.modules.command.applications import JobApplications, register_value_source
from yuliang.modules.command.jobs import CreateJobRequest, JobStatus, MoneyValue
from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.freshness import ResourcePolicy, configure_policy
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.connectors.sync_store import MirrorRecord, SyncLease, SyncPage, SyncStore
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_capability_evidence import capability as capability
from .test_identity_database import database as database
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_platform_payloads import decoder, parameters, signer
from .test_resource_freshness import health_ready as health_ready
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "action,field,kind,before,after",
    [
        (
            "CHANGE_BID",
            "bid_minor",
            "INTEGER",
            0,
            {"kind": "MONEY", "currency": "CNY", "amount_minor": 1},
        ),
        ("SET_ROI_TARGET", "target_roi", "NUMBER", 1.25, {"kind": "RATIO", "value": 2.5}),
        ("SET_ROI_TARGET", "target_roi", "NUMBER", 0, {"kind": "RATIO", "value": 2.5}),
        ("PAUSE_AD_PLAN", "enabled", "BOOLEAN", True, {"kind": "BOOLEAN", "value": False}),
        ("RESUME_AD_PLAN", "enabled", "BOOLEAN", False, {"kind": "BOOLEAN", "value": True}),
    ],
)
async def test_other_action_current_values_from_confirmed_pages(
    applications: dict[str, Any],
    action: Any,
    field: str,
    kind: Any,
    before: Any,
    after: dict[str, Any],
) -> None:
    data = applications
    ctx, spec = data["readctx"], data["readspec"]
    store = SyncStore(data["engine"], data["codec"], policy=ArchivePolicy(frozenset({field})))
    lease = store.begin(ctx, spec, "plan.state", uuid4(), parameters(ctx))
    assert isinstance(lease, SyncLease)
    permit = store.prepare(ctx, spec, lease)
    payload = parameters(ctx) | {"cursor": None}
    client = RecordedPlatformClient(
        PlatformHttpClient(
            signer,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"code": 0, "data": {field: before}})
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
        target=data["target"],
        before_persist=lambda session: store.dispatch_guard(session, ctx, spec, permit, payload),
    )
    assert result.exchange is not None
    actual = json.loads(result.exchange.body)["data"]
    fetched = result.exchange.received_at
    store.commit_page(
        ctx,
        spec,
        permit,
        SyncPage(
            (MirrorRecord(native_id="456", values=actual),),
            None,
            True,
            fetched,
            fetched - timedelta(seconds=1),
        ),
    )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        configure_policy(
            owner,
            lease.source_id,
            ResourcePolicy(
                resource_key="plan_state",
                max_age_seconds=60,
                refresh_seconds=10,
                target_mode="OBJECT",
                required_fields=((field, kind),),
            ),
            "synthetic/M2-05a/typed-field",
        )
        register_value_source(
            owner,
            data["campaign"],
            lease.source_id,
            ctx.authorization_id,
            action,
            field,
            "synthetic/M2-05a/typed-mapping",
        )
    value = CreateJobRequest.model_validate(
        data["input"].model_dump() | {"action_type": action, "new_value": after}
    )
    job = data["applications"].create(value)
    assert job.old_value is not None
    key = "amount_minor" if kind == "INTEGER" else "value"
    assert job.old_value.model_dump()[key] == before and job.new_value.model_dump() == after
    assert job.status == JobStatus.BLOCKED


@pytest.fixture
def applications(health_ready: dict[str, Any]) -> dict[str, Any]:
    data = health_ready
    campaign, live = uuid4(), uuid4()
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO campaigns(id,tenant_id,shop_id,account_id,
        provider,product_line,native_id)
        VALUES(:id,:tenant,:shop,:account,'tencent','adq','456')"""),
            {
                "id": campaign,
                "tenant": data["tenant"],
                "shop": data["shop"],
                "account": data["ctx"].account_id,
            },
        )
        owner.execute(
            text(
                "INSERT INTO live_sessions(id,tenant_id,shop_id,title) "
                "VALUES(:id,:tenant,:shop,'Synthetic live')"
            ),
            {"id": live, "tenant": data["tenant"], "shop": data["shop"]},
        )
        link_authorization(
            owner,
            data["ctx"].authorization_id,
            data["ctx"].account_id,
            "synthetic/command/discovery",
        )
        register_value_source(
            owner,
            campaign,
            data["source"],
            data["ctx"].authorization_id,
            "CHANGE_DAILY_BUDGET",
            "budget_minor",
            "synthetic/command/current-value",
        )
    value = CreateJobRequest(
        source="PC_MANUAL",
        shop_id=data["shop"],
        target_type="AD_PLAN",
        target_id=campaign,
        action_type="CHANGE_DAILY_BUDGET",
        new_value=MoneyValue(amount_minor=200),
        intent="测试预算申请",
        idempotency_key=uuid4().hex,
    )
    service = JobApplications(
        data["engine"],
        data["identity"],
        uuid4(),
        SecretStr("synthetic-job-audit-secret-more-than-32-chars"),
    )
    return data | {"applications": service, "input": value, "campaign": campaign, "live": live}


def test_actual_current_value_proof_and_atomic_history(applications: dict[str, Any]) -> None:
    data = applications
    job = data["applications"].create(data["input"])
    assert job.old_value == MoneyValue(amount_minor=100) and job.new_value == MoneyValue(
        amount_minor=200
    )
    assert job.status == JobStatus.BLOCKED and job.risk_summary.source == "SERVER"
    assert (
        job.risk_summary.policy_version == 0
        and job.risk_summary.checks[-1].code == "POLICY_ENGINE_READY"
    )
    assert not job.risk_summary.checks[-1].passed and job.approval.state == "NOT_REQUESTED"
    assert job.execution_receipt is None and job.account_id == str(data["ctx"].account_id)
    assert data["applications"].get(UUID(job.job_id)) == job
    with business_transaction(data["engine"], data["identity"]) as session:
        row = (
            session.execute(
                text("SELECT intent_hash,target_proof FROM command_jobs WHERE id=:id"),
                {"id": job.job_id},
            )
            .mappings()
            .one()
        )
        assert len(row["intent_hash"]) == 64
        assert row["target_proof"]["sourceId"] == str(data["source"])
        assert row["target_proof"]["outboundId"] and row["target_proof"]["field"] == "budget_minor"
        assert (
            session.execute(
                text("SELECT count(*) FROM job_events WHERE job_id=:id"), {"id": job.job_id}
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                text("SELECT count(*) FROM audit_logs WHERE object_id=:id AND action='job.create'"),
                {"id": job.job_id},
            ).scalar_one()
            == 1
        )
    if evidence := os.getenv("YULIANG_JOB_EVIDENCE"):
        Path(evidence).write_text(
            json.dumps(
                {
                    "job": job.model_dump(mode="json"),
                    "requestId": str(data["applications"].request_id),
                    "proof": row["target_proof"],
                    "platformCalls": 0,
                    "paidCalls": 0,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )


def test_same_key_replay_and_different_payload_never_duplicate(
    applications: dict[str, Any],
) -> None:
    data = applications
    job = data["applications"].create(data["input"])
    # 已保存申请按原键回放，不拿后来变化的现值重新建第二申请。
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE sync_sources SET enabled=false WHERE id=:id"), {"id": data["source"]}
        )
    assert data["applications"].create(data["input"]) == job
    changed = data["input"].model_copy(update={"new_value": MoneyValue(amount_minor=300)})
    with pytest.raises(ApiError) as mismatch:
        data["applications"].create(changed)
    assert mismatch.value.code == ErrorCode.IDEMPOTENCY_MISMATCH
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM command_jobs WHERE document IS NOT NULL")
            ).scalar_one()
            == 1
        )


def test_eight_concurrent_creations_have_one_job_event_and_audit(
    applications: dict[str, Any],
) -> None:
    data = applications
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(lambda _: data["applications"].create(data["input"]), range(8)))
    assert len({j.job_id for j in jobs}) == 1
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM job_events WHERE job_id=:id"), {"id": jobs[0].job_id}
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize(
    "change",
    ["missing_mapping", "stale", "missing_field", "wrong_native", "wrong_shop", "revoked_scope"],
)
def test_missing_or_wrong_facts_never_become_old_value(
    applications: dict[str, Any], change: str
) -> None:
    data = applications
    value = data["input"]
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if change == "missing_mapping":
            owner.execute(
                text("DELETE FROM command_value_sources WHERE campaign_id=:id"),
                {"id": data["campaign"]},
            )
        elif change == "stale":
            owner.execute(
                text(
                    "UPDATE sync_pages SET source_updated_at=source_updated_at-interval '1 hour',"
                    "fetched_at=fetched_at-interval '1 hour' WHERE run_id=:id"
                ),
                {"id": data["good_lease"].run_id},
            )
        elif change == "missing_field":
            owner.execute(
                text("UPDATE sync_resource_mirrors SET projection='{}'::jsonb WHERE source_id=:id"),
                {"id": data["source"]},
            )
        elif change == "wrong_native":
            owner.execute(
                text("UPDATE campaigns SET native_id='other-plan' WHERE id=:id"),
                {"id": data["campaign"]},
            )
        elif change == "wrong_shop":
            value = value.model_copy(update={"shop_id": uuid4()})
        else:
            owner.execute(
                text("DELETE FROM data_scopes WHERE membership_id=:id"), {"id": data["member"]}
            )
    with pytest.raises(ApiError):
        data["applications"].create(value)
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM command_jobs WHERE document IS NOT NULL")
            ).scalar_one()
            == 0
        )


def test_live_handoff_is_manual_and_never_advertising_write(applications: dict[str, Any]) -> None:
    data = applications
    value = CreateJobRequest.model_validate(
        data["input"].model_dump()
        | {
            "target_type": "LIVE_SESSION",
            "target_id": data["live"],
            "action_type": "REQUEST_LIVE_START",
            "new_value": {"kind": "HANDOFF", "value": "START"},
        }
    )
    job = data["applications"].create(value)
    assert job.status == JobStatus.MANUAL_REQUIRED and job.risk_summary.result == "MANUAL_ONLY"
    assert job.old_value is None and job.account_id is None and job.execution_receipt is None
    assert job.target_id == job.live_session_id == str(data["live"])


def test_private_other_member_same_key_and_revoked_reader(applications: dict[str, Any]) -> None:
    data = applications
    job = data["applications"].create(data["input"])
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("""INSERT INTO role_grants(tenant_id,membership_id,role_id)
        SELECT :tenant,:member,id FROM roles WHERE tenant_id=:tenant AND code='OPERATOR'"""),
            {"tenant": data["tenant"], "member": data["approver_member"]},
        )
    other = JobApplications(
        data["engine"],
        VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"]),
        uuid4(),
        data["applications"].key,
    )
    with pytest.raises(ApiError) as hidden:
        other.get(UUID(job.job_id))
    assert hidden.value.code == ErrorCode.NO_DATA
    with pytest.raises(ApiError) as mismatch:
        other.create(data["input"])
    assert mismatch.value.code == ErrorCode.IDEMPOTENCY_MISMATCH
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:id"), {"id": data["member"]}
        )
    with pytest.raises(ApiError):
        data["applications"].get(UUID(job.job_id))


def test_audit_failure_rolls_back_job_and_event(
    applications: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = applications

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr("yuliang.modules.command.applications.record", fail)
    with pytest.raises(RuntimeError):
        data["applications"].create(data["input"])
    with business_transaction(data["engine"], data["identity"]) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM command_jobs WHERE document IS NOT NULL")
            ).scalar_one()
            == 0
        )
        assert session.execute(text("SELECT count(*) FROM job_events")).scalar_one() == 0


def test_legacy_partial_jobs_are_not_fabricated_and_documents_cannot_bypass_check(
    applications: dict[str, Any],
) -> None:
    data = applications
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        legacy = owner.execute(
            text(
                "INSERT INTO command_jobs(tenant_id,created_by,shop_id) "
                "VALUES(:tenant,:user,:shop) RETURNING id"
            ),
            {"tenant": data["tenant"], "user": data["user"], "shop": data["shop"]},
        ).scalar_one()
    with pytest.raises(ApiError) as absent:
        data["applications"].get(legacy)
    assert absent.value.code == ErrorCode.NO_DATA
    with (
        pytest.raises(DBAPIError),
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(
            text(
                "INSERT INTO command_jobs(tenant_id,created_by,shop_id,document) "
                "VALUES(:tenant,:user,:shop,'{}'::jsonb)"
            ),
            {"tenant": data["tenant"], "user": data["user"], "shop": data["shop"]},
        )
    with (
        pytest.raises(DBAPIError),
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(text("UPDATE job_events SET to_status='SUCCEEDED'"))
