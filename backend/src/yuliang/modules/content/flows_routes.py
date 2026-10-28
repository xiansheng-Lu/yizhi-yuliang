"""版本绑定的原子派生，不调用平台或生成供应商。"""

from uuid import UUID

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from yuliang.modules.content.flows import FlowCreate, FlowService
from yuliang.modules.content.routes import content, respond
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/v1/content/documents")


@router.post("/{identifier}/flow")
def derive(identifier: UUID, value: FlowCreate, request: Request) -> JSONResponse:
    actor = content(request)
    if request.query_params:
        raise ApiError(ErrorCode.INVALID_INPUT, "派生请求只接受固定body，不接受身份或范围查询覆盖")
    return respond(request, lambda: FlowService(actor).create(identifier, value))
