"""真实PG副作用/首结果/恢复；真实子进程中断，不执行任何供应商调用。"""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from yuliang.modules.command.applications import JobApplications
from yuliang.modules.command.jobs import CreateJobRequest
from yuliang.modules.execution.idempotency import IdempotencyLedger
from yuliang.modules.execution.idempotency_authorization import CommandAuthorization
from yuliang.modules.execution.idempotency_models import (
    ApiOperation,
    OwnershipTicket,
    ResponseSnapshot,
)
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

from .test_capability_evidence import capability as capability
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import health_ready as health_ready
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration


def service(data: dict[str, Any]) -> IdempotencyLedger:
    return IdempotencyLedger(
        data["engine"], data["identity"], uuid4(), CommandAuthorization(data["applications"])
    )


def payload(data: dict[str, Any]) -> dict[str, Any]:
    value: CreateJobRequest = data["input"]
    return value.model_dump(mode="json")


def handler(data: dict[str, Any], session: Session) -> ResponseSnapshot:
    repository: JobApplications = data["applications"]
    job = repository.create(data["input"], unit_of_work=session)
    return ResponseSnapshot(status=201, body=job.model_dump(mode="json"))


def counts(data: dict[str, Any]) -> tuple[int, int]:
    with business_transaction(data["engine"], data["identity"]) as session:
        return (
            session.execute(
                text("SELECT count(*) FROM command_jobs WHERE document IS NOT NULL")
            ).scalar_one(),
            session.execute(text("SELECT count(*) FROM api_idempotency")).scalar_one(),
        )


def ticket_for(
    data: dict[str, Any], key: str = "same-api-key"
) -> tuple[IdempotencyLedger, OwnershipTicket]:
    ledger = service(data)
    start = ledger.begin(ApiOperation.CREATE, key, payload(data))
    assert start.state == "ACQUIRED" and start.ticket is not None
    assert start.ticket.secret.get_secret_value() not in repr(start)
    assert "secret" not in start.model_dump_json()
    return ledger, start.ticket


def expire(data: dict[str, Any], ticket: OwnershipTicket) -> None:
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE api_idempotency SET lease_until=clock_timestamp()-"
                "interval '1 second' WHERE id=:id"
            ),
            {"id": ticket.record_id},
        )


