"""实际批准来源+PG派发；上游仅测试固定DTO内存响应，生产registry为空。"""

from dataclasses import replace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from yuliang.modules.connectors.archive import RecordedPlatformClient
from yuliang.modules.connectors.http import PlatformHttpClient
from yuliang.modules.execution.dispatch_runtime import DispatchExecutor, DispatchRuntime
from yuliang.modules.execution.dispatch_service import (
    ApprovedExecutionDispatch,
    DispatchMessageRejected,
    RecoveryHandoffPending,
)
from yuliang.platform.db import business_transaction

from .test_approval_execution_outbox import (
    applications as applications,
)
from .test_approval_execution_outbox import (
    budget_ready as budget_ready,
)
from .test_approval_execution_outbox import (
    capability as capability,
)
from .test_approval_execution_outbox import (
    database as database,
)
from .test_approval_execution_outbox import (
    governance as governance,
)
from .test_approval_execution_outbox import (
    health_ready as health_ready,
)
from .test_approval_execution_outbox import (
    login_api as login_api,
)
from .test_approval_execution_outbox import (
    oauth_ready as oauth_ready,
)
from .test_approval_execution_outbox import (
    pending,
    registered,
    request,
    reviewer,
)
from .test_approval_execution_outbox import (
    risk_ready as risk_ready,
)
from .test_approval_execution_outbox import (
    sync_ready as sync_ready,
)
from .test_execution_attempts import SyntheticBudgetSerializer
from .test_platform_payloads import decoder, signer

pytestmark = pytest.mark.integration


def event(d: dict[str, Any]) -> Any:
    job = pending(d)
    reviewer(d).review(request(job))
    return registered(d, job.job_id)[1]


def service(d: dict[str, Any], runtime: DispatchRuntime | None = None) -> ApprovedExecutionDispatch:
    return ApprovedExecutionDispatch(d["engine"], d["applications"].key, runtime)


def runtime(d: dict[str, Any], calls: list[str]) -> DispatchRuntime:
    registry = DispatchRuntime()

    def upstream(wire: httpx.Request) -> httpx.Response:
        with business_transaction(d["engine"], d["identity"]) as s:
            row = s.execute(text("SELECT state,dispatch_consumed_at FROM execution_attempts")).one()
            assert row.state == "SENT" and row.dispatch_consumed_at is not None
            assert (
                s.execute(
                    text("SELECT count(*) FROM platform_requests WHERE effect='WRITE'")
                ).scalar_one()
                == 1
            )
        calls.append(wire.method)
        return httpx.Response(200, json=dict(code=0, trace_id="synthetic-dispatch"))

    client = RecordedPlatformClient(
        PlatformHttpClient(signer, transport=httpx.MockTransport(upstream)),
        d["archive"],
        decoder=decoder,
    )

    def factory(s: Any, identity: Any, source: Any) -> DispatchExecutor:
        assert identity == d["identity"]
        return DispatchExecutor(
            replace(d["ctx"], identity=identity),
            SyntheticBudgetSerializer(d["spec"]),
            client,
            lambda: SecretStr("synthetic"),
            d["target"],
        )

    registry.register("CHANGE_DAILY_BUDGET", d["ctx"].provider, d["ctx"].product_line, factory)
    return registry


def attempts(d: dict[str, Any]) -> int:
    with business_transaction(d["engine"], d["identity"]) as s:
        return int(s.execute(text("SELECT count(*) FROM execution_attempts")).scalar_one())


def test_default_closed_is_durable_and_later_registry_cannot_activate_old_event(
    budget_ready: dict[str, Any],
) -> None:
    d = budget_ready
    item = event(d)
    first = service(d).handle(item)
    assert first["state"] == "BLOCKED_EXECUTOR" and attempts(d) == 0
    calls: list[str] = []
    assert service(d, runtime(d, calls)).handle(item) == first
    assert calls == [] and attempts(d) == 0
    assert d["applications"].get(item.job_id).status == "APPROVED"


def test_actual_source_original_actor_sends_once_and_records_receipt(
    budget_ready: dict[str, Any],
) -> None:
    d = budget_ready
    item = event(d)
    calls: list[str] = []
    dispatcher = service(d, runtime(d, calls))
    first = dispatcher.handle(item)
    assert first["state"] == "RECEIPT_RECORDED" and calls == ["POST"]
    assert dispatcher.handle(item) == first and attempts(d) == 1
    assert set(first) == {"event_id", "job_id", "state", "attempt_id", "reason"}
    with business_transaction(d["engine"], d["identity"]) as s:
        row = s.execute(text("SELECT actor_id,membership_id FROM execution_attempts")).one()
        assert row.actor_id == item.user_id != d["approver"]
        assert row.membership_id == item.membership_id
    assert d["applications"].get(item.job_id).status == "VERIFYING"


@pytest.mark.parametrize("field", ["event_id", "job_id", "user_id", "membership_id"])
def test_broker_ids_cannot_manufacture_approved_source(
    budget_ready: dict[str, Any], field: str
) -> None:
    d = budget_ready
    item = event(d).model_copy(update={field: uuid4()})
    with pytest.raises(DispatchMessageRejected):
        service(d).handle(item)
    assert attempts(d) == 0


