"""平台上下文只接受产品线选择，内部ID/租户/操作可用性不由客户端授予。"""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from starlette.responses import JSONResponse

from yuliang.modules.configuration.integration_models import ProductLine
from yuliang.modules.configuration.integration_service import IntegrationService
from yuliang.modules.identity.routes import SESSION_COOKIE, service
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/v1/delivery/integrations")


@router.get("/context")
def context(request: Request, productLine: Annotated[ProductLine, Query()]) -> JSONResponse:
    instance = service(request)
    row = instance.row(request.cookies.get(SESSION_COOKIE, ""))
    if row is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "请先登录")
    if (
        set(request.query_params) != {"productLine"}
        or len(request.query_params.getlist("productLine")) != 1
    ):
        raise ApiError(ErrorCode.INVALID_INPUT, "只接受唯一的产品线参数，不接受跨线ID拼装")
    actor = IntegrationService(
        instance.app, VerifiedIdentity(row["tenant_id"], row["membership_id"], row["user_id"])
    )
    return success(request, actor.context(productLine).model_dump(mode="json", by_alias=True))