def test_first_result_reused_and_payload_mismatch_no_side_effect(
    applications: dict[str, Any],
) -> None:
    data = applications
    ledger, ticket = ticket_for(data)
    assert ledger.lookup(ApiOperation.CREATE, "same-api-key").state == "IN_FLIGHT"
    first = ledger.execute(ticket, lambda session: handler(data, session))
    repeat = ledger.begin(
        ApiOperation.CREATE, "same-api-key", dict(reversed(list(payload(data).items())))
    )
    assert repeat.state == "COMPLETED" and repeat.response == first and counts(data) == (1, 1)
    changed = payload(data) | {"intent": "不同请求载荷"}
    with pytest.raises(ApiError) as error:
        ledger.begin(ApiOperation.CREATE, "same-api-key", changed)
    assert error.value.code == ErrorCode.IDEMPOTENCY_MISMATCH and counts(data) == (1, 1)
    if evidence := os.getenv("YULIANG_IDEMPOTENCY_EVIDENCE"):
        Path(evidence).write_text(
            json.dumps(
                {
                    "recordId": str(ticket.record_id),
                    "jobId": first.body["job_id"],
                    "operation": "JOB_CREATE",
                    "status": first.status,
                    "replayed": "COMPLETED",
                    "businessRows": 1,
                    "ledgerRows": 1,
                    "platformCalls": 0,
                    "paidCalls": 0,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def test_concurrent_begin_one_owner_and_explainable_in_flight(applications: dict[str, Any]) -> None:
    data = applications
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: service(data).begin(ApiOperation.CREATE, "concurrent-key", payload(data)),
                range(4),
            )
        )
    assert sum(item.state == "ACQUIRED" for item in results) == 1
    assert sum(item.state == "IN_FLIGHT" for item in results) == 3 and counts(data) == (0, 1)


def test_running_transaction_cannot_be_recovered_or_reexecuted(
    applications: dict[str, Any],
) -> None:
    data = applications
    ledger, ticket = ticket_for(data)
    entered, release = Event(), Event()

    def slow(session: Session) -> ResponseSnapshot:
        response = handler(data, session)
        entered.set()
        assert release.wait(10)
        return response

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(ledger.execute, ticket, slow)
        assert entered.wait(10)
        try:
            assert ledger.lookup(ApiOperation.CREATE, "same-api-key").state == "IN_FLIGHT"
            for attempt in (
                lambda: ledger.execute(ticket, lambda session: handler(data, session)),
                lambda: ledger.recover(ApiOperation.CREATE, "same-api-key"),
            ):
                with pytest.raises(ApiError) as error:
                    attempt()
                assert error.value.code == ErrorCode.LOCKED
        finally:
            release.set()
        first = pending.result(timeout=10)
    assert ledger.execute(ticket, lambda session: handler(data, session)) == first and counts(
        data
    ) == (1, 1)


def test_business_failure_rolls_back_and_recovery_changes_owner(
    applications: dict[str, Any],
) -> None:
    data = applications
    ledger, ticket = ticket_for(data)

    def fail(session: Session) -> ResponseSnapshot:
        handler(data, session)
        raise RuntimeError("synthetic interrupted transaction")

    with pytest.raises(RuntimeError):
        ledger.execute(ticket, fail)
    assert (
        counts(data) == (0, 1)
        and ledger.lookup(ApiOperation.CREATE, "same-api-key").state == "IN_FLIGHT"
    )
    expire(data, ticket)
    resumed = ledger.recover(ApiOperation.CREATE, "same-api-key")
    assert resumed.ticket is not None
    with pytest.raises(ApiError) as error:
        ledger.execute(ticket, lambda session: handler(data, session))
    assert error.value.code == ErrorCode.VERSION_CONFLICT
    ledger.execute(resumed.ticket, lambda session: handler(data, session))
    assert counts(data) == (1, 1)


def test_real_process_exit_keeps_original_key_and_rolls_back_business(
    applications: dict[str, Any],
) -> None:
    data = applications
    ledger, ticket = ticket_for(data)
    actor = data["identity"]
    env = os.environ | {
        "YULIANG_IDEM_PROBE_DB_URL": data["engine"].url.render_as_string(hide_password=False),
        "YULIANG_IDEM_PROBE_IDENTITY": json.dumps(
            {
                "tenant": actor.tenant_id,
                "member": str(actor.membership_id),
                "user": str(actor.user_id),
            }
        ),
        "YULIANG_IDEM_PROBE_AUDIT_KEY": data["applications"].key.get_secret_value(),
        "YULIANG_IDEM_PROBE_PAYLOAD": json.dumps(payload(data)),
        "YULIANG_IDEM_PROBE_RECORD": str(ticket.record_id),
        "YULIANG_IDEM_PROBE_OWNER": ticket.secret.get_secret_value(),
    }
    result = subprocess.run(  # noqa: S603 -- 固定本地中断探针，无任意shell命令。
        [sys.executable, str(Path(__file__).parents[1] / "helpers/idempotency_crash.py")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 17 and result.stdout.strip() == "BUSINESS_WRITTEN_UNCOMMITTED"
    assert (
        counts(data) == (0, 1)
        and ledger.lookup(ApiOperation.CREATE, "same-api-key").record_id == ticket.record_id
    )
    expire(data, ticket)
    resumed = ledger.recover(ApiOperation.CREATE, "same-api-key")
    assert resumed.ticket is not None
    ledger.execute(resumed.ticket, lambda session: handler(data, session))
    assert counts(data) == (1, 1)


def test_known_4xx_keeps_first_failure_and_rolls_back_partial_writes(
    applications: dict[str, Any],
) -> None:
    data = applications
    ledger, ticket = ticket_for(data)

    def rejected(session: Session) -> ResponseSnapshot:
        handler(data, session)
        raise ApiError(ErrorCode.RISK_BLOCKED, "合成业务拒绝")

    response = ledger.execute(ticket, rejected)
    assert response.status == 422 and response.body["error"]["code"] == "RISK_BLOCKED"
    assert ledger.execute(ticket, lambda session: handler(data, session)) == response and counts(
        data
    ) == (0, 1)


def test_completed_result_still_checks_current_permission_and_history_not_deleted(
    applications: dict[str, Any],
) -> None:
    data = applications
    ledger, ticket = ticket_for(data)
    response = ledger.execute(ticket, lambda session: handler(data, session))
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE api_idempotency SET created_at=created_at-interval '2 years' WHERE id=:id"
            ),
            {"id": ticket.record_id},
        )
    assert ledger.lookup(ApiOperation.CREATE, "same-api-key").response == response
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:id"),
            {"id": data["identity"].membership_id},
        )
    with pytest.raises(ApiError):
        ledger.lookup(ApiOperation.CREATE, "same-api-key")
    with pytest.raises(ApiError):
        ledger.execute(ticket, lambda session: handler(data, session))


