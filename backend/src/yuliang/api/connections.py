"""F03连接管理；不接收客户端租户/账户关联或平台令牌。"""

from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.responses import JSONResponse

from yuliang.modules.catalog.service import ConnectionService
from yuliang.modules.connectors.runtime import PlatformRuntime
from yuliang.modules.identity.routes import SESSION_COOKIE, service
from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/v1/connections")


class DisconnectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    impactDigest: str = Field(pattern=r"^[0-9a-f]{64}$")


def connections(request: Request) -> ConnectionService:
    instance = service(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    row = instance.row(token)
    if not row:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "请先登录")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        instance.require_csrf(token, request.headers.get("X-CSRF-Token", ""))
    runtime = getattr(request.app.state, "platform_runtime", None)
    resources = (
        [
            port.resource
            for port in runtime.ports.values()
            if port.resource.tenant_id == row["tenant_id"]
        ]
        if isinstance(runtime, PlatformRuntime)
        else []
    )
    return ConnectionService(
        instance.app,
        VerifiedIdentity(row["tenant_id"], row["membership_id"], row["user_id"]),
        row["session_id"],
        UUID(request.state.request_id),
        SecretStr(request.app.state.settings.auth_audit_secret.get_secret_value()),
        implemented_reads=frozenset(
            item.connector_id for item in resources if item.readonly_interfaces
        ),
        reauthorization_applications=frozenset(item.id for item in resources),
    )


@router.get("")
def list_connections(request: Request) -> JSONResponse:
    return success(request, connections(request).listing())


@router.get("/{authorization_id}/disconnect-impact")
def disconnect_impact(authorization_id: UUID, request: Request) -> JSONResponse:
    return success(request, connections(request).preview(authorization_id))


@router.delete("/{authorization_id}")
def disconnect(authorization_id: UUID, value: DisconnectInput, request: Request) -> JSONResponse:
    return success(request, connections(request).disconnect(authorization_id, value.impactDigest))


@router.post("/{authorization_id}/refresh")
async def reauthorize(authorization_id: UUID, request: Request) -> JSONResponse:
    actor = connections(request)
    target = getattr(request.app.state, "platform_runtime", None)
    if not isinstance(target, PlatformRuntime) or target.oauth is None:
        return success(request, actor.reauthorize(authorization_id))
    from sqlalchemy import text

    from yuliang.api.platform_authorization import authorize
    from yuliang.platform.db import business_transaction

    with business_transaction(actor.engine, actor.identity) as session:
        actor._admin(session)
        application = session.execute(
            text("SELECT application_id FROM provider_authorizations WHERE id=:id"),
            {"id": authorization_id},
        ).scalar_one_or_none()
    if application is None:
        raise ApiError(ErrorCode.NO_DATA, "连接不存在")
    # 创建新的授权与证据，不复活旧的能力绑定或被断开的连接。
    return await authorize(application, request)
