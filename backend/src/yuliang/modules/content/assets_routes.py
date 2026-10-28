"""已确认素材团队读取、固定版本下载和逻辑归档；不接受客户端对象地址。"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from yuliang.modules.content.assets import AssetService
from yuliang.modules.content.routes import content
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/v1/content/assets")


def assets(request: Request) -> AssetService:
    base = content(request)
    if base.shop is None:
        raise ApiError(ErrorCode.INVALID_INPUT, "素材需要明确店铺")
    return AssetService(
        base.engine,
        base.identity,
        base.shop,
        base.blob,
        ttl=request.app.state.settings.blob_download_ttl_seconds,
    )


@router.get("")
def listing(
    request: Request,
    productId: str | None = None,
    kind: Literal["image", "text"] | None = None,
    cursor: UUID | None = None,
) -> JSONResponse:
    if not set(request.query_params) <= {"productId", "kind", "cursor"}:
        raise ApiError(ErrorCode.INVALID_INPUT, "素材查询不接受对象地址覆盖")
    return success(request, assets(request).list(productId, kind, cursor))


@router.get("/{identifier}")
def get(identifier: UUID, request: Request) -> JSONResponse:
    return success(request, assets(request).get(identifier))


@router.get("/{identifier}/download")
def download(identifier: UUID, request: Request) -> JSONResponse:
    if request.query_params:
        raise ApiError(ErrorCode.INVALID_INPUT, "下载只接素材ID，不接受key/version/bucket覆盖")
    return success(request, assets(request).download(identifier))


@router.delete("/{identifier}")
def archive(identifier: UUID, request: Request) -> JSONResponse:
    if request.query_params or request.headers.get("Content-Length", "0") not in {"0", ""}:
        raise ApiError(ErrorCode.INVALID_INPUT, "素材归档只接原ID")
    assets(request).archive(identifier)
    return success(request,None)
