"""素材账号意图实际HTTP；不接受客户端原生标识/主体/actor或文件证明。"""

from uuid import UUID

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from yuliang.modules.content.account_links_models import LinkCancel, LinkChange
from yuliang.modules.content.account_links_service import AccountLinks
from yuliang.modules.content.assets_routes import assets
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/v1/content/assets")


def links(request: Request) -> AccountLinks:
    if request.query_params:
        raise ApiError(ErrorCode.INVALID_INPUT, "账号意图请求不接受查询身份覆盖")
    return AccountLinks(assets(request))


@router.get("/{asset}/account-links")
def listing(asset: UUID, request: Request) -> JSONResponse:
    return success(request, links(request).list(asset))


@router.post("/{asset}/account-links")
def link(asset: UUID, value: LinkChange, request: Request) -> JSONResponse:
    return success(request, links(request).link(asset, value))


@router.post("/{asset}/account-links/{identifier}/cancel")
def cancel(asset: UUID, identifier: UUID, value: LinkCancel, request: Request) -> JSONResponse:
    return success(request, links(request).cancel(asset, identifier, value.expectedVersion))
