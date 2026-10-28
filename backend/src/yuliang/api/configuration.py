"""V9只读目录与完整配置上下文；不接受客户端授予能力或替换租户。"""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import ValidationError
from starlette.responses import JSONResponse

from yuliang.modules.configuration.models import ConfigurationContext, Product
from yuliang.modules.configuration.service import ConfigurationService
from yuliang.modules.identity.routes import SESSION_COOKIE, service
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/v1/delivery/configuration")


def configuration(request: Request) -> ConfigurationService:
    instance = service(request)
    row = instance.row(request.cookies.get(SESSION_COOKIE, ""))
    if row is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "请先登录")
    return ConfigurationService(
        instance.app,
        VerifiedIdentity(row["tenant_id"], row["membership_id"], row["user_id"]),
        request.app.state.settings.auth_audit_secret,
    )


@router.get("/catalog")
def catalog(request: Request, product: Annotated[Product, Query()]) -> JSONResponse:
    actor = configuration(request)
    if (
        set(request.query_params) != {"product"}
        or len(request.query_params.getlist("product")) != 1
    ):
        raise ApiError(ErrorCode.INVALID_INPUT, "目录仅接受唯一product参数")
    return success(request, actor.catalog(product).model_dump(mode="json", by_alias=True))


@router.get("/capabilities")
def capabilities(request: Request) -> JSONResponse:
    actor = configuration(request)
    # 前端contextQuery把两个数组用逗号连接，其余维度即使为空也显式传入。
    values: dict[str, object] = dict(request.query_params)
    if any(len(request.query_params.getlist(k)) != 1 for k in request.query_params):
        raise ApiError(ErrorCode.INVALID_INPUT, "配置上下文参数不能重复")
    for key in ("targetIds", "placementIds"):
        if key in values:
            values[key] = str(values[key]).split(",") if values[key] else []
    try:
        context = ConfigurationContext.model_validate(values)
    except ValidationError:
        raise ApiError(ErrorCode.INVALID_INPUT, "配置上下文缺失、含未知维度或格式不合法") from None
    return success(
        request,
        actor.capabilities(context).model_dump(mode="json", by_alias=True, exclude_none=True),
    )


@router.get("/options")
def options(request: Request) -> JSONResponse:
    actor = configuration(request)
    values: dict[str, object] = dict(request.query_params)
    if any(len(request.query_params.getlist(k)) != 1 for k in request.query_params):
        raise ApiError(ErrorCode.INVALID_INPUT, "资源查询参数不能重复")
    filters = {
        key: str(values.pop(key, "")) for key in ("kind", "q", "cursor", "category", "parentId")
    }
    for key in ("targetIds", "placementIds"):
        if key in values:
            values[key] = str(values[key]).split(",") if values[key] else []
    try:
        context = ConfigurationContext.model_validate(values)
    except ValidationError:
        raise ApiError(ErrorCode.INVALID_INPUT, "资源上下文缺失或格式不合法") from None
    runtime = getattr(request.app.state, "platform_runtime", None)
    if runtime is None:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "资源来源尚未接入")
    from uuid import UUID

    result = runtime.lookup_options(
        actor.identity,
        context,
        filters["kind"],
        q=filters["q"],
        cursor=filters["cursor"] or None,
        category=filters["category"],
        parent_id=filters["parentId"],
        request_id=UUID(request.state.request_id),
    )
    return success(request, result)
