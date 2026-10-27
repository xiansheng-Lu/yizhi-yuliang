from uuid import UUID

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from yuliang.modules.identity.routes import SESSION_COOKIE, service
from yuliang.modules.workspace.models import (
    JobAttachment,
    MessageSend,
    ProjectAssignment,
    ProjectCreate,
)
from yuliang.modules.workspace.service import WorkspaceService
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/v1")


def workspace(request: Request) -> WorkspaceService:
    instance = service(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    row = instance.row(token)
    if row is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "请先登录")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        instance.require_csrf(token, request.headers.get("X-CSRF-Token", ""))
    identity = VerifiedIdentity(row["tenant_id"], row["membership_id"], row["user_id"])
    return WorkspaceService(instance.app, identity, UUID(request.state.request_id))


@router.get("/workspace/history")
def history(request: Request) -> JSONResponse:
    return success(request, workspace(request).history())


@router.post("/projects")
def create_project(value: ProjectCreate, request: Request) -> JSONResponse:
    return success(request, workspace(request).create_project(value.name))


@router.post("/conversation-messages")
def send_message(value: MessageSend, request: Request) -> JSONResponse:
    return success(request, workspace(request).send(value))


@router.patch("/conversations/{conversation_id}")
def move(conversation_id: UUID, value: ProjectAssignment, request: Request) -> JSONResponse:
    return success(request, workspace(request).move(conversation_id, value.projectId))


@router.post("/conversations/{conversation_id}/jobs")
def attach(conversation_id: UUID, value: JobAttachment, request: Request) -> JSONResponse:
    return success(
        request, workspace(request).attach(conversation_id, value.messageId, value.jobId)
    )


@router.put("/command-jobs/{job_id}/project")
def assign(job_id: UUID, value: ProjectAssignment, request: Request) -> JSONResponse:
    workspace(request).assign(job_id, value.projectId)
    return success(request, None)
