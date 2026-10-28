"""私有素材上传计划；店铺/商品/文档来源校验，签名不落公共日志。"""

from uuid import UUID

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from yuliang.infrastructure.blob import BlobStore
from yuliang.modules.content.routes import content
from yuliang.modules.content.uploads import UploadService
from yuliang.modules.content.uploads_models import UploadCreate
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/v1/content/uploads")


def uploads(request: Request) -> UploadService:
    base = content(request)
    blob = getattr(request.app.state, "blob_store", None)
    if not isinstance(blob, BlobStore) or base.shop is None:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "私有素材上传尚未配置或未启用")
    settings = request.app.state.settings
    return UploadService(
        base.engine, base.identity, base.shop, blob, ttl=settings.blob_upload_ttl_seconds
    )


@router.post("")
def create(value: UploadCreate, request: Request) -> JSONResponse:
    return success(request, uploads(request).create(value))


@router.get("/{identifier}")
def get(identifier: UUID, request: Request) -> JSONResponse:
    return success(request, uploads(request).get(identifier))


@router.post("/{identifier}/complete")
def complete(identifier: UUID, request: Request) -> JSONResponse:
    if request.query_params or request.headers.get("Content-Length", "0") not in {"0", ""}:
        raise ApiError(ErrorCode.INVALID_INPUT, "完成确认只接原上传ID，不接受key/桶或元数据覆盖")
    return success(request, uploads(request).complete(identifier))
