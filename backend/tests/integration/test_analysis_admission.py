"""实际PG受理、Outbox/Inbox和HTTP原键恢复；无计算/平台/付费调用。"""

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from kombu import Queue  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.infrastructure.outbox import celery_sender, consume, publish_one
from yuliang.modules.analysis.models import AnalysisCreate
from yuliang.modules.analysis.service import AnalysisService
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode
from yuliang.worker.app import KINDS, create_celery

from .test_adq_reports import configuration_ready as configuration_ready
from .test_adq_reports import contract, query
from .test_adq_reports import governance as governance
from .test_adq_reports import legacy as legacy
from .test_adq_reports import oauth_ready as oauth_ready
from .test_adq_reports import reports as reports
from .test_capability_evidence import capability as capability
from .test_identity_database import database as database
from .test_password_sessions import challenge
from .test_password_sessions import login_api as login_api

pytestmark = pytest.mark.integration
PREFIX = "/v1/business-analysis"


@pytest.fixture
async def analysis_ready(reports: dict[str, Any]) -> dict[str, Any]:
    data = reports
    # configuration_ready已实际登录，不重复消费登录防护令牌
    contract(data)
    data["state"].update(cost=0, gmv=0)
    await data["reader"].sync(
        data["contexts"]["DAILY"], uuid4(), query(start_date="2026-10-01", end_date="2026-10-07")
    )
    value = AnalysisCreate.model_validate(
        dict(
            query=dict(
                shopId=str(data["shop"]),
                platformId="adq",
                startDate="2026-10-01",
                endDate="2026-10-07",
                dimensions=["ads"],
                timezone="Asia/Shanghai",
            ),
            idempotencyKey=str(uuid4()),
        )
    )
    return data | {"analysis": AnalysisService(data["engine"], data["identity"]), "input": value}


