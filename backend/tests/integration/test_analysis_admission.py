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
from .test_infrastructure import POSIX_PREFORK_WORKER
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


@POSIX_PREFORK_WORKER
def test_real_rabbitmq_prefork_delivery_computes_once_and_freezes_result(
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
    completed = data["analysis"].get(task.id)
    assert completed.status == "succeeded" and completed.progress == 100
    assert completed.result_id is not None and counts(data) == (1, 1, 1, 1)
    assert first_result["computed"] is True and duplicate_result["computed"] is False
    forged = client.send_task(
        "yuliang.system.analysis_accept",
        kwargs={"payload": payload | {"job_id": str(uuid4())}},
        kwargsrepr="<ids-only>",
    )
    data["worker_results"].append(forged)
    with pytest.raises(ValueError):
        forged.get(timeout=15)
    assert counts(data) == (1, 1, 1, 1)
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


def test_actual_calculation_snapshot_result_and_no_direct_writer(
    analysis_ready: dict[str, Any],
) -> None:
    from yuliang.modules.analysis.repository import execute_analysis

    d = analysis_ready
    task = d["analysis"].create(d["input"])
    assert execute_analysis(d["engine"], d["identity"], task.id)
    assert not execute_analysis(d["engine"], d["identity"], task.id)
    done = d["analysis"].get(task.id)
    assert done.status == "succeeded" and done.progress == 100
    result = d["analysis"].results(done.result_id)[0]
    assert result["taskId"] == str(task.id) and result["source"] == "API"
    assert result["completeness"] == "partial" and result["comparison"]["previous"] is None
    metrics = {m["id"]: m for m in result["metrics"]}
    assert metrics["ads.spend"]["value"] == 0 and metrics["ads.roi"]["value"] is None
    assert metrics["ads.cpa"]["value"] is None
    assert all(m["dimension"] == "ads" for m in result["metrics"])
    with business_transaction(d["engine"], d["identity"]) as s:
        with pytest.raises(DBAPIError):
            s.execute(text("UPDATE analysis_tasks SET progress=45"))
    with business_transaction(d["engine"], d["identity"]) as s:
        with pytest.raises(DBAPIError):
            s.execute(text("UPDATE analysis_results SET payload='{}'::jsonb"))


def test_execution_lease_fences_and_recovers_original_task(analysis_ready: dict[str, Any]) -> None:
    from yuliang.modules.analysis.repository import execute_analysis

    d = analysis_ready
    task = d["analysis"].create(d["input"])
    with business_transaction(d["engine"], d["identity"]) as s:
        old = s.execute(
            text("SELECT access.analysis_execute(:id,NULL,10)"), dict(id=task.id)
        ).scalar_one()
    from yuliang.modules.analysis.repository import AnalysisLeaseBusy

    with pytest.raises(AnalysisLeaseBusy):
        execute_analysis(d["engine"], d["identity"], task.id)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE analysis_executions SET lease_until=clock_timestamp()-interval '1 second'")
        )
    assert execute_analysis(d["engine"], d["identity"], task.id)
    assert len(d["analysis"].results()) == 1
    assert d["analysis"].get(task.id).status == "succeeded"
    assert old is not None


@pytest.mark.asyncio
async def test_production_report_roi_recomputed_and_raw_ratio_ignored(
    analysis_ready: dict[str, Any],
) -> None:
    from yuliang.modules.analysis.repository import execute_analysis

    d = analysis_ready
    d["state"].update(cost=100, gmv=300)
    await d["reader"].sync(
        d["contexts"]["DAILY"], uuid4(), query(start_date="2026-10-01", end_date="2026-10-07")
    )
    task = d["analysis"].create(d["input"])
    assert execute_analysis(d["engine"], d["identity"], task.id)
    done = d["analysis"].get(task.id)
    metrics = {m["id"]: m for m in d["analysis"].results(done.result_id)[0]["metrics"]}
    assert metrics["ads.spend"]["value"] == 700
    assert metrics["ads.gmv"]["value"] == 2100 and metrics["ads.roi"]["value"] == 3
    with business_transaction(d["engine"], d["identity"]) as s:
        snapshot = s.execute(
            text("SELECT snapshot FROM analysis_executions WHERE task_id=:id"), dict(id=task.id)
        ).scalar_one()
        assert all(row["metric_name"] != "roi" for row in snapshot)


