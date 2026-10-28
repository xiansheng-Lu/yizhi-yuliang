"""真实会话/CSRF发起授权，ADQ回调以一次性state恢复原会话；不返回令牌。"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import SecretStr
from starlette.responses import JSONResponse

from yuliang.api.connections import connections
from yuliang.modules.connectors.oauth import OAuthRejected
from yuliang.modules.connectors.renewal import renew_authorization
from yuliang.modules.connectors.runtime import PlatformRuntime
from yuliang.platform.envelope import success
from yuliang.platform.errors import ApiError, ErrorCode

router = APIRouter(prefix="/v1/connections")


def runtime(request: Request) -> PlatformRuntime:
    value = getattr(request.app.state, "platform_runtime", None)
    if not isinstance(value, PlatformRuntime):
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "平台资源尚未完成交接")
    return value


@router.get("/applications")
def applications(request: Request) -> JSONResponse:
    actor = connections(request)
    return success(request, runtime(request).listing(actor.identity, actor.session_id))


@router.post("/applications/{application_id}/authorize")
async def authorize(application_id: UUID, request: Request) -> JSONResponse:
    actor, target = connections(request), runtime(request)
    try:
        port = target.require(actor.identity, actor.session_id, application_id)
        if target.oauth is None:
            raise OAuthRejected()
        start = target.oauth.start(
            actor.identity, actor.session_id, port.application(), actor.request_id
        )
        response: dict[str, Any]
        if port.resource.mode == "ADQ_OAUTH":
            response = {
                "authorizationMode": "OAUTH",
                "authorizationUrl": port.authorize_url(start.state),
                "expiresAt": start.expires_at.isoformat(),
            }
        else:
            # 小店自研只使用本应用凭据，不构造外部授权页或要求不存在的商家回调。
            response = await target.oauth.complete(
                start.state, SecretStr("SELF_CREDENTIAL_ACTIVATION"), port.application(), port
            )
            response["authorizationMode"] = "SELF_CREDENTIAL"
        return success(request, response)
    except OAuthRejected:
        raise ApiError(
            ErrorCode.CAPABILITY_UNAVAILABLE, "授权未完成，请由原管理员核对资源后重试"
        ) from None


@router.get("/oauth/{application_id}/callback")
async def callback(application_id: UUID, request: Request) -> JSONResponse:
    target = runtime(request)
    port = target.ports.get(application_id)
    if port is None or port.resource.mode != "ADQ_OAUTH" or target.oauth is None:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "授权回调不可用")
    values = getattr(request.state, "platform_oauth_query", {})
    state = values.get("state", SecretStr(""))
    code = values.get("authorization_code", SecretStr(""))
    try:
        result = await target.oauth.complete(state, code, port.application(), port)
        return success(request, result)
    except OAuthRejected:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "授权回调未完成，请重新发起") from None


@router.post("/{authorization_id}/renew-token")
async def renew(authorization_id: UUID, request: Request) -> JSONResponse:
    actor, target = connections(request), runtime(request)
    if target.oauth is None:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "平台授权服务尚未启用")
    # 只在本租户查应用归属；HTTP不接收application/tenant/account或token。
    from sqlalchemy import text

    from yuliang.platform.db import business_transaction

    try:
        with business_transaction(actor.engine, actor.identity) as session:
            application = session.execute(
                text("SELECT application_id FROM provider_authorizations WHERE id=:id"),
                {"id": authorization_id},
            ).scalar_one_or_none()
        if application is None:
            raise OAuthRejected()
        port = target.require(actor.identity, actor.session_id, application)
        tokens = await renew_authorization(
            target.oauth, actor.identity, actor.session_id, authorization_id, actor.request_id, port
        )
        return success(
            request,
            {
                "authorizationId": str(authorization_id),
                "status": "ACTIVE",
                "expiresAt": tokens.expires_at.isoformat(),
            },
        )
    except OAuthRejected:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "令牌续期未完成或授权已停用") from None
