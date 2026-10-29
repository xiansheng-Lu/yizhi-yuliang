"""生成报价/确认/账本API；默认不挂真实付费供应商。"""

from uuid import UUID

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from yuliang.modules.content.generation_models import GenerationCreate, GenerationInput
from yuliang.modules.content.generation_service import GenerationService
from yuliang.modules.content.routes import content
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/v1/content/generations")


def generations(request: Request) -> GenerationService:
    if request.query_params:
        raise ApiError(ErrorCode.INVALID_INPUT, "生成接口不接受query身份覆盖")
    scope = content(request)
    factory = request.app.state.imagegen_provider
    provider = factory(scope) if factory is not None else None
    return GenerationService(scope, provider)


@router.post("/quotes")
def quote(value: GenerationInput, request: Request) -> JSONResponse:
    return success(request, generations(request).quote(value))


@router.post("/confirmations")
def confirm(value: GenerationCreate, request: Request) -> JSONResponse:
    generations(request).confirm(value)
    return success(request, None)


@router.post("")
def create(value: GenerationCreate, request: Request) -> JSONResponse:
    return success(request, generations(request).create(value))


@router.get("/{identifier}")
def get(identifier: UUID, request: Request) -> JSONResponse:
    return success(request, generations(request).get(identifier))


@router.post("/{identifier}/cancel-unsent")
def cancel(identifier: UUID, request: Request) -> JSONResponse:
    return success(request, generations(request).cancel_unsent(identifier))