@pytest.mark.parametrize("change", ["inactive", "permission", "version"])
def test_real_source_current_permission_or_version_blocks_without_send(
    budget_ready: dict[str, Any], change: str
) -> None:
    d = budget_ready
    item = event(d)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if change == "inactive":
            owner.execute(
                text("UPDATE memberships SET status='REMOVED' WHERE id=:id"),
                dict(id=item.membership_id),
            )
        elif change == "permission":
            owner.execute(
                text("DELETE FROM role_grants WHERE membership_id=:id"), dict(id=item.membership_id)
            )
        else:
            owner.execute(
                text(
                    "UPDATE command_jobs SET expires_at=created_at+interval '1 microsecond' "
                    "WHERE id=:id"
                ),
                dict(id=item.job_id),
            )
    calls: list[str] = []
    result = service(d, runtime(d, calls)).handle(item)
    assert result["state"] == "BLOCKED_AUTH" and calls == []
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert (
            owner.execute(
                text("SELECT count(*) FROM execution_attempts WHERE tenant_id=:tenant"),
                dict(tenant=d["tenant"]),
            ).scalar_one()
            == 0
        )


def test_committed_sent_lost_ticket_redelivery_only_checks_recovery(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    d = budget_ready
    item = event(d)
    calls: list[str] = []
    dispatcher = service(d, runtime(d, calls))

    def crash(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic process boundary after SENT commit")

    with monkeypatch.context() as patch:
        patch.setattr(dispatcher, "_send", crash)
        with pytest.raises(RuntimeError):
            dispatcher.handle(item)
    assert attempts(d) == 1 and calls == []
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE execution_recoveries SET outcome='token_secret_stays_DB' WHERE job_id=:job"
            ),
            dict(job=item.job_id),
        )
    result = dispatcher.handle(item)
    assert result["state"] == "RECOVERY_REQUIRED" and result["recovery"]["attempt_id"] == str(
        result["attempt_id"]
    )
    assert attempts(d) == 1 and calls == []
    assert "token_secret_stays_DB" not in str(result)
    assert "outcome" not in result["recovery"]


def test_receipt_handoff_confirm_failure_replay_and_no_second_write(
    budget_ready: dict[str, Any],
) -> None:
    d = budget_ready
    item = event(d)
    calls: list[str] = []
    dispatcher = service(d, runtime(d, calls))
    assert dispatcher.handle(item)["state"] == "RECEIPT_RECORDED"
    notices: list[Any] = []

    def failed(*args: Any) -> None:
        raise RuntimeError("synthetic broker confirm failure")

    with pytest.raises(RecoveryHandoffPending):
        dispatcher.publish_recovery(item, failed)
    assert dispatcher.handle(item)["state"] == "RECEIPT_RECORDED"

    def sender(identifier: Any, name: Any, payload: Any, due: Any) -> None:
        # 独立连接可见claim已提交，且仅当前attempt对应的恢复事件。
        with business_transaction(d["engine"], d["identity"]) as s:
            row = s.execute(
                text("SELECT state,lease_owner FROM outbox_events WHERE event_id=:id"),
                dict(id=identifier),
            ).one()
            assert row.state == "PENDING" and row.lease_owner is not None
        assert name == "yuliang.system.execution_recovery"
        assert payload["job_id"] == str(item.job_id)
        notices.append(identifier)

    first = dispatcher.publish_recovery(item, sender)
    assert first["state"] == "PUBLISHED" and len(notices) == 1
    assert dispatcher.publish_recovery(item, sender) == first
    assert len(notices) == 1 and calls == ["POST"] and attempts(d) == 1


def test_default_closed_has_no_recovery_to_publish(budget_ready: dict[str, Any]) -> None:
    d = budget_ready
    item = event(d)
    dispatcher = service(d)
    assert dispatcher.handle(item)["state"] == "BLOCKED_EXECUTOR"

    def forbidden(*args: Any) -> None:
        raise AssertionError("closed runtime must not publish recovery")

    assert dispatcher.publish_recovery(item, forbidden)["state"] == "NO_ATTEMPT"


def test_handoff_inactive_original_actor_only_records_recovery_block(
    budget_ready: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    d = budget_ready
    item = event(d)
    calls: list[str] = []
    dispatcher = service(d, runtime(d, calls))
    with monkeypatch.context() as patch:
        patch.setattr(
            dispatcher, "_send", lambda *args: (_ for _ in ()).throw(RuntimeError("crash"))
        )
        with pytest.raises(RuntimeError):
            dispatcher.handle(item)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE memberships SET status='REMOVED' WHERE id=:id"),
            dict(id=item.membership_id),
        )

    def forbidden(*args: Any) -> None:
        raise AssertionError("inactive actor cannot publish execution recovery")

    result = dispatcher.publish_recovery(item, forbidden)
    assert result["state"] == "BLOCKED_AUTH" and calls == []
    assert dispatcher.publish_recovery(item, forbidden)["state"] == "TERMINAL"
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert (
            owner.execute(
                text("SELECT state FROM execution_recoveries WHERE job_id=:id"),
                dict(id=item.job_id),
            ).scalar_one()
            == "BLOCKED_AUTH"
        )
        assert (
            owner.execute(
                text("SELECT status FROM command_jobs WHERE id=:id"), dict(id=item.job_id)
            ).scalar_one()
            == "EXECUTING"
        )
