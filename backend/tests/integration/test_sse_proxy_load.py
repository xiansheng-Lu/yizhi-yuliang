"""Actual private nginx, 50 TCP streams and production 25-second heartbeats."""

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import pytest
from sqlalchemy import text

from yuliang.modules.content.workflows_service import WorkflowService
from yuliang.modules.identity.routes import SESSION_COOKIE
from yuliang.platform.events import CoreEventStream

from .sse_proxy_harness import first_frame, proxy_application, raw_frames, resources
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
from .test_infrastructure import POSIX_NGINX_PROXY
from .test_shop_product_details import details as details
from .test_shop_products import products as products

pytestmark = [pytest.mark.integration, POSIX_NGINX_PROXY]


def evidence(name: str, value: dict[str, Any]) -> None:
    if directory := os.getenv("YULIANG_SSE_PROXY_EVIDENCE"):
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(
            json.dumps(
                value
                | dict(
                    platformCalls=0,
                    paidCalls=0,
                    productionTls="NOT_RUN",
                    frontendF12="PENDING",
                    humanSignoff="PENDING",
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def cookie(d: dict[str, Any]) -> dict[str, str]:
    return {SESSION_COOKIE: str(d["client"].cookies[SESSION_COOKIE])}


def baseline(d: dict[str, Any]) -> str:
    stream = CoreEventStream(d["engine"], d["identity"])
    stream.project(500)
    return stream.read().cursor


def database_sample(d: dict[str, Any]) -> dict[str, Any]:
    with psycopg.connect(d["database"]["admin"], autocommit=True, connect_timeout=5) as observer:
        row = observer.execute("""SELECT count(*),
            count(*) FILTER(WHERE state='idle in transaction'),
            count(*) FILTER(WHERE state='idle in transaction'
              AND clock_timestamp()-xact_start>interval '2 seconds'),
            coalesce(max(extract(epoch FROM clock_timestamp()-xact_start)),0)
            FROM pg_stat_activity WHERE datname=current_database()
            AND usename IN ('app_login','app_auth')""").fetchone()
    assert row is not None
    return dict(
        connections=row[0],
        idleInTransaction=row[1],
        idleTransactionOver2s=row[2],
        maxTransactionAgeSeconds=float(row[3]),
    )


def percentile(values: list[float], q: float) -> float:
    return sorted(values)[min(len(values) - 1, int((len(values) - 1) * q))]


@pytest.mark.asyncio
async def test_real_nginx_fifty_streams_flush_heartbeat_resume_and_cleanup(
    analysis_ready: dict[str, Any],
    tmp_path: Path,
) -> None:
    from yuliang.api import events as events_api

    assert events_api.HEARTBEAT_SECONDS == 25 and events_api.POLL_SECONDS == 1
    d = analysis_ready
    cursor = baseline(d)
    records: list[dict[str, Any]] = [
        dict(connection=i, firstFrameSeconds=None, pings=[], events=[], cursor=cursor)
        for i in range(50)
    ]
    samples: list[dict[str, Any]] = []
    controls: dict[str, float] = {}
    ready, stop = asyncio.Event(), asyncio.Event()
    connected = 0
    started = time.monotonic()
    with proxy_application(d, tmp_path) as h:
        direct = await first_frame(h["upstream"], cookie(d), cursor)
        proxied = await first_frame(h["url"], cookie(d), cursor)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(first_frame(h["fault"], cookie(d), cursor, timeout=2), 2.5)
        negative_control = True
        async with httpx.AsyncClient(
            headers={"Origin": "http://testserver"},
            cookies=cookie(d),
            limits=httpx.Limits(max_connections=60, max_keepalive_connections=60),
            timeout=40,
        ) as client:

            async def capture(index: int) -> None:
                nonlocal connected
                row = records[index]
                beginning = time.monotonic()
                async with client.stream(
                    "GET", h["url"] + "/v1/events", headers={"Last-Event-ID": cursor}
                ) as response:
                    assert response.status_code == 200
                    incoming = raw_frames(response)
                    arrived, frame = await asyncio.wait_for(anext(incoming), 5)
                    assert frame == [": connected"]
                    row["firstFrameSeconds"] = arrived - beginning
                    connected += 1
                    if connected == 50:
                        ready.set()
                    while not stop.is_set():
                        pending = asyncio.ensure_future(anext(incoming))
                        ending = asyncio.create_task(stop.wait())
                        done, _ = await asyncio.wait(
                            {pending, ending}, timeout=40, return_when=asyncio.FIRST_COMPLETED
                        )
                        if not done:
                            pending.cancel()
                            ending.cancel()
                            await asyncio.gather(pending, ending, return_exceptions=True)
                            raise AssertionError("stream heartbeat/frame timeout")
                        if ending in done:
                            pending.cancel()
                            await asyncio.gather(pending, return_exceptions=True)
                            break
                        ending.cancel()
                        await asyncio.gather(ending, return_exceptions=True)
                        stamp, frame = pending.result()
                        if frame == [": ping"]:
                            row["pings"].append(stamp - started)
                        else:
                            assert len(frame) == 3 and frame[1] == "event: analysis.changed"
                            body = json.loads(frame[2].removeprefix("data: "))
                            row["cursor"] = frame[0].removeprefix("id: ")
                            row["events"].append(
                                dict(
                                    entityId=body["entityId"],
                                    version=body["version"],
                                    atSeconds=stamp - started,
                                    topic="analysis.changed",
                                )
                            )

            tasks = [asyncio.create_task(capture(i)) for i in range(50)]

            async def monitor() -> None:
                while not stop.is_set():
                    sample = await asyncio.to_thread(database_sample, d)
                    samples.append(
                        dict(
                            atSeconds=time.monotonic() - started,
                            database=sample,
                            processes=resources(h),
                        )
                    )
                    try:
                        await asyncio.wait_for(stop.wait(), 5)
                    except TimeoutError:
                        pass

            monitoring: asyncio.Task[None] | None = None
            load_started = 0.0
            try:
                await asyncio.wait_for(ready.wait(), 10)
                assert not any(t.done() for t in tasks), "a stream closed before all 50 connected"
                load_started = time.monotonic()
                monitoring = asyncio.create_task(monitor())
                for delay in (0, 20):
                    if delay:
                        await asyncio.sleep(delay)
                    called = time.monotonic() - started
                    value = d["input"].model_copy(update={"idempotency_key": uuid4().hex})
                    task = await asyncio.to_thread(d["analysis"].create, value)
                    controls[str(task.id)] = called
                await asyncio.sleep(max(0, 65 - (time.monotonic() - load_started)))
                assert not any(t.done() for t in tasks), "unexpected stream EOF/error during load"
                stop.set()
                await asyncio.wait_for(asyncio.gather(*tasks), 10)
                if monitoring:
                    await monitoring
            finally:
                stop.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if monitoring:
                    await asyncio.gather(monitoring, return_exceptions=True)
        elapsed = time.monotonic() - load_started
        # A new real task appears while disconnected; proxy must preserve Last-Event-ID.
        last = records[0]["cursor"]
        third = await asyncio.to_thread(
            d["analysis"].create, d["input"].model_copy(update={"idempotency_key": uuid4().hex})
        )
        async with httpx.AsyncClient(
            headers={"Origin": "http://testserver"}, cookies=cookie(d), timeout=10
        ) as client:
            async with client.stream(
                "GET", h["url"] + "/v1/events", headers={"Last-Event-ID": last}
            ) as response:
                incoming = raw_frames(response)
                assert (await anext(incoming))[1] == [": connected"]
                _, frame = await asyncio.wait_for(anext(incoming), 5)
                resumed = json.loads(frame[2].removeprefix("data: "))
                assert resumed["entityId"] == str(third.id) and resumed["version"] == 1
            assert (
                await client.get(
                    h["url"] + "/v1/events", headers={"Origin": "https://evil.invalid"}
                )
            ).status_code == 403
            client.cookies.clear()
            assert (await client.get(h["url"] + "/v1/events")).status_code == 401
        firsts = [float(r["firstFrameSeconds"]) for r in records]
        latencies: list[float] = []
        for row in records:
            assert len(row["pings"]) >= 2
            assert all(
                20 <= b - a <= 35 for a, b in zip(row["pings"], row["pings"][1:], strict=False)
            )
            assert len(row["events"]) == 2
            assert {e["entityId"] for e in row["events"]} == set(controls)
            assert all(e["version"] == 1 for e in row["events"])
            latencies.extend(e["atSeconds"] - controls[e["entityId"]] for e in row["events"])
        assert percentile(firsts, 0.95) <= 2 and max(firsts) <= 5
        assert percentile(latencies, 0.95) <= 3 and max(latencies) <= 5
        assert len(samples) >= 10
        assert max(s["processes"]["downstreamEstablished"] for s in samples) >= 50
        assert max(s["database"]["idleTransactionOver2s"] for s in samples) == 0
        details: dict[str, Any] = dict(
            nginx=h["nginx_version"],
            configSha256=h["config_sha256"],
            targetStreams=50,
            connectedStreams=connected,
            durationSeconds=elapsed,
            directFirstFrameSeconds=direct,
            proxyFirstFrameSeconds=proxied,
            rateLimitedNegativeControlDetected=negative_control,
            negativeControl="nginx 1 byte/second; buffering on alone did not delay first frame",
            firstFrameP95=percentile(firsts, 0.95),
            firstFrameP50=percentile(firsts, 0.5),
            firstFrameMax=max(firsts),
            eventCallToFrameP95=percentile(latencies, 0.95),
            eventCallToFrameMax=max(latencies),
            heartbeatSeconds=25,
            minimumPings=min(len(r["pings"]) for r in records),
            businessSubmissions=3,
            deliveredLoadEvents=sum(len(r["events"]) for r in records),
            unexpectedEof=0,
            streamTimeouts=0,
            streamHttpErrors=0,
            sampleCount=len(samples),
            records=records,
            samples=samples,
            cleanup=h["cleanup"],
            identities=1,
            interpretation="50 actual TCP SSE streams, not 50 distinct tenants",
        )
        assert len(d["analysis"].tasks()) == 3, "reconnection must not recreate analysis tasks"
    assert all(
        details["cleanup"][k]
        for k in (
            "ownNginxExited",
            "ownUvicornExited",
            "normalPortClosed",
            "faultPortClosed",
            "appPortClosed",
        )
    )
    evidence("fifty-streams.json", details | dict(result="PASS"))


@pytest.mark.asyncio
async def test_proxy_buffered_analysis_scope_revocation_drops_frame(
    analysis_ready: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yuliang.api import events as events_api

    d = analysis_ready
    cursor = baseline(d)
    task = d["analysis"].create(d["input"])
    polled, changed = threading.Event(), threading.Event()
    errors: list[BaseException] = []
    original = events_api.EventSubscription.poll

    def buffered(subscription: Any, after: str | None) -> Any:
        page = original(subscription, after)
        assert any(e.data.entity_id == task.id for e in page.events)
        polled.set()
        assert changed.wait(10) and not errors
        return page

    def revoke() -> None:
        try:
            assert polled.wait(10)
            with d["maintenance"].begin() as owner:
                owner.execute(text("SET LOCAL ROLE app_owner"))
                owner.execute(
                    text("UPDATE shops SET status='UNKNOWN' WHERE id=:id"), dict(id=d["shop"])
                )
        except BaseException as exc:
            errors.append(exc)
        finally:
            changed.set()

    monkeypatch.setattr(events_api.EventSubscription, "poll", buffered)
    worker = threading.Thread(target=revoke, daemon=True)
    worker.start()
    received = []
    with proxy_application(d, tmp_path) as h:
        async with httpx.AsyncClient(
            headers={"Origin": "http://testserver"}, cookies=cookie(d), timeout=15
        ) as client:
            async with client.stream(
                "GET", h["url"] + "/v1/events", headers={"Last-Event-ID": cursor}
            ) as response:
                assert response.status_code == 200
                incoming = raw_frames(response)
                assert (await anext(incoming))[1] == [": connected"]
                _, frame = await asyncio.wait_for(anext(incoming), 5)
                received.append(frame[1])
                assert frame[1] == "event: resync"
                assert (
                    json.loads(frame[2].removeprefix("data: "))["reason"] == "AUTHORIZATION_CHANGED"
                )
                with pytest.raises(StopAsyncIteration):
                    await anext(incoming)
        cleanup = h["cleanup"]
    worker.join(timeout=5)
    assert not worker.is_alive() and not errors
    assert all(
        e.data.entity_id != task.id
        for e in CoreEventStream(d["engine"], d["identity"]).read().events
    )
    evidence(
        "buffered-revocation.json",
        dict(
            result="PASS",
            actualNginx=True,
            actualPollBarrier=True,
            receivedEventTypes=received,
            bufferedAnalysisLeakCount=0,
            cleanup=cleanup,
        ),
    )


@pytest.mark.asyncio
async def test_proxy_workflow_actual_creation_and_resume(
    content_ready: dict[str, Any], tmp_path: Path
) -> None:
    d = content_ready
    cursor = baseline(d)
    service = WorkflowService(d["content"])
    first = service.create(workflow_request(d, uuid4().hex))
    ids = []
    with proxy_application(d, tmp_path) as h:
        async with httpx.AsyncClient(
            headers={"Origin": "http://testserver"}, cookies=cookie(d), timeout=10
        ) as client:
            for index in range(2):
                expected = first if index == 0 else service.create(workflow_request(d, uuid4().hex))
                async with client.stream(
                    "GET", h["url"] + "/v1/events", headers={"Last-Event-ID": cursor}
                ) as response:
                    assert response.status_code == 200
                    incoming = raw_frames(response)
                    assert (await anext(incoming))[1] == [": connected"]
                    _, frame = await asyncio.wait_for(anext(incoming), 5)
                    assert frame[1] == "event: workflow.changed"
                    body = json.loads(frame[2].removeprefix("data: "))
                    assert body["entityId"] == expected["id"] and body["version"] == 1
                    cursor = frame[0].removeprefix("id: ")
                    ids.append(body["entityId"])
        cleanup = h["cleanup"]
    assert len(set(ids)) == 2
    evidence(
        "workflow-proxy-resume.json",
        dict(
            result="PASS",
            actualNginx=True,
            actualBusinessCreations=2,
            resumedDistinctResources=2,
            cleanup=cleanup,
        ),
    )
