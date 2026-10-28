"""Real analysis/workflow mutations, source/projection/RLS and loopback SSE."""

import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from yuliang.main import create_app
from yuliang.modules.analysis.repository import execute_analysis
from yuliang.modules.content.workflows_execution_models import Skip
from yuliang.modules.content.workflows_execution_service import WorkflowExecution
from yuliang.modules.content.workflows_models import TemplateDefinition
from yuliang.modules.content.workflows_service import WorkflowService
from yuliang.modules.identity.routes import SESSION_COOKIE
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.events import CoreEventStream

from .test_analysis_admission import analysis_ready as analysis_ready
from .test_analysis_admission import capability as capability
from .test_analysis_admission import configuration_ready as configuration_ready
from .test_analysis_admission import database as database
from .test_analysis_admission import governance as governance
from .test_analysis_admission import legacy as legacy
from .test_analysis_admission import login_api as login_api
from .test_analysis_admission import oauth_ready as oauth_ready
from .test_analysis_admission import reports as reports
from .test_cloud_documents import content_ready as content_ready
from .test_content_workflows import request as workflow_request
from .test_core_sse import frames
from .test_shop_product_details import details as details
from .test_shop_products import products as products

pytestmark = pytest.mark.integration


def notices(d: dict[str, Any], entity: str, identifier: UUID) -> list[Any]:
    stream = CoreEventStream(d["engine"], d["identity"])
    stream.project(500)
    return [
        e
        for e in stream.read(limit=500).events
        if e.data.entity_type == entity and e.data.entity_id == identifier
    ]


def sources(d: dict[str, Any], entity: str, identifier: UUID) -> list[Any]:
    with d["maintenance"].begin() as s:
        s.execute(text("SET LOCAL ROLE app_owner"))
        return list(
            s.execute(
                text(
                    "SELECT * FROM event_source_outbox WHERE entity_type=:type "
                    "AND entity_id=:id ORDER BY version"
                ),
                dict(type=entity, id=identifier),
            ).mappings()
        )


def test_actual_analysis_stages_result_replay_and_real_fk(analysis_ready: dict[str, Any]) -> None:
    d = analysis_ready
    task = d["analysis"].create(d["input"])
    assert d["analysis"].create(d["input"]).id == task.id
    assert execute_analysis(d["engine"], d["identity"], task.id)
    assert not execute_analysis(d["engine"], d["identity"], task.id)
    events = notices(d, "analysis_task", task.id)
    assert [e.data.version for e in events] == list(range(1, 7))
    assert [e.data.changes.status for e in events] == [
        "QUEUED",
        "RUNNING",
        "RUNNING",
        "RUNNING",
        "RUNNING",
        "SUCCEEDED",
    ]
    assert all(e.event == "analysis.changed" for e in events)
    rows = sources(d, "analysis_task", task.id)
    assert len(rows) == 6 and all(
        r["analysis_task_id"] == task.id and r["job_id"] is None for r in rows
    )
    done = d["analysis"].get(task.id)
    assert d["analysis"].results(done.result_id)[0]["taskId"] == str(task.id)
    assert all(set(e.data.changes.model_dump()) == {"status"} for e in events)


def test_analysis_coverage_failure_and_transaction_rollback(analysis_ready: dict[str, Any]) -> None:
    d = analysis_ready
    task = d["analysis"].create(d["input"])
    with pytest.raises(RuntimeError), business_transaction(d["engine"], d["identity"]) as s:
        s.execute(text("SELECT access.analysis_execute(:id,NULL,10)"), dict(id=task.id))
        raise RuntimeError("rollback real task and source")
    assert len(sources(d, "analysis_task", task.id)) == 1
    with business_transaction(d["engine"], d["identity"]) as s:
        s.execute(text("SELECT access.analysis_coverage_failed(:id)"), dict(id=task.id))
        s.execute(text("SELECT access.analysis_coverage_failed(:id)"), dict(id=task.id))
    events = notices(d, "analysis_task", task.id)
    assert [e.data.changes.status for e in events] == [
        "QUEUED",
        "FAILED",
    ]
    assert [e.data.version for e in events] == [1, 2]


def test_analysis_owner_account_scope_and_source_forgery_denied(
    analysis_ready: dict[str, Any],
) -> None:
    d = analysis_ready
    task = d["analysis"].create(d["input"])
    notices(d, "analysis_task", task.id)
    other = CoreEventStream(
        d["engine"], VerifiedIdentity(d["tenant"], d["approver_member"], d["approver"])
    )
    assert all(e.data.entity_id != task.id for e in other.read().events)
    alien = CoreEventStream(
        d["engine"], VerifiedIdentity(d["tenant"] + 999, d["member"], d["user"])
    )
    assert not alien.read().events
    for sql in (
        "INSERT INTO event_resource_heads VALUES(:tenant,'analysis_task',:id,99)",
        "INSERT INTO event_source_outbox(tenant_id,scope_user_id,entity_type,entity_id,version,"
        "event_type,changes,occurred_at,analysis_task_id) VALUES(:tenant,:actor,'analysis_task',"
        ":id,99,'analysis.changed','{\"status\":\"SUCCEEDED\"}',clock_timestamp(),:id)",
    ):
        with (
            pytest.raises(DBAPIError) as denied,
            business_transaction(d["engine"], d["identity"]) as s,
        ):
            s.execute(text(sql), dict(tenant=d["tenant"], id=task.id, actor=d["user"]))
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    with d["maintenance"].begin() as s:
        s.execute(text("SET LOCAL ROLE app_owner"))
        s.execute(text("DELETE FROM data_scopes WHERE membership_id=:id"), dict(id=d["member"]))
    assert not notices(d, "analysis_task", task.id)


