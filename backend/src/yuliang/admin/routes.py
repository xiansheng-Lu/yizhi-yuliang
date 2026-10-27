"""默认关闭的管理API；校验实际来源IP、会话、内部平台管理员角色。"""

from ipaddress import ip_address, ip_network

from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from starlette.responses import JSONResponse

from yuliang.modules.identity.routes import SESSION_COOKIE, service
from yuliang.modules.identity.service import digest
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/internal/admin")


@router.get("/directory")
def directory(request: Request) -> JSONResponse:
    try:
        host = ip_address(request.client.host if request.client else "")
    except ValueError:
        raise ApiError(ErrorCode.FORBIDDEN, "管理来源未获允许") from None
    if not any(
        host in ip_network(network) for network in request.app.state.settings.admin_allowed_networks
    ):
        raise ApiError(ErrorCode.FORBIDDEN, "管理来源未获允许")
    instance = service(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    row = instance.row(token)
    if not row:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "请先登录")
    identity = VerifiedIdentity(row["tenant_id"], row["membership_id"], row["user_id"])
    try:
        with business_transaction(instance.app, identity) as session:
            result = session.execute(
                text("SELECT access.admin_directory(:token,:request)"),
                {"token": digest(token), "request": request.state.request_id},
            ).scalar_one()
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) == "42501":
            raise ApiError(ErrorCode.FORBIDDEN, "没有平台管理权限") from None
        raise
    return success(request, result)
