"""分析受理/历史，复用Cookie与CSRF；不接受伪造用户或计算状态。"""

from uuid import UUID

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

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