def test_stage_progress_crash_snapshot_recovery_and_old_fence(
    analysis_ready: dict[str, Any],
    monkeypatch: Any,
) -> None:
    from yuliang.modules.analysis import repository

    d = analysis_ready
    task = d["analysis"].create(d["input"])
    calculate_original = repository.calculate

    def crash(*args: Any, **kwargs: Any) -> Any:
        assert d["analysis"].get(task.id).progress == 40
        raise SystemExit("synthetic worker crash")

    monkeypatch.setattr(repository, "calculate", crash)
    with pytest.raises(SystemExit):
        repository.execute_analysis(d["engine"], d["identity"], task.id)
    with business_transaction(d["engine"], d["identity"]) as s:
        old = s.execute(
            text("SELECT token FROM analysis_executions WHERE task_id=:id"), dict(id=task.id)
        ).scalar_one()
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(text("UPDATE adq_report_contracts SET active=false"))
        owner.execute(text("UPDATE metric_facts SET value_num=999 WHERE unit='MINOR'"))
        owner.execute(
            text("UPDATE analysis_executions SET lease_until=clock_timestamp()-interval '1 second'")
        )
    with business_transaction(d["engine"], d["identity"]) as s:
        fresh = s.execute(
            text("SELECT access.analysis_execute(:id,NULL,10)"), dict(id=task.id)
        ).scalar_one()
    assert fresh != old
    with business_transaction(d["engine"], d["identity"]) as s:
        with pytest.raises(DBAPIError):
            s.execute(
                text("SELECT access.analysis_execute(:id,:old,40)"), dict(id=task.id, old=old)
            )
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("UPDATE analysis_executions SET lease_until=clock_timestamp()-interval '1 second'")
        )
    monkeypatch.setattr(repository, "calculate", calculate_original)
    assert repository.execute_analysis(d["engine"], d["identity"], task.id)
    assert d["analysis"].get(task.id).status == "succeeded"
    assert len(d["analysis"].results()) == 1


def test_calculation_exception_persists_safe_failed_reason(
    analysis_ready: dict[str, Any],
    monkeypatch: Any,
) -> None:
    from yuliang.modules.analysis import repository

    d = analysis_ready
    task = d["analysis"].create(d["input"])

    def invalid(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("PRIVATE_PROVIDER_PAYLOAD")

    monkeypatch.setattr(repository, "calculate", invalid)
    assert repository.execute_analysis(d["engine"], d["identity"], task.id) is False
    failed = d["analysis"].get(task.id)
    assert failed.status == "failed" and failed.progress == 40
    assert "PRIVATE" not in str(failed.error)
    assert not d["analysis"].results()


def test_concurrent_claim_has_one_actual_owner(analysis_ready: dict[str, Any]) -> None:
    d = analysis_ready
    task = d["analysis"].create(d["input"])

    def claim(_: int) -> Any:
        with business_transaction(d["engine"], d["identity"]) as s:
            return s.execute(
                text("SELECT access.analysis_execute(:id,NULL,10)"), dict(id=task.id)
            ).scalar_one_or_none()

    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(claim, range(2)))
    assert sum(value is not None for value in tokens) == 1
    assert d["analysis"].get(task.id).progress == 10


