"""人工SOP动作；实际Cookie/CSRF与店铺权限沿用云稿。"""

from uuid import UUID

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from yuliang.modules.content.routes import content, respond
from yuliang.modules.content.workflows_execution_models import Accept, Execute, Redo, Skip
from yuliang.modules.content.workflows_execution_service import WorkflowExecution

router = APIRouter(prefix="/v1/content/workflow-runs")


@router.get("/{identifier}/execution")
def state(identifier: UUID, request: Request) -> JSONResponse:
    return respond(request, lambda: WorkflowExecution(content(request)).get(identifier))


@router.post("/{identifier}/steps/{code}/execute")
def execute(identifier: UUID, code: str, value: Execute, request: Request) -> JSONResponse:
    return respond(
        request, lambda: WorkflowExecution(content(request)).execute(identifier, code, value)
    )


@router.post("/{identifier}/steps/{code}/accept")
def accept(identifier: UUID, code: str, value: Accept, request: Request) -> JSONResponse:
    return respond(
        request, lambda: WorkflowExecution(content(request)).accept(identifier, code, value)
    )


@router.post("/{identifier}/steps/{code}/skip")
def skip(identifier: UUID, code: str, value: Skip, request: Request) -> JSONResponse:
    return respond(
        request, lambda: WorkflowExecution(content(request)).skip(identifier, code, value)
    )


@router.post("/{identifier}/steps/{code}/redo")
def redo(identifier: UUID, code: str, value: Redo, request: Request) -> JSONResponse:
    return respond(
        request, lambda: WorkflowExecution(content(request)).redo(identifier, code, value)
    )