def test_completion_audit_failure_does_not_commit_business_or_result(
    applications: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    data = applications
    ledger, ticket = ticket_for(data)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("synthetic completion audit failure")

    monkeypatch.setattr("yuliang.modules.execution.idempotency.record", fail)
    with pytest.raises(RuntimeError):
        ledger.execute(ticket, lambda session: handler(data, session))
    assert (
        counts(data) == (0, 1)
        and ledger.lookup(ApiOperation.CREATE, "same-api-key").state == "IN_FLIGHT"
    )


def test_runtime_cannot_replace_completed_result_or_remove_key_and_no_auth_is_closed(
    applications: dict[str, Any],
) -> None:
    data = applications
    for query in (
        "DELETE FROM api_idempotency",
        "UPDATE api_idempotency SET response_body='{}'::jsonb",
    ):
        with (
            pytest.raises(DBAPIError),
            business_transaction(data["engine"], data["identity"]) as session,
        ):
            session.execute(text(query))
    with pytest.raises(ApiError) as error:
        IdempotencyLedger(data["engine"], data["identity"], uuid4()).begin(
            ApiOperation.CREATE, "closed", payload(data)
        )
    assert error.value.code == ErrorCode.CAPABILITY_UNAVAILABLE


def test_operation_and_caller_namespaces_do_not_share_key(applications: dict[str, Any]) -> None:
    data = applications
    ledger = service(data)
    first = ledger.begin(ApiOperation.CREATE, "namespace-key", payload(data))
    job = data["applications"].create(data["input"])
    other = ledger.begin(
        ApiOperation.CONFIRM, "namespace-key", {"job_id": job.job_id, "expected_version": 1}
    )
    assert first.record_id != other.record_id
    actor, member = uuid4(), uuid4()
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO users(id,email,display_name) "
                "VALUES(:id,:email,'Synthetic idempotency caller')"
            ),
            {"id": actor, "email": f"{actor}@example.invalid"},
        )
        owner.execute(
            text("INSERT INTO memberships(id,tenant_id,user_id) VALUES(:id,:tenant,:user)"),
            {"id": member, "tenant": data["tenant"], "user": actor},
        )
        owner.execute(
            text(
                "INSERT INTO role_grants(tenant_id,membership_id,role_id) "
                "SELECT tenant_id,:new,role_id FROM role_grants WHERE membership_id=:old"
            ),
            {"new": member, "old": data["identity"].membership_id},
        )
        owner.execute(
            text(
                "INSERT INTO data_scopes(tenant_id,membership_id,scope_type) "
                "VALUES(:tenant,:member,'ALL_TENANT')"
            ),
            {"tenant": data["tenant"], "member": member},
        )
    identity = VerifiedIdentity(data["tenant"], member, actor)
    apps = JobApplications(data["engine"], identity, uuid4(), data["applications"].key)
    separate = IdempotencyLedger(data["engine"], identity, uuid4(), CommandAuthorization(apps))
    second = separate.begin(ApiOperation.CREATE, "namespace-key", payload(data))
    assert second.record_id not in {first.record_id, other.record_id}


