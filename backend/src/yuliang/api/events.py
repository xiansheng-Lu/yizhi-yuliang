"""只读SSE：逐次查当前会话/范围，短事务之间异步等待，绝不派发业务写。"""

import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from pydantic import SecretStr
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from yuliang.modules.identity.routes import SESSION_COOKIE, service
from yuliang.modules.identity.service import IdentityService
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.errors import ApiError, ErrorCode
from yuliang.platform.events import CoreEventStream, EventPage, StreamEvent

router = APIRouter(prefix="/v1")
POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 25.0


def event_frame(tenant: int, event: StreamEvent) -> str:
    return (
        f"id: {tenant}:{event.seq}\nevent: {event.event}\n"
        f"data: {event.data.model_dump_json(by_alias=True)}\n\n"
    )


def resync_frame(cursor: str, reason: str) -> str:
    payload = json.dumps({"reason": reason}, separators=(",", ":"))
    return f"id: {cursor}\nevent: resync\ndata: {payload}\n\n"


class EventSubscription:
    def __init__(self, instance: IdentityService, token: str) -> None:
        self.instance, self.token = instance, SecretStr(token)
        row = instance.row(token)
        if row is None:
            raise ApiError(ErrorCode.UNAUTHENTICATED, "请先登录")
        self.session_id = row["session_id"]
        self.identity = VerifiedIdentity(row["tenant_id"], row["membership_id"], row["user_id"])
        self.stream = CoreEventStream(instance.app, self.identity)
        self.fingerprint = self.stream.permission_fingerprint()

    def check(self) -> bool:
        row = self.instance.row(self.token.get_secret_value())
        if row is None or (
            row["session_id"],
            row["tenant_id"],
            row["membership_id"],
            row["user_id"],
        ) != (
            self.session_id,
            self.identity.tenant_id,
            self.identity.membership_id,
            self.identity.user_id,
        ):
            raise ApiError(ErrorCode.UNAUTHENTICATED, "事件会话已失效")
        return self.stream.permission_fingerprint() == self.fingerprint

    def poll(self, cursor: str | None) -> EventPage:
        self.stream.capture_due()
        self.stream.project()
        self.stream.prune()
        return self.stream.read(cursor)

    async def frames(self, request: Request, page: EventPage) -> AsyncIterator[str]:
        # 立即刷新HTTP头，空租户也能建立流；此注释不推进游标。
        yield ": connected\n\n"
        heartbeat = time.monotonic()
        while not await request.is_disconnected():
            try:
                if not await run_in_threadpool(self.check):
                    current = await run_in_threadpool(self.stream.read)
                    yield resync_frame(current.cursor, "AUTHORIZATION_CHANGED")
                    return
                if page.resync_reason:
                    yield resync_frame(page.cursor, page.resync_reason)
                    return
                for event in page.events:
                    if await request.is_disconnected():
                        return
                    if not await run_in_threadpool(self.check):
                        current = await run_in_threadpool(self.stream.read)
                        yield resync_frame(current.cursor, "AUTHORIZATION_CHANGED")
                        return
                    yield event_frame(self.identity.tenant_id, event)
                cursor = page.cursor
                if time.monotonic() - heartbeat >= HEARTBEAT_SECONDS:
                    yield ": ping\n\n"
                    heartbeat = time.monotonic()
                if not page.has_more:
                    await asyncio.sleep(POLL_SECONDS)
                page = await run_in_threadpool(self.poll, cursor)
            except ApiError:
                # 退出、过期、移除或吊销时结束，不向已失效身份继续发数据/水位。
                return


@router.get("/events")
def subscribe(request: Request) -> StreamingResponse:
    lease = EventSubscription(service(request), request.cookies.get(SESSION_COOKIE, ""))
    initial = lease.poll(request.headers.get("last-event-id"))
    return StreamingResponse(
        lease.frames(request, initial),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
