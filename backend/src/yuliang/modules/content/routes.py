"""云文档接口；显式店铺范围、Cookie鉴权、CSRF及409当前副本。"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from yuliang.infrastructure.blob import BlobStore
from yuliang.modules.content.models import Document, DocumentDelete, Kind
from yuliang.modules.content.service import ContentService, DocumentConflict
from yuliang.modules.identity.routes import SESSION_COOKIE, service
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode, failure

router = APIRouter(prefix="/v1/content/documents")


def content(request: Request) -> ContentService:
    instance = service(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    row = instance.row(token)
    if row is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "请先登录")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        instance.require_csrf(token, request.headers.get("X-CSRF-Token", ""))
    raw = request.headers.get("X-Shop-Id")
    if not raw:
        raise ApiError(ErrorCode.INVALID_INPUT, "云稿请求需要X-Shop-Id店铺范围")
    try:
        shop = UUID(raw)
    except ValueError:
        raise ApiError(ErrorCode.INVALID_INPUT, "X-Shop-Id格式非法") from None
    blob = getattr(request.app.state, "blob_store", None)
    return ContentService(
        instance.app,
        VerifiedIdentity(row["tenant_id"], row["membership_id"], row["user_id"]),
        shop,
        blob=blob if isinstance(blob, BlobStore) else None,
    )


def respond(request: Request, action: Callable[[], Any]) -> JSONResponse:
    try:
        return success(request, action())
    except DocumentConflict as exc:
        response = failure(request, exc)
        import json

        body = json.loads(bytes(response.body))
        body["error"]["current"] = exc.current
        return JSONResponse(
            body,
            status_code=409,
            headers={"Cache-Control": "no-store", "X-Request-Id": request.state.request_id},
        )


@router.get("")
def documents(
    request: Request,
    kind: Kind | None = None,
    productId: str | None = None,
    cursor: UUID | None = None,
) -> JSONResponse:
    if not set(request.query_params) <= {"kind", "productId", "cursor"}:
        raise ApiError(ErrorCode.INVALID_INPUT, "文档查询参数非法")
    return respond(request, lambda: content(request).list(kind, productId, cursor))


@router.get("/{identifier}")
def get(identifier: UUID, request: Request) -> JSONResponse:
    return respond(request, lambda: content(request).get(identifier))


@router.put("/{identifier}")
def save(identifier: UUID, value: Document, request: Request) -> JSONResponse:
    return respond(request, lambda: content(request).save(identifier, value))


@router.delete("/{identifier}")
def delete(identifier: UUID, value: DocumentDelete, request: Request) -> JSONResponse:
    return respond(request, lambda: content(request).delete(identifier, value.version))