@pytest.fixture
def analysis_worker(analysis_ready: dict[str, Any]) -> Iterator[dict[str, Any]]:
    data = analysis_ready
    prefix = "test-analysis-" + uuid4().hex
    settings = data["settings"].model_copy(update={"worker_queue_prefix": prefix})
    client = create_celery(settings)
    env = os.environ.copy()
    env.update(
        YULIANG_DATABASE_URL=data["database"]["app"],
        YULIANG_AUTH_DATABASE_URL=data["database"]["auth"],
        YULIANG_REDIS_URL=settings.redis_url.get_secret_value(),
        YULIANG_BROKER_URL=settings.broker_url.get_secret_value(),
        YULIANG_SESSION_SECRET=settings.session_secret.get_secret_value(),
        YULIANG_WORKER_QUEUE_PREFIX=prefix,
    )
    hostname = prefix + "@" + socket.gethostname()
    process = subprocess.Popen(  # noqa: S603 -- 固定隔离队列Worker，凭据仅环境。
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "yuliang.worker.app:app",
            "worker",
            "--pool=prefork",
            "--concurrency=1",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
            "--loglevel=ERROR",
            "--hostname=" + hostname,
            "--queues=" + ",".join(prefix + "." + kind for kind in KINDS),
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    results: list[Any] = []
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            assert process.poll() is None, "分析Worker在就绪前退出"
            if client.control.ping(destination=[hostname], timeout=0.5):
                break
            time.sleep(0.2)
        else:
            pytest.fail("分析Worker未就绪")
        yield data | {"broker_client": client, "worker_results": results}
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        for item in results:
            item.forget()
        with client.connection_for_write() as connection:
            for kind in KINDS:
                Queue(prefix + "." + kind)(connection).delete()
            connection.channel().exchange_delete(exchange=prefix, if_unused=True)
        client.close()


def counts(data: dict[str, Any]) -> tuple[int, int, int, int]:
    with business_transaction(data["engine"], data["identity"]) as session:
        row = session.execute(
            text(
                "SELECT (SELECT count(*) FROM analysis_tasks), "
                "(SELECT count(*) FROM analysis_results), "
                "(SELECT count(*) FROM analysis_deliveries), "
                "(SELECT count(*) FROM outbox_events)"
            )
        ).one()
        return row[0], row[1], row[2], row[3]


def notice(data: dict[str, Any], task: UUID) -> tuple[UUID, dict[str, Any]]:
    with business_transaction(data["engine"], data["identity"]) as session:
        row = session.execute(
            text(
                "SELECT e.event_id,e.payload FROM outbox_events e JOIN analysis_tasks t "
                "ON e.event_id=t.transport_event_id WHERE t.id=:id"
            ),
            {"id": task},
        ).one()
        return row.event_id, row.payload


def test_http_original_key_reopen_and_readonly_history(analysis_ready: dict[str, Any]) -> None:
    data = analysis_ready
    client = data["client"]
    payload = data["input"].model_dump(mode="json", by_alias=True)
    headers = {"X-CSRF-Token": challenge(client)}
    first = client.post(PREFIX + "/tasks", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    task = first.json()["data"]
    assert task["status"] == "queued" and task["progress"] == 0 and "resultId" not in task
    assert client.post(PREFIX + "/tasks", json=payload, headers=headers).json()["data"] == task
    for _ in range(3):
        assert client.get(PREFIX + "/tasks/" + task["id"]).json()["data"] == task
    assert client.get(PREFIX + "/tasks").json()["data"] == [task]
    assert client.get(PREFIX + "/results").json()["data"] == []
    assert client.get(PREFIX + "/results/" + str(uuid4())).status_code == 404
    assert counts(data) == (1, 0, 0, 1)
    assert first.headers["cache-control"] == "no-store"
    if target := os.getenv("YULIANG_ANALYSIS_EVIDENCE"):
        Path(target).write_text(
            json.dumps(
                {
                    "task": task,
                    "input": payload,
                    "identity": client.get("/v1/auth/session").json()["data"],
                    "results": [],
                    "scope": "SYNTHETIC_IDENTITIES_ACTUAL_PG_COOKIE_HTTP_NO_COMPUTATION",
                    "platformCalls": 0,
                    "paidCalls": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def test_same_key_race_and_reordered_dimensions_keep_original_task(
    analysis_ready: dict[str, Any],
) -> None:
    data, service, value = analysis_ready, analysis_ready["analysis"], analysis_ready["input"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = list(pool.map(lambda _: service.create(value), range(2)))
    assert tasks[0] == tasks[1] and counts(data) == (1, 0, 0, 1)
    reordered = AnalysisCreate.model_validate(
        value.model_dump() | {"query": value.query.model_dump() | {"dimensions": ("ads",)}}
    )
    assert service.create(reordered) == tasks[0]
    changed = AnalysisCreate.model_validate(
        value.model_dump() | {"query": value.query.model_dump() | {"end_date": "2026-10-08"}}
    )
    with pytest.raises(ApiError) as conflict:
        service.create(changed)
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_MISMATCH


def test_actual_outbox_publish_failure_inbox_rollback_and_duplicate_delivery(
    analysis_ready: dict[str, Any],
) -> None:
    data = analysis_ready
    task = data["analysis"].create(data["input"])
    event, payload = notice(data, task.id)
    assert set(payload) == {"event_id", "tenant_id", "job_id", "membership_id", "user_id"}

    def offline(*args: Any) -> None:
        raise ConnectionError("synthetic transport failure")

    assert not publish_one(data["engine"], data["identity"], event, offline)
    delivered: list[dict[str, Any]] = []
    assert publish_one(
        data["engine"], data["identity"], event, lambda event_id, name, body: delivered.append(body)
    )
    assert delivered == [payload]

    def receive(session: Any) -> None:
        session.execute(text("SELECT access.analysis_receive(:id)"), {"id": event})

    def interrupted(session: Any) -> None:
        receive(session)
        raise RuntimeError("synthetic crash before commit")

    with pytest.raises(RuntimeError):
        consume(data["engine"], data["identity"], "analysis.accept", event, interrupted)
    assert counts(data) == (1, 0, 0, 1)
    assert consume(data["engine"], data["identity"], "analysis.accept", event, receive)
    assert not consume(data["engine"], data["identity"], "analysis.accept", event, receive)
    assert counts(data) == (1, 0, 1, 1)
    assert data["analysis"].get(task.id) == task


def test_private_user_and_tenant_history_and_current_scope_revocation(
    analysis_ready: dict[str, Any],
) -> None:
    data = analysis_ready
    task = data["analysis"].create(data["input"])
    other = AnalysisService(
        data["engine"], VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"])
    )
    assert other.tasks() == () and other.results() == []
    with pytest.raises(ApiError):
        other.get(task.id)
    alien = AnalysisService(
        data["engine"], VerifiedIdentity(data["tenant"] + 1, data["member"], data["user"])
    )
    assert alien.tasks() == ()
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:member"), {"member": data["member"]}
        )
    assert data["analysis"].tasks() == ()
    with pytest.raises(ApiError):
        data["analysis"].create(data["input"])


def test_runtime_cannot_forge_status_result_or_admit_unauthorized_query(
    analysis_ready: dict[str, Any],
) -> None:
    data = analysis_ready
    data["analysis"].create(data["input"])
    with (
        pytest.raises(DBAPIError) as denied,
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(text("UPDATE analysis_tasks SET status='succeeded',progress=100"))
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    with (
        pytest.raises(DBAPIError) as denied_result,
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(text("DELETE FROM analysis_results"))
    assert getattr(denied_result.value.orig, "sqlstate", None) == "42501"
    with (
        pytest.raises(DBAPIError),
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(
            text("SELECT access.analysis_admit(CAST(:query AS jsonb),:key)"),
            {"query": json.dumps({"status": "succeeded"}), "key": str(uuid4())},
        )
    for change in ({"shop_id": uuid4()}, {"platform_id": "juguang"}):
        value = AnalysisCreate.model_validate(
            data["input"].model_dump() | {"query": data["input"].query.model_dump() | change}
        )
        with pytest.raises(ApiError) as error:
            data["analysis"].create(value)
        assert error.value.code == ErrorCode.FORBIDDEN


def test_http_csrf_origin_body_overrides_and_cookie_rejected(
    analysis_ready: dict[str, Any],
) -> None:
    data, client = analysis_ready, analysis_ready["client"]
    payload = data["input"].model_dump(mode="json", by_alias=True)
    assert client.post(PREFIX + "/tasks", json=payload).status_code == 403
    assert (
        client.get(PREFIX + "/results", headers={"Origin": "https://other.invalid"}).status_code
        == 403
    )
    assert client.get(PREFIX + "/tasks?tenantId=1").status_code == 422
    headers = {"X-CSRF-Token": challenge(client)}
    assert (
        client.post(
            PREFIX + "/tasks", json=payload | {"userId": str(uuid4())}, headers=headers
        ).status_code
        == 422
    )
    client.cookies.clear()
    assert client.get(PREFIX + "/results").status_code == 401


def test_real_rabbitmq_prefork_delivery_and_duplicate_keep_original_queued_task(
    analysis_worker: dict[str, Any],
) -> None:
    data = analysis_worker
    task = data["analysis"].create(data["input"])
    event, payload = notice(data, task.id)
    client = data["broker_client"]
    assert publish_one(data["engine"], data["identity"], event, celery_sender(client))
    first = client.AsyncResult(str(event))
    data["worker_results"].append(first)
    first_result = first.get(timeout=15)
    assert first_result["deliveryRecorded"] is True
    duplicate = client.send_task(
        "yuliang.system.analysis_accept", kwargs={"payload": payload}, kwargsrepr="<ids-only>"
    )
    data["worker_results"].append(duplicate)
    duplicate_result = duplicate.get(timeout=15)
    assert duplicate_result["deliveryRecorded"] is False
    assert data["analysis"].get(task.id) == task and counts(data) == (1, 0, 1, 1)
    forged = client.send_task(
        "yuliang.system.analysis_accept",
        kwargs={"payload": payload | {"job_id": str(uuid4())}},
        kwargsrepr="<ids-only>",
    )
    data["worker_results"].append(forged)
    with pytest.raises(ValueError):
        forged.get(timeout=15)
    assert counts(data) == (1, 0, 1, 1)
    if target := os.getenv("YULIANG_ANALYSIS_QUEUE_EVIDENCE"):
        Path(target).write_text(
            json.dumps(
                {
                    "taskId": str(task.id),
                    "eventId": str(event),
                    "transport": "REAL_RABBITMQ_PREFORK_WORKER_REDIS_PG",
                    "first": first_result,
                    "duplicate": duplicate_result,
                    "forgedMessage": "REJECTED_VALUE_ERROR",
                    "taskBeforeAndAfter": task.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ),
                    "counts": counts(data),
                    "platformCalls": 0,
                    "paidCalls": 0,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def test_atomic_admission_rollback_does_not_leave_event_or_orphan_task(
    analysis_ready: dict[str, Any],
) -> None:
    data = analysis_ready
    with (
        pytest.raises(RuntimeError),
        business_transaction(data["engine"], data["identity"]) as session,
    ):
        session.execute(
            text("SELECT access.analysis_admit(CAST(:query AS jsonb),:key)"),
            {
                "query": data["input"].query.model_dump_json(by_alias=True),
                "key": data["input"].idempotency_key,
            },
        )
        raise RuntimeError("synthetic lost response before commit")
    assert counts(data) == (0, 0, 0, 0)
    task = data["analysis"].create(data["input"])
    assert task.status == "queued" and counts(data) == (1, 0, 0, 1)


def test_same_key_for_another_authorized_user_is_independent_and_private(
    analysis_ready: dict[str, Any],
) -> None:
    data = analysis_ready
    original = data["analysis"].create(data["input"])
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO role_grants(tenant_id,membership_id,role_id) "
                "SELECT :tenant,:member,id FROM roles WHERE tenant_id=:tenant AND code='OPERATOR'"
            ),
            {"tenant": data["tenant"], "member": data["approver_member"]},
        )
    other = AnalysisService(
        data["engine"], VerifiedIdentity(data["tenant"], data["approver_member"], data["approver"])
    )
    independent = other.create(data["input"])
    assert independent.id != original.id and other.tasks() == (independent,)
    assert data["analysis"].tasks() == (original,)


def test_platform_link_requires_both_scopes_and_current_account_mask(
    analysis_ready: dict[str, Any],
) -> None:
    data = analysis_ready
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:member"), {"member": data["member"]}
        )
        owner.execute(
            text(
                "INSERT INTO data_scopes(tenant_id,membership_id,scope_type,shop_id) "
                "VALUES(:tenant,:member,'SHOP',:shop)"
            ),
            {"tenant": data["tenant"], "member": data["member"], "shop": data["shop"]},
        )
    with pytest.raises(ApiError) as denied:
        data["analysis"].create(data["input"])
    assert denied.value.code == ErrorCode.FORBIDDEN
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "INSERT INTO data_scopes(tenant_id,membership_id,scope_type,account_id) "
                "VALUES(:tenant,:member,'ACCOUNT',:account)"
            ),
            {
                "tenant": data["tenant"],
                "member": data["member"],
                "account": data["binding"].account_id,
            },
        )
    task = data["analysis"].create(data["input"])
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text(
                "UPDATE data_scopes SET permissions_mask=ARRAY['task.create'] "
                "WHERE membership_id=:member AND scope_type='ACCOUNT'"
            ),
            {"member": data["member"]},
        )
    with pytest.raises(ApiError):
        data["analysis"].get(task.id)


@pytest.mark.asyncio
async def test_coverage_real_zero_missing_day_and_original_key(
    analysis_ready: dict[str, Any],
) -> None:
    d = analysis_ready
    original = d["analysis"].create(d["input"])
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE metric_facts SET refund_policy='UNKNOWN' WHERE fact_date='2026-10-07'")
        )
    assert d["analysis"].create(d["input"]).id == original.id
    missing = d["input"].model_copy(update=dict(idempotency_key=str(uuid4())))
    with pytest.raises(ApiError) as err:
        d["analysis"].create(missing)
    assert err.value.code == ErrorCode.DATA_COVERAGE_INSUFFICIENT
    assert any("2026-10-07" in row["message"] for row in err.value.details)
    assert len(d["analysis"].tasks()) == 1


def test_coverage_invalid_period_and_missing_dimension(analysis_ready: dict[str, Any]) -> None:
    d = analysis_ready
    for changes, code in [
        (dict(end_date="2026-10-02"), ErrorCode.INVALID_INPUT),
        (dict(dimensions=("live",)), ErrorCode.DATA_COVERAGE_INSUFFICIENT),
    ]:
        value = AnalysisCreate.model_validate(
            d["input"].model_dump()
            | dict(query=d["input"].query.model_dump() | changes, idempotency_key=str(uuid4()))
        )
        with pytest.raises(ApiError) as err:
            d["analysis"].create(value)
        assert err.value.code == code
    assert not d["analysis"].tasks()


def test_worker_rechecks_missing_facts_before_recording_delivery(
    analysis_ready: dict[str, Any],
    monkeypatch: Any,
) -> None:
    from yuliang.worker import tasks

    d = analysis_ready
    task = d["analysis"].create(d["input"])
    _, payload = notice(d, task.id)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE metric_facts SET refund_policy='UNKNOWN' WHERE metric_name='spend_minor'")
        )
    monkeypatch.setattr(tasks, "engine", lambda: d["engine"])
    result = tasks.accept_analysis.run(payload)
    assert result["deliveryRecorded"] is True
    failed = d["analysis"].get(task.id)
    assert failed.status == "failed" and "DATA_COVERAGE_INSUFFICIENT" in str(failed.error)
    with business_transaction(d["engine"], d["identity"]) as s:
        assert s.execute(text("SELECT count(*) FROM analysis_deliveries")).scalar_one() == 1


def test_advertising_facts_cannot_cover_shop_dimension(analysis_ready: dict[str, Any]) -> None:
    d = analysis_ready
    value = AnalysisCreate.model_validate(
        d["input"].model_dump()
        | dict(query=d["input"].query.model_dump() | dict(dimensions=("shop",)))
    )
    with pytest.raises(ApiError) as error:
        d["analysis"].create(value)
    assert error.value.code == ErrorCode.DATA_COVERAGE_INSUFFICIENT
    assert not d["analysis"].tasks()


@pytest.mark.parametrize("invalidate", ["expired_contract", "partial_run", "mixed_unit"])
def test_coverage_rejects_incomplete_or_unconfirmed_source(
    analysis_ready: dict[str, Any],
    invalidate: str,
) -> None:
    d = analysis_ready
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        if invalidate == "expired_contract":
            owner.execute(text("UPDATE adq_report_contracts SET active=false"))
        elif invalidate == "partial_run":
            owner.execute(text("UPDATE sync_runs SET state='PARTIAL'"))
        else:
            owner.execute(
                text("""UPDATE metric_facts SET refund_policy='NET_OF_REFUND'
                WHERE fact_date='2026-10-07'""")
            )
    with pytest.raises(ApiError) as error:
        d["analysis"].create(d["input"])
    assert error.value.code == ErrorCode.DATA_COVERAGE_INSUFFICIENT
    assert not d["analysis"].tasks()