def test_same_key_across_tenants_and_cross_tenant_record_access_denied(
    applications: dict[str, Any],
) -> None:
    data = applications
    first = service(data).begin(ApiOperation.CREATE, "cross-tenant-key", payload(data))
    member, role, shop, account, campaign = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    actor = data["identity"].user_id
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        tenant = owner.execute(
            text("INSERT INTO tenants(name) VALUES('Synthetic second namespace') RETURNING id")
        ).scalar_one()
        owner.execute(
            text("INSERT INTO memberships(id,tenant_id,user_id) VALUES(:member,:tenant,:user)"),
            {"member": member, "tenant": tenant, "user": actor},
        )
        owner.execute(
            text(
                "INSERT INTO roles(id,tenant_id,code,name) "
                "VALUES(:role,:tenant,'OPERATOR','Synthetic namespace role')"
            ),
            {"role": role, "tenant": tenant},
        )
        owner.execute(
            text("""INSERT INTO role_permissions(tenant_id,role_id,permission_code)
            SELECT DISTINCT :tenant,:role,p.permission_code FROM role_permissions p
            JOIN role_grants g
            ON (g.tenant_id,g.role_id)=(p.tenant_id,p.role_id) WHERE g.membership_id=:original"""),
            {"tenant": tenant, "role": role, "original": data["identity"].membership_id},
        )
        owner.execute(
            text(
                "INSERT INTO role_grants(tenant_id,membership_id,role_id) "
                "VALUES(:tenant,:member,:role)"
            ),
            {"tenant": tenant, "member": member, "role": role},
        )
        owner.execute(
            text(
                "INSERT INTO data_scopes(tenant_id,membership_id,scope_type) "
                "VALUES(:tenant,:member,'ALL_TENANT')"
            ),
            {"tenant": tenant, "member": member},
        )
        owner.execute(
            text(
                "INSERT INTO shops(id,tenant_id,platform,native_id,name) "
                "VALUES(:shop,:tenant,'wxshop','namespace','Synthetic shop')"
            ),
            {"shop": shop, "tenant": tenant},
        )
        owner.execute(
            text(
                "INSERT INTO ad_accounts(id,tenant_id,provider,product_line,native_id,name) "
                "VALUES(:account,:tenant,'tencent','adq','namespace','Synthetic account')"
            ),
            {"account": account, "tenant": tenant},
        )
        owner.execute(
            text(
                "INSERT INTO entity_links(tenant_id,shop_id,account_id) "
                "VALUES(:tenant,:shop,:account)"
            ),
            {"tenant": tenant, "shop": shop, "account": account},
        )
        owner.execute(
            text(
                "INSERT INTO campaigns(id,tenant_id,shop_id,account_id,"
                "provider,product_line,native_id) "
                "VALUES(:campaign,:tenant,:shop,:account,'tencent','adq','456')"
            ),
            {"campaign": campaign, "tenant": tenant, "shop": shop, "account": account},
        )
    identity = VerifiedIdentity(tenant, member, actor)
    apps = JobApplications(data["engine"], identity, uuid4(), data["applications"].key)
    ledger = IdempotencyLedger(data["engine"], identity, uuid4(), CommandAuthorization(apps))
    value = payload(data) | {"shop_id": str(shop), "target_id": str(campaign)}
    separate = ledger.begin(ApiOperation.CREATE, "cross-tenant-key", value)
    assert separate.record_id != first.record_id
    assert ledger.lookup(ApiOperation.CREATE, "cross-tenant-key").record_id == separate.record_id
    assert first.ticket is not None
    with pytest.raises(ApiError) as error:
        ledger.execute(first.ticket, lambda session: handler(data, session))
    assert error.value.code == ErrorCode.NO_DATA