@pytest.mark.asyncio
async def test_workflow_real_revision_replay_and_removed_product_visibility(
    content_ready: dict[str, Any],
) -> None:
    d = content_ready
    service = WorkflowService(d["content"])
    value = workflow_request(d)
    template = service.append_template(
        UUID(value.templateId),
        TemplateDefinition.model_validate(
            dict(
                schemaVersion=2,
                code="notice_optional",
                name="合成可选人工步骤",
                steps=[dict(code="brief", name="确认", kind="MANUAL", optional=True)],
            )
        ),
    )
    value = value.model_copy(update={"templateId": template["id"]})
    run = service.create(value)
    identifier = UUID(run["id"])
    assert service.create(value)["id"] == run["id"]
    execution = WorkflowExecution(d["content"])
    action = Skip(
        actionKey="skip-brief", expectedRunVersion=1, expectedAttempt=1, reason="human skip"
    )
    execution.skip(identifier, "brief", action)
    execution.skip(identifier, "brief", action)
    events = notices(d, "workflow_run", identifier)
    assert [e.data.version for e in events] == [1, 2]
    assert all(e.event == "workflow.changed" for e in events)
    rows = sources(d, "workflow_run", identifier)
    assert all(r["workflow_run_id"] == identifier and r["job_id"] is None for r in rows)
    assert execution.get(identifier)["revision"] == 2
    with d["maintenance"].begin() as s:
        s.execute(text("SET LOCAL ROLE app_owner"))
        s.execute(
            text("UPDATE commerce_products SET deleted_at=clock_timestamp() WHERE id=:id"),
            dict(id=UUID(d["product"])),
        )
    assert not notices(d, "workflow_run", identifier)


