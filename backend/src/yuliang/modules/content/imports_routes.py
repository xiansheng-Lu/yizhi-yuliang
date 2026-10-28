"""真实认证的元数据导入API；不读取浏览器本地文件或客户端路径。"""

from uuid import UUID

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from yuliang.modules.content.imports_models import ImportBundle
from yuliang.modules.content.imports_service import ContentImports
from yuliang.modules.content.routes import content, respond
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/v1/content/imports")


def imports(request: Request) -> ContentImports:
    if request.query_params:
        raise ApiError(ErrorCode.INVALID_INPUT, "导入查询参数不接受身份覆盖")
    return ContentImports(content(request))


@router.post("/preview")
def preview(value: ImportBundle, request: Request) -> JSONResponse:
    return respond(request, lambda: imports(request).preview(value))


@router.post("/apply/{local_id}")
def apply(local_id: UUID, value: ImportBundle, request: Request) -> JSONResponse:
    return respond(request, lambda: imports(request).apply(value, str(local_id)))


@router.post("/progress")
def progress(value: ImportBundle, request: Request) -> JSONResponse:
    return respond(request, lambda: imports(request).progress(value))
