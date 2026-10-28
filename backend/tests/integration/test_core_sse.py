"""真实本地HTTP流/PG登录与撤权，不使用TestClient缓存完整无限响应。"""

import json
import os
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import uvicorn
from sqlalchemy import text

from yuliang.api import events as events_api
from yuliang.main import create_app
from yuliang.modules.identity.routes import SESSION_COOKIE
from yuliang.platform.events import CoreEventStream

from .test_capability_evidence import capability as capability
from .test_identity_database import database as database
from .test_job_applications import applications as applications
from .test_oauth_vault import oauth_ready as oauth_ready
from .test_password_sessions import challenge
from .test_password_sessions import login_api as login_api
from .test_platform_governance import governance as governance
from .test_resource_freshness import health_ready as health_ready
from .test_sync_checkpoints import sync_ready as sync_ready

pytestmark = pytest.mark.integration
ORIGIN = {"Origin": "http://testserver"}


def trace(name: str, values: dict[str, Any]) -> None:
    if directory := os.getenv("YULIANG_SSE_EVIDENCE"):
        Path(directory, name).write_text(
            json.dumps(
                values | {"platformCalls": 0, "paidCalls": 0, "humanSignoff": "PENDING"},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )


@pytest.fixture
def http_stream(
    applications: dict[str, Any], login_api: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Any]]:
    data = applications
    # 仅加快测试撤销轮询；心跳仍使用实际25秒。
    monkeypatch.setattr(events_api, "POLL_SECONDS", 0.03)
    baseline = CoreEventStream(data["engine"], data["identity"])
    baseline.project(limit=500)
    initial_cursor = baseline.read().cursor
    instance = create_app(data["settings"])
    server = uvicorn.Server(
        uvicorn.Config(instance, host="127.0.0.1", port=0, log_level="critical", access_log=False)
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        try:
            yield data | {
                "url": f"http://127.0.0.1:{port}",
                "token": str(login_api["client"].cookies[SESSION_COOKIE]),
                "login": login_api,
                "stream_app": instance,
                "initial_cursor": initial_cursor,
            }
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()


def client(data: dict[str, Any]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=data["url"],
        headers=ORIGIN | {"Last-Event-ID": data["initial_cursor"]},
        cookies={SESSION_COOKIE: data["token"]},
        timeout=35,
    )


async def frames(response: httpx.Response) -> AsyncIterator[list[str]]:
    lines: list[str] = []
    async for line in response.aiter_lines():
        if line:
            lines.append(line)
        elif lines:
            yield lines
            lines = []


def new_job(data: dict[str, Any]) -> Any:
    return data["applications"].create(
        data["input"].model_copy(update={"idempotency_key": uuid4().hex})
    )


async def test_real_sse_frames_resume_and_version_reread_without_write_replay(
    http_stream: dict[str, Any],
) -> None:
    data = http_stream
    first = new_job(data)
    async with client(data) as browser:
        async with browser.stream("GET", "/v1/events") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-accel-buffering"] == "no"
            incoming = frames(response)
            assert await anext(incoming) == [": connected"]
            frame = await anext(incoming)
            assert frame[1] == "event: job.changed"
            body = json.loads(frame[2].removeprefix("data: "))
            assert body["entityId"] == first.job_id and body["version"] == first.version
            cursor = frame[0].removeprefix("id: ")
            first_frame, first_request = frame, response.headers["x-request-id"]
        second = new_job(data)
        async with browser.stream(
            "GET", "/v1/events", headers={"Last-Event-ID": cursor}
        ) as response:
            incoming = frames(response)
            assert await anext(incoming) == [": connected"]
            frame = await anext(incoming)
            body = json.loads(frame[2].removeprefix("data: "))
            assert body["entityId"] == second.job_id and body["version"] == second.version
            assert data["applications"].get(UUID(second.job_id)) == second
            trace(
                "frames-resume.json",
                {
                    "input": "REAL_LOCAL_HTTP_PG_COOKIE_AND_APPLICATIONS",
                    "requestIds": [first_request, response.headers["x-request-id"]],
                    "firstFrame": first_frame,
                    "resumedFrame": frame,
                    "reread": "ACTUAL_APPLICATION_QUERY_NOT_LIFECYCLE_HTTP",
                    "jobIds": [first.job_id, second.job_id],
                    "jobVersions": [first.version, second.version],
                    "commandJobsAfterReconnect": 2,
                    "executionOrdersAfterReconnect": 0,
                },
            )
    with data["maintenance"].begin() as owner:
        owner.execute(text("SET LOCAL ROLE app_owner"))
        assert owner.execute(text("SELECT count(*) FROM command_jobs")).scalar_one() == 2
        assert owner.execute(text("SELECT count(*) FROM execution_orders")).scalar_one() == 0


async def test_logout_closes_active_stream(http_stream: dict[str, Any]) -> None:
    data = http_stream
    async with client(data) as browser, browser.stream("GET", "/v1/events") as response:
        incoming = frames(response)
        assert await anext(incoming) == [": connected"]
        csrf = challenge(data["login"]["client"])
        assert (
            data["login"]["client"]
            .post("/v1/auth/logout", headers={"X-CSRF-Token": csrf})
            .status_code
            == 200
        )
        with pytest.raises(StopAsyncIteration):
            await anext(incoming)


async def test_actual_scope_change_without_version_bump_resyncs_and_closes(
    http_stream: dict[str, Any],
) -> None:
    data = http_stream
    async with client(data) as browser, browser.stream("GET", "/v1/events") as response:
        incoming = frames(response)
        assert await anext(incoming) == [": connected"]
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(
                text(
                    "UPDATE data_scopes SET permissions_mask=ARRAY['overview.read'] "
                    "WHERE membership_id=:id"
                ),
                {"id": data["identity"].membership_id},
            )
        frame = await anext(incoming)
        assert frame[1] == "event: resync"
        assert json.loads(frame[2].removeprefix("data: ")) == {"reason": "AUTHORIZATION_CHANGED"}
        with pytest.raises(StopAsyncIteration):
            await anext(incoming)


@pytest.mark.parametrize("mode", ["cross_tenant", "ahead", "old"])
async def test_resume_resync_reasons_use_current_tenant(
    http_stream: dict[str, Any], mode: str
) -> None:
    data = http_stream
    new_job(data)
    current = CoreEventStream(data["engine"], data["identity"])
    current.project()
    cursor = f"{data['tenant']}:0"
    if mode == "cross_tenant":
        cursor = f"{data['tenant'] + 1}:0"
    elif mode == "ahead":
        cursor = f"{data['tenant']}:{current.read().head_seq + 1}"
    else:
        with data["maintenance"].begin() as owner:
            owner.execute(text("SET LOCAL ROLE app_owner"))
            owner.execute(text("UPDATE event_projections SET created_at=now()-interval '8 days'"))
    async with client(data) as browser:
        async with browser.stream(
            "GET", "/v1/events", headers={"Last-Event-ID": cursor}
        ) as response:
            incoming = frames(response)
            assert await anext(incoming) == [": connected"]
            frame = await anext(incoming)
            assert frame[0].startswith(f"id: {data['tenant']}:")
            assert frame[1] == "event: resync"
            assert (
                json.loads(frame[2].removeprefix("data: "))["reason"]
                == {
                    "cross_tenant": "CURSOR_TENANT_CHANGED",
                    "ahead": "CURSOR_AHEAD",
                    "old": "CURSOR_OLD",
                }[mode]
            )


async def test_stream_auth_origin_and_cursor_fail_before_headers(
    http_stream: dict[str, Any],
) -> None:
    data = http_stream
    async with client(data) as browser:
        assert (
            await browser.get("/v1/events", headers={"Origin": "https://evil.invalid"})
        ).status_code == 403
        assert (
            await browser.get("/v1/events", headers={"Last-Event-ID": "x:1"})
        ).status_code == 422
        browser.cookies.clear()
        assert (await browser.get("/v1/events")).status_code == 401


async def test_real_twenty_five_second_heartbeat_holds_no_idle_pg_transaction(
    http_stream: dict[str, Any],
) -> None:
    data = http_stream
    async with client(data) as browser, browser.stream("GET", "/v1/events") as response:
        incoming = frames(response)
        assert await anext(incoming) == [": connected"]
        started = time.monotonic()
        assert await anext(incoming) == [": ping"]
        elapsed = time.monotonic() - started
        assert 24.5 <= elapsed < 33
        # 独立管理员观察实际状态；普通维护账号不具备跨角色活动细节，不能用其空结果证明。
        with psycopg.connect(data["database"]["admin"]) as observer:
            assert observer.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                "AND usename IN ('app_login','app_auth') AND state='idle in transaction'"
            ).fetchone() == (0,)
        trace(
            "heartbeat.json",
            {
                "input": "REAL_LOCAL_HTTP_IDLE_STREAM_AND_PG_ADMIN_OBSERVATION",
                "requestId": response.headers["x-request-id"],
                "elapsedSeconds": elapsed,
                "heartbeat": ": ping",
                "idleInTransaction": 0,
            },
        )