def test_frozen_rules_evidence_immutable_and_current_scope_filtered(
    analysis_ready: dict[str, Any],
) -> None:
    from yuliang.modules.analysis.repository import execute_analysis

    d = analysis_ready
    task = d["analysis"].create(d["input"])
    assert execute_analysis(d["engine"], d["identity"], task.id)
    done = d["analysis"].get(task.id)
    first = d["analysis"].results(done.result_id)[0]
    assert first["insightSource"] == "rules" and first["insights"]
    assert all(i["metricId"] in {m["id"] for m in first["metrics"]} for i in first["insights"])
    assert all("fact:" in i["evidence"] and "run:" in i["evidence"] for i in first["insights"])
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(text("UPDATE adq_report_contracts SET active=false"))
        owner.execute(text("UPDATE metric_facts SET value_num=999 WHERE unit='MINOR'"))
    assert d["analysis"].results(done.result_id)[0] == first
    assert not execute_analysis(d["engine"], d["identity"], task.id)
    with d["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        owner.execute(
            text("DELETE FROM data_scopes WHERE membership_id=:id"),
            dict(id=d["identity"].membership_id),
        )
    with pytest.raises(ApiError) as error:
        d["analysis"].results(done.result_id)
    assert error.value.code == ErrorCode.NO_DATA


def test_unclaimed_or_null_execution_fence_cannot_change_stage(
    analysis_ready: dict[str, Any],
) -> None:
    d = analysis_ready
    task = d["analysis"].create(d["input"])
    for token in (None, uuid4()):
        for stage in (-1, 40, 75, 90, 100):
            with business_transaction(d["engine"], d["identity"]) as session:
                assert session.execute(text("SELECT current_user")).scalar_one() == "app_login"
                with pytest.raises(DBAPIError) as error:
                    session.execute(
                        text("SELECT access.analysis_execute(:id,:token,:stage)"),
                        dict(id=task.id, token=token, stage=stage),
                    )
                assert getattr(error.value.orig, "sqlstate", None) == "P0409"
    assert d["analysis"].get(task.id).status == "queued"
    with business_transaction(d["engine"], d["identity"]) as session:
        claimed = session.execute(
            text("SELECT access.analysis_execute(:id,NULL,10)"), dict(id=task.id)
        ).scalar_one()
    assert claimed is not None
    with business_transaction(d["engine"], d["identity"]) as session:
        with pytest.raises(DBAPIError) as error:
            session.execute(text("SELECT access.analysis_execute(:id,NULL,-1)"), dict(id=task.id))
        assert getattr(error.value.orig, "sqlstate", None) == "P0409"
    assert d["analysis"].get(task.id).status == "running"


@pytest.mark.asyncio
async def test_weighted_frontend_result_uses_actual_report_bases(
    analysis_ready: dict[str, Any],
) -> None:
    from yuliang.modules.analysis.repository import execute_analysis

    d = analysis_ready

    def unequal(body: Any, request: Any) -> None:
        day = json.loads(request.url.params["date_range"])["start_date"]
        cost, gmv = {"2026-10-05": (100, 1000), "2026-10-06": (900, 900)}.get(day, (0, 0))
        for row in body["data"]["list"]:
            row.update(cost=cost, order_amount=gmv)

    d["state"]["mutate"] = unequal
    await d["reader"].sync(
        d["contexts"]["DAILY"], uuid4(), query(start_date="2026-10-05", end_date="2026-10-11")
    )
    value = AnalysisCreate.model_validate(
        d["input"].model_dump()
        | dict(
            query=d["input"].query.model_dump()
            | dict(start_date="2026-10-05", end_date="2026-10-11")
        )
    )
    task = d["analysis"].create(value)
    assert execute_analysis(d["engine"], d["identity"], task.id)
    done = d["analysis"].get(task.id)
    result = d["analysis"].results(done.result_id)[0]
    metrics = {m["id"]: m for m in result["metrics"]}
    assert metrics["ads.roi"]["value"] == 1.9
    assert (metrics["ads.roi"]["numerator"], metrics["ads.roi"]["denominator"]) == (
        "ads.gmv",
        "ads.spend",
    )
    assert all(
        p["values"]["ads.impressions"] == 20 and p["values"]["ads.clicks"] == 5
        for p in result["points"]
    )
    assert all(p["values"]["ads.orders"] is None for p in result["points"])
    if destination := os.getenv("YULIANG_ANALYSIS_RESULT_CAPTURE"):
        Path(destination).write_text(
            json.dumps(dict(result=result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
