"""内容模型默认关闭；仅显式服务端runtime注入可调用。"""

from uuid import UUID

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from yuliang.modules.content.ai_content_models import ContentConfirm, ContentPrepare
from yuliang.modules.content.ai_content_service import AIContent, ContentRuntime
from yuliang.modules.content.routes import content, respond

router = APIRouter(prefix="/v1/content")


def ai_content(request: Request) -> AIContent:
    runtime = getattr(request.app.state, "content_ai_runtime", None)
    return AIContent(content(request), runtime if isinstance(runtime, ContentRuntime) else None)


@router.post("/workflow-runs/{run}/steps/{code}/ai/prepare")
def prepare(run: UUID, code: str, value: ContentPrepare, request: Request) -> JSONResponse:
    return respond(request, lambda: ai_content(request).prepare(run, code, value))


@router.get("/ai-requests/{identifier}")
def get(identifier: UUID, request: Request) -> JSONResponse:
    return respond(request, lambda: ai_content(request).get(identifier))


@router.post("/ai-requests/{identifier}/confirm")
def confirm(identifier: UUID, value: ContentConfirm, request: Request) -> JSONResponse:
    return respond(request, lambda: ai_content(request).confirm(identifier, value))