@contextmanager
def server(d: dict[str, Any]) -> Iterator[str]:
    instance = uvicorn.Server(
        uvicorn.Config(
            create_app(d["settings"]),
            host="127.0.0.1",
            port=0,
            log_level="critical",
            access_log=False,
        )
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        thread = threading.Thread(target=instance.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while not instance.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert instance.started
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            instance.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()


async def loopback(d: dict[str, Any], kind: str) -> None:
    stream = CoreEventStream(d["engine"], d["identity"])
    stream.project(500)
    cursor = stream.read().cursor

    def create(key: str) -> str:
        if kind == "analysis":
            return str(
                d["analysis"].create(d["input"].model_copy(update={"idempotency_key": key})).id
            )
        return str(WorkflowService(d["content"]).create(workflow_request(d, key))["id"])

    first = create(uuid4().hex)
    with server(d) as url:
        async with httpx.AsyncClient(
            base_url=url,
            headers={"Origin": "http://testserver"},
            cookies={SESSION_COOKIE: str(d["client"].cookies[SESSION_COOKIE])},
            timeout=10,
        ) as browser:
            async with browser.stream(
                "GET", "/v1/events", headers={"Last-Event-ID": cursor}
            ) as response:
                assert response.status_code == 200
                incoming = frames(response)
                assert await anext(incoming) == [": connected"]
                frame = await anext(incoming)
                assert frame[1] == f"event: {kind}.changed"
                assert json.loads(frame[2].removeprefix("data: "))["entityId"] == first
                cursor = frame[0].removeprefix("id: ")
            second = create(uuid4().hex)
            async with browser.stream(
                "GET", "/v1/events", headers={"Last-Event-ID": cursor}
            ) as response:
                incoming = frames(response)
                assert await anext(incoming) == [": connected"]
                frame = await anext(incoming)
                body = json.loads(frame[2].removeprefix("data: "))
                assert body["entityId"] == second and body["version"] == 1


@pytest.mark.asyncio
async def test_analysis_real_loopback_sse_and_resume(analysis_ready: dict[str, Any]) -> None:
    await loopback(analysis_ready, "analysis")


@pytest.mark.asyncio
async def test_workflow_real_loopback_sse_and_resume(content_ready: dict[str, Any]) -> None:
    await loopback(content_ready, "workflow")


@pytest.mark.asyncio
async def test_workflow_product_removed_live_stream_resyncs(
    content_ready: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yuliang.api import events as events_api

    d = content_ready
    monkeypatch.setattr(events_api, "POLL_SECONDS", 0.03)
    stream = CoreEventStream(d["engine"], d["identity"])
    stream.project(500)
    cursor = stream.read().cursor
    WorkflowService(d["content"]).create(workflow_request(d))
    with server(d) as url:
        async with httpx.AsyncClient(
            base_url=url,
            headers={"Origin": "http://testserver"},
            cookies={SESSION_COOKIE: str(d["client"].cookies[SESSION_COOKIE])},
            timeout=10,
        ) as browser:
            async with browser.stream(
                "GET", "/v1/events", headers={"Last-Event-ID": cursor}
            ) as response:
                incoming = frames(response)
                assert await anext(incoming) == [": connected"]
                assert (await anext(incoming))[1] == "event: workflow.changed"
                with d["maintenance"].begin() as owner:
                    owner.execute(text("SET LOCAL ROLE app_owner"))
                    owner.execute(
                        text(
                            "UPDATE commerce_products SET deleted_at=clock_timestamp() WHERE id=:id"
                        ),
                        dict(id=UUID(d["product"])),
                    )
                frame = await anext(incoming)
                assert frame[1] == "event: resync"
                assert (
                    json.loads(frame[2].removeprefix("data: "))["reason"] == "AUTHORIZATION_CHANGED"
                )
                with pytest.raises(StopAsyncIteration):
                    await anext(incoming)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["shop_status", "account_line"])
async def test_already_polled_analysis_page_cannot_send_after_scope_changes(
    analysis_ready: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    from yuliang.api import events as events_api

    d = analysis_ready
    stream = CoreEventStream(d["engine"], d["identity"])
    stream.project(500)
    cursor = stream.read().cursor
    bare = uuid4()
    if change == "account_line":
        # A real, dependency-free account permits a legal provider/line update without
        # disabling the FKs that intentionally protect existing report/binding rows.
        with d["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(
                text(
                    "INSERT INTO ad_accounts(id,tenant_id,entity_id,provider,product_line,"
                    "native_id,name,status) VALUES(:id,:tenant,:entity,'tencent','adq',"
                    "'990001','Scope test','AUTHORIZED')"
                ),
                dict(id=bare, tenant=d["tenant"], entity=d["context"].business_entity_id),
            )
            owner.execute(
                text(
                    "INSERT INTO entity_links(tenant_id,shop_id,account_id) "
                    "VALUES(:tenant,:shop,:account)"
                ),
                dict(tenant=d["tenant"], shop=d["shop"], account=bare),
            )
        # Use the actual controlled DB admission producer, not a forged event/task row.
        # This scope-only task has no coverage for the bare account and is not executed.
        with business_transaction(d["engine"], d["identity"]) as session:
            task = session.execute(
                text("SELECT access.analysis_admit(CAST(:query AS jsonb),:key)"),
                dict(query=d["input"].query.model_dump_json(by_alias=True), key=uuid4().hex),
            ).scalar_one()
    else:
        task = d["analysis"].create(d["input"]).id
    polled, changed = threading.Event(), threading.Event()
    errors: list[BaseException] = []
    original = events_api.EventSubscription.poll

    def buffered(subscription: Any, after: str | None) -> Any:
        page = original(subscription, after)
        assert any(e.data.entity_id == task for e in page.events)
        polled.set()
        assert changed.wait(10), "scope mutation did not finish after actual poll"
        assert not errors
        return page

    def revoke() -> None:
        try:
            assert polled.wait(10), "real initial SSE poll did not buffer the analysis event"
            with d["maintenance"].begin() as owner:
                owner.execute(text("SET LOCAL ROLE app_owner"))
                if change == "shop_status":
                    owner.execute(
                        text("UPDATE shops SET status='UNKNOWN' WHERE id=:id"), dict(id=d["shop"])
                    )
                else:
                    owner.execute(
                        text(
                            "UPDATE ad_accounts SET provider='ocean',product_line='qianchuan' "
                            "WHERE id=:id"
                        ),
                        dict(id=bare),
                    )
        except BaseException as exc:
            errors.append(exc)
        finally:
            changed.set()

    monkeypatch.setattr(events_api.EventSubscription, "poll", buffered)
    worker = threading.Thread(target=revoke, daemon=True)
    worker.start()
    with server(d) as url:
        async with httpx.AsyncClient(
            base_url=url,
            headers={"Origin": "http://testserver"},
            cookies={SESSION_COOKIE: str(d["client"].cookies[SESSION_COOKIE])},
            timeout=15,
        ) as browser:
            async with browser.stream(
                "GET", "/v1/events", headers={"Last-Event-ID": cursor}
            ) as response:
                assert response.status_code == 200
                incoming = frames(response)
                assert await anext(incoming) == [": connected"]
                frame = await anext(incoming)
                assert frame[1] == "event: resync", (
                    "already buffered analysis event must not escape"
                )
                assert (
                    json.loads(frame[2].removeprefix("data: "))["reason"] == "AUTHORIZATION_CHANGED"
                )
                with pytest.raises(StopAsyncIteration):
                    await anext(incoming)
    worker.join(timeout=5)
    assert not worker.is_alive() and not errors
    assert not notices(d, "analysis_task", task)
