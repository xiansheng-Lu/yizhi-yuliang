"""SOP目录/版本与创作线入口；Cookie、CSRF、显式店铺复用云稿。"""

from uuid import UUID

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from yuliang.modules.content.routes import content, respond
from yuliang.modules.content.workflows_models import RunCreate, TemplateDefinition
from yuliang.modules.content.workflows_service import WorkflowService

router = APIRouter(prefix="/v1/content")


@router.get("/workflow-templates")
def templates(request: Request) -> JSONResponse:
    return respond(request, lambda: WorkflowService(content(request)).templates())


@router.post("/workflow-templates/{identifier}/versions")
def append(identifier: UUID, value: TemplateDefinition, request: Request) -> JSONResponse:
    return respond(
        request, lambda: WorkflowService(content(request)).append_template(identifier, value)
    )


@router.post("/workflow-runs")
def create(value: RunCreate, request: Request) -> JSONResponse:
    return respond(request, lambda: WorkflowService(content(request)).create(value))


@router.get("/workflow-runs/{identifier}")
def get(identifier: UUID, request: Request) -> JSONResponse:
    return respond(request, lambda: WorkflowService(content(request)).get(identifier))


@router.get("/workflow-runs")
def list_runs(request: Request) -> JSONResponse:
    return respond(request, lambda: WorkflowService(content(request)).list_runs())
