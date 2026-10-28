"""V9既有POST queries入口；会话/CSRF/范围与资源能力分别验证。"""

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from yuliang.modules.identity.routes import SESSION_COOKIE, service
from yuliang.modules.reporting.query_models import QueryRequest
from yuliang.modules.reporting.query_service import QueryService
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/v1/delivery/integrations")


@router.post("/queries")
def query(value: QueryRequest, request: Request) -> JSONResponse:
    instance = service(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    row = instance.row(token)
    if row is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "请先登录")
    instance.require_csrf(token, request.headers.get("X-CSRF-Token", ""))
    if request.query_params:
        raise ApiError(
            ErrorCode.INVALID_INPUT, "queries仅接受冻结body，不接受租户/账户查询参数覆盖"
        )
    actor = QueryService(
        instance.app, VerifiedIdentity(row["tenant_id"], row["membership_id"], row["user_id"])
    )
    return success(request, actor.query(value).model_dump(mode="json", by_alias=True))
