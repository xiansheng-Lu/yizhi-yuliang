"""分析受理/历史，复用Cookie与CSRF；不接受伪造用户或计算状态。"""

from uuid import UUID

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, JSONResponse

from yuliang.modules.analysis.drafts_models import ReportDraft
from yuliang.modules.analysis.drafts_service import DraftService
from yuliang.modules.analysis.models import AnalysisCreate
from yuliang.modules.analysis.service import AnalysisService
from yuliang.modules.identity.routes import SESSION_COOKIE, service
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/v1/business-analysis")


def analysis(request: Request) -> AnalysisService:
    instance = service(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    row = instance.row(token)
    if row is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "请先登录")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        instance.require_csrf(token, request.headers.get("X-CSRF-Token", ""))
    if request.query_params:
        raise ApiError(ErrorCode.INVALID_INPUT, "分析历史不接受客户端用户或租户覆盖")
    return AnalysisService(
        instance.app, VerifiedIdentity(row["tenant_id"], row["membership_id"], row["user_id"])
    )


@router.post("/tasks")
def create(value: AnalysisCreate, request: Request) -> JSONResponse:
    return success(
        request,
        analysis(request).create(value).model_dump(mode="json", by_alias=True, exclude_none=True),
    )


@router.get("/tasks")
def tasks(request: Request) -> JSONResponse:
    return success(
        request,
        [
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in analysis(request).tasks()
        ],
    )


@router.get("/tasks/{identifier}")
def task(identifier: UUID, request: Request) -> JSONResponse:
    return success(
        request,
        analysis(request).get(identifier).model_dump(mode="json", by_alias=True, exclude_none=True),
    )


@router.get("/results")
def results(request: Request) -> JSONResponse:
    return success(request, analysis(request).results())


@router.get("/results/{identifier}")
def result(identifier: UUID, request: Request) -> JSONResponse:
    return success(request, analysis(request).results(identifier)[0])


def drafts_service(request: Request) -> "DraftService":
    from yuliang.modules.analysis.drafts_service import DraftService

    current = analysis(request)
    return DraftService(current.engine, current.identity)


@router.get("/drafts")
def drafts_list(request: Request) -> JSONResponse:
    return success(request, drafts_service(request).list())


@router.get("/drafts/{identifier}")
def draft_get(identifier: UUID, request: Request) -> JSONResponse:
    return success(request, drafts_service(request).get(identifier))


@router.put("/drafts/{identifier}")
def draft_save(identifier: UUID, value: "ReportDraft", request: Request) -> JSONResponse:
    import json

    from yuliang.modules.analysis.drafts_service import DraftConflict
    from yuliang.platform.errors import failure

    try:
        return success(request, drafts_service(request).save(identifier, value))
    except DraftConflict as error:
        response = failure(request, error)
        body = json.loads(bytes(response.body))
        body["error"]["current"] = error.current
        return JSONResponse(body, status_code=409)


@router.get("/drafts/{identifier}/preview")
def draft_preview(identifier: UUID, request: Request) -> "HTMLResponse":
    from starlette.responses import HTMLResponse

    return HTMLResponse(
        drafts_service(request).preview(identifier),
        headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
