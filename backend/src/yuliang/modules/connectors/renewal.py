"""驭量接管后按授权串行续期；失败先持久化降级，再向调用方拒绝。"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text

from yuliang.modules.audit.service import record
from yuliang.modules.connectors.base import TokenSet
from yuliang.modules.connectors.native_auth import NativeAuthorization
from yuliang.modules.connectors.oauth import OAuthRejected, OAuthService, require_admin
from yuliang.modules.connectors.token_cipher import Ciphertext, TokenContext
from yuliang.platform.db import VerifiedIdentity, business_transaction


async def renew_authorization(
    service: OAuthService,
    identity: VerifiedIdentity,
    session_id: UUID,
    authorization_id: UUID,
    request_id: UUID,
    port: NativeAuthorization,
) -> TokenSet:
    failed = False
    cancelled = False
    result: TokenSet | None = None
    with business_transaction(service.engine, identity) as session:
        require_admin(session, identity, session_id)
        # 事务锁跨进程；并发请求直接拒绝，不并行轮换同一refresh_token。
        if not session.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,760119))"),
            {"key": str(authorization_id)},
        ).scalar_one():
            raise OAuthRejected()
        row = (
            session.execute(
                text("SELECT * FROM provider_authorizations WHERE id=:id FOR UPDATE"),
                {"id": authorization_id},
            )
            .mappings()
            .first()
        )
        resource = port.resource
        if (
            row is None
            or row["status"] == "REVOKED"
            or (row["tenant_id"], row["application_id"], row["app_id"], row["connector_id"])
            != (resource.tenant_id, resource.id, resource.app_id, resource.connector_id)
        ):
            raise OAuthRejected()
        port.application()  # 仍需已完成唯一刷新者交接。
        context = TokenContext(
            row["tenant_id"],
            row["id"],
            row["application_id"],
            row["app_id"],
            row["connector_id"],
            row["provider"],
            row["product_line"],
        )
        refresh = (
            service.cipher.open(
                context, "refresh", Ciphertext(row["key_version"], row["refresh_token_enc"])
            )
            if row["refresh_token_enc"]
            else None
        )
        if row["status"] == "ACTIVE" and row["expires_at"] > datetime.now(UTC) + timedelta(
            seconds=300
        ):
            return TokenSet(
                access_token=service.cipher.open(
                    context, "access", Ciphertext(row["key_version"], row["access_token_enc"])
                ),
                refresh_token=refresh,
                expires_at=row["expires_at"],
                scopes=tuple(row["scopes"]),
            )
        try:
            renewed = await port.renew(refresh)
            result = renewed.model_copy(update={"scopes": renewed.scopes or tuple(row["scopes"])})
            access = service.cipher.seal(context, "access", result.access_token)
            new_refresh = (
                service.cipher.seal(context, "refresh", result.refresh_token)
                if result.refresh_token
                else None
            )
        except (Exception, asyncio.CancelledError) as error:
            failed, cancelled = True, isinstance(error, asyncio.CancelledError)
        applied = session.execute(
            text("""SELECT access.renew_provider_token(:member,:user,:session,:id,:application,
                :app,:connector,:version,:success,:access,:refresh,:key,:expires,:scopes)"""),
            {
                "member": identity.membership_id,
                "user": identity.user_id,
                "session": session_id,
                "id": authorization_id,
                "application": resource.id,
                "app": resource.app_id,
                "connector": resource.connector_id,
                "version": row["version"],
                "success": not failed,
                "access": access.value if not failed else None,
                "refresh": new_refresh.value if not failed and new_refresh else None,
                "key": access.key_version if not failed else None,
                "expires": result.expires_at if not failed and result else None,
                "scopes": list(result.scopes) if not failed and result else None,
            },
        ).scalar_one()
        if not applied:
            raise OAuthRejected()
        record(
            session,
            identity,
            {
                "action": "connection.token.rotate",
                "object_type": "authorization",
                "object_id": authorization_id,
                "request_id": request_id,
                "detail": {
                    "changed_fields": ["status"],
                    "result": "DENIED" if failed else "APPLIED",
                },
            },
        )
    if cancelled:
        raise asyncio.CancelledError()
    if failed or result is None:
        raise OAuthRejected()
    return result
