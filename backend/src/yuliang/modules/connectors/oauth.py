"""无Cookie回调使用原会话；换码在事务外，单次消费后不能自动重试。"""

import asyncio
import hashlib
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from yuliang.modules.audit.service import record
from yuliang.modules.capability.models import CONNECTOR_CONTEXTS
from yuliang.modules.connectors.base import TokenSet
from yuliang.modules.connectors.redaction import fingerprint
from yuliang.modules.connectors.token_cipher import Ciphertext, TokenCipher, TokenContext
from yuliang.platform.db import VerifiedIdentity, business_transaction


class OAuthRejected(Exception):
    def __init__(self) -> None:
        super().__init__("授权请求不可用，请重新由原管理员发起")


class CodeExchangeFailure(Exception):
    def __init__(self, *, unknown: bool = True) -> None:
        super().__init__("授权换码未完成")
        self.unknown = unknown


@dataclass(frozen=True)
class OAuthApplication:
    id: UUID
    app_id: str
    connector_id: str
    provider: str
    product_line: str
    redirect_uri: str

    def __post_init__(self) -> None:
        parsed = urlsplit(self.redirect_uri)
        if (
            self.connector_id not in {"adq", "wxpromo", "wxshop"}
            or CONNECTOR_CONTEXTS.get(self.connector_id) != (self.provider, self.product_line)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", self.app_id)
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.query
            or parsed.port not in (None, 443)
        ):
            raise ValueError("需要服务端核对的应用与固定HTTPS回调")

    @property
    def redirect_digest(self) -> str:
        return hashlib.sha256(self.redirect_uri.encode()).hexdigest()


@dataclass(frozen=True)
class OAuthStart:
    id: UUID
    authorization_id: UUID
    state: SecretStr = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class OAuthExchangeContext:
    identity: VerifiedIdentity
    session_id: UUID
    state_id: UUID
    authorization_id: UUID
    request_id: UUID
    application: OAuthApplication

    @property
    def token_context(self) -> TokenContext:
        app = self.application
        return TokenContext(
            self.identity.tenant_id,
            self.authorization_id,
            app.id,
            app.app_id,
            app.connector_id,
            app.provider,
            app.product_line,
        )


class OAuthExchangePort(Protocol):
    """服务端适配器实现固定出站/业务响应核验；本基件不配置真实平台。"""

    async def exchange(self, context: OAuthExchangeContext, code: SecretStr) -> TokenSet: ...


def require_admin(session: Session, identity: VerifiedIdentity, session_id: UUID) -> None:
    allowed = session.execute(
        text("SELECT access.oauth_actor(:tenant,:member,:user,:session)"),
        {
            "tenant": identity.tenant_id,
            "member": identity.membership_id,
            "user": identity.user_id,
            "session": session_id,
        },
    ).scalar_one()
    if not allowed:
        raise OAuthRejected()


class OAuthService:
    def __init__(self, engine: Engine, state_key: SecretStr, cipher: TokenCipher) -> None:
        fingerprint(state_key, "oauth-key-check", b"")
        self.engine, self._key, self.cipher = engine, state_key, cipher

    def start(
        self,
        identity: VerifiedIdentity,
        session_id: UUID,
        application: OAuthApplication,
        request_id: UUID,
        *,
        ttl_seconds: int = 300,
    ) -> OAuthStart:
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 600:
            raise ValueError("state有效期须30至600秒")
        state = SecretStr(secrets.token_urlsafe(48))
        state_id, authorization = uuid4(), uuid4()
        digest = fingerprint(self._key, "oauth-state-v1", state.get_secret_value().encode())
        with business_transaction(self.engine, identity) as session:
            require_admin(session, identity, session_id)
            expires = session.execute(
                text("""INSERT INTO oauth_states(id,tenant_id,user_id,
              membership_id,session_id,authorization_id,request_id,application_id,app_id,
              connector_id,provider,product_line,redirect_digest,state_digest,expires_at)
              VALUES(:id,:tenant,:user,:member,:session,:auth,:request,:application,:app,
              :connector,:provider,:product,:redirect,:digest,
              statement_timestamp()+make_interval(secs=>:ttl)) RETURNING expires_at"""),
                {
                    "id": state_id,
                    "tenant": identity.tenant_id,
                    "user": identity.user_id,
                    "member": identity.membership_id,
                    "session": session_id,
                    "auth": authorization,
                    "request": request_id,
                    "application": application.id,
                    "app": application.app_id,
                    "connector": application.connector_id,
                    "provider": application.provider,
                    "product": application.product_line,
                    "redirect": application.redirect_digest,
                    "digest": digest,
                    "ttl": ttl_seconds,
                },
            ).scalar_one()
            record(
                session,
                identity,
                {
                    "action": "connection.oauth.start",
                    "object_type": "oauth_state",
                    "object_id": state_id,
                    "request_id": request_id,
                },
            )
        return OAuthStart(state_id, authorization, state, expires)

    def _claim(self, state: SecretStr, application: OAuthApplication) -> OAuthExchangeContext:
        raw = state.get_secret_value()
        if not re.fullmatch(r"[A-Za-z0-9_-]{64}", raw):
            raise OAuthRejected()
        digest = fingerprint(self._key, "oauth-state-v1", raw.encode())
        # definer函数只接受不可枚举HMAC及固定应用上下文；此处没有其他设备Cookie。
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    text("""SELECT * FROM access.oauth_claim(:digest,:application,
                :app,:connector,:redirect)"""),
                    {
                        "digest": digest,
                        "application": application.id,
                        "app": application.app_id,
                        "connector": application.connector_id,
                        "redirect": application.redirect_digest,
                    },
                )
                .mappings()
                .first()
            )
        if row is None:
            raise OAuthRejected()
        return OAuthExchangeContext(
            VerifiedIdentity(row["tenant_id"], row["membership_id"], row["user_id"]),
            row["session_id"],
            row["id"],
            row["authorization_id"],
            row["request_id"],
            application,
        )

    @staticmethod
    def _event(
        session: Session, context: OAuthExchangeContext, phase: str, code_digest: str
    ) -> None:
        session.execute(
            text("""INSERT INTO authorization_exchange_events(tenant_id,state_id,
            request_id,phase,code_digest) VALUES(:tenant,:state,:request,:phase,:digest)"""),
            {
                "tenant": context.identity.tenant_id,
                "state": context.state_id,
                "request": context.request_id,
                "phase": phase,
                "digest": code_digest,
            },
        )

    def _failed(self, context: OAuthExchangeContext, phase: str, digest: str) -> bool:
        # 不记录供应商异常原文、code、state或token；数据库失败保留CLAIMED不自动恢复。
        try:
            with business_transaction(self.engine, context.identity) as session:
                changed = session.execute(
                    text("""UPDATE oauth_states SET state=:phase,
                    completed_at=clock_timestamp() WHERE id=:id AND state='CLAIMED'
                    RETURNING id"""),
                    {"id": context.state_id, "phase": phase},
                ).scalar_one_or_none()
                if changed:
                    self._event(session, context, phase, digest)
            return True
        except Exception:
            return False

    async def complete(
        self,
        state: SecretStr,
        code: SecretStr,
        application: OAuthApplication,
        exchange: OAuthExchangePort,
    ) -> dict[str, Any]:
        try:
            raw = code.get_secret_value().encode()
        except UnicodeError:
            raise OAuthRejected() from None
        if not 1 <= len(raw) <= 8192:
            raise OAuthRejected()
        context = self._claim(state, application)
        digest = fingerprint(self._key, "oauth-code-v1", raw)
        try:
            with business_transaction(self.engine, context.identity) as session:
                require_admin(session, context.identity, context.session_id)
                self._event(session, context, "STARTED", digest)
                record(
                    session,
                    context.identity,
                    {
                        "action": "connection.oauth.exchange",
                        "object_type": "authorization",
                        "object_id": context.authorization_id,
                        "request_id": context.request_id,
                    },
                )
            # 事务已提交。实际平台适配器需自身提供固定URL/业务核验，无自动换码重试。
            tokens = await asyncio.wait_for(exchange.exchange(context, code), timeout=30)
            if (
                not isinstance(tokens, TokenSet)
                or tokens.expires_at <= datetime.now(UTC)
                or (
                    len(tokens.scopes) > 256
                    or len(set(tokens.scopes)) != len(tokens.scopes)
                    or any(
                        not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", scope)
                        for scope in tokens.scopes
                    )
                )
            ):
                raise CodeExchangeFailure()
            access = self.cipher.seal(context.token_context, "access", tokens.access_token)
            refresh = (
                self.cipher.seal(context.token_context, "refresh", tokens.refresh_token)
                if tokens.refresh_token
                else None
            )
            with business_transaction(self.engine, context.identity) as session:
                require_admin(session, context.identity, context.session_id)
                valid = session.execute(
                    text("""SELECT id FROM oauth_states WHERE id=:id
                    AND state='CLAIMED' AND expires_at>clock_timestamp() FOR UPDATE"""),
                    {"id": context.state_id},
                ).scalar_one_or_none()
                if valid is None:
                    raise OAuthRejected()
                session.execute(
                    text("""INSERT INTO provider_authorizations(id,tenant_id,state_id,
                    authorized_by,application_id,app_id,connector_id,provider,product_line,
                    access_token_enc,refresh_token_enc,key_version,scopes,expires_at,status)
                    VALUES(:id,:tenant,:state,:user,:application,:app,:connector,:provider,:product,
                    :access,:refresh,:key,:scopes,:expires,'ACTIVE')"""),
                    {
                        "id": context.authorization_id,
                        "tenant": context.identity.tenant_id,
                        "state": context.state_id,
                        "user": context.identity.user_id,
                        "application": application.id,
                        "app": application.app_id,
                        "connector": application.connector_id,
                        "provider": application.provider,
                        "product": application.product_line,
                        "access": access.value,
                        "refresh": refresh.value if refresh else None,
                        "key": access.key_version,
                        "scopes": list(tokens.scopes),
                        "expires": tokens.expires_at,
                    },
                )
                self._event(session, context, "SUCCEEDED", digest)
                session.execute(
                    text("""UPDATE oauth_states SET state='SUCCEEDED',
                    completed_at=clock_timestamp() WHERE id=:id"""),
                    {"id": context.state_id},
                )
        except asyncio.CancelledError:
            self._failed(context, "UNKNOWN", digest)
            raise
        except Exception as error:
            self._failed(
                context,
                "FAILED"
                if isinstance(error, CodeExchangeFailure) and not error.unknown
                else "UNKNOWN",
                digest,
            )
            raise OAuthRejected() from None
        return {
            "authorizationId": str(context.authorization_id),
            "status": "ACTIVE",
            "provider": application.provider,
            "productLine": application.product_line,
            "expiresAt": tokens.expires_at.isoformat(),
        }

    def tokens(
        self, identity: VerifiedIdentity, session_id: UUID, authorization_id: UUID
    ) -> TokenSet:
        with business_transaction(self.engine, identity) as session:
            require_admin(session, identity, session_id)
            row = (
                session.execute(
                    text("""SELECT * FROM provider_authorizations
                WHERE id=:id AND status='ACTIVE' AND expires_at>clock_timestamp()"""),
                    {"id": authorization_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise OAuthRejected()
            context = TokenContext(
                row["tenant_id"],
                row["id"],
                row["application_id"],
                row["app_id"],
                row["connector_id"],
                row["provider"],
                row["product_line"],
            )
            return TokenSet(
                access_token=self.cipher.open(
                    context, "access", Ciphertext(row["key_version"], row["access_token_enc"])
                ),
                refresh_token=self.cipher.open(
                    context, "refresh", Ciphertext(row["key_version"], row["refresh_token_enc"])
                )
                if row["refresh_token_enc"]
                else None,
                expires_at=row["expires_at"],
                scopes=tuple(row["scopes"]),
            )

    def rotate(self, identity: VerifiedIdentity, session_id: UUID, authorization_id: UUID) -> None:
        with business_transaction(self.engine, identity) as session:
            require_admin(session, identity, session_id)
            row = (
                session.execute(
                    text("SELECT * FROM provider_authorizations WHERE id=:id FOR UPDATE"),
                    {"id": authorization_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise OAuthRejected()
            if row["key_version"] == self.cipher.current_version:
                return
            context = TokenContext(
                row["tenant_id"],
                row["id"],
                row["application_id"],
                row["app_id"],
                row["connector_id"],
                row["provider"],
                row["product_line"],
            )
            access = self.cipher.seal(
                context,
                "access",
                self.cipher.open(
                    context, "access", Ciphertext(row["key_version"], row["access_token_enc"])
                ),
            )
            refresh = (
                self.cipher.seal(
                    context,
                    "refresh",
                    self.cipher.open(
                        context, "refresh", Ciphertext(row["key_version"], row["refresh_token_enc"])
                    ),
                )
                if row["refresh_token_enc"]
                else None
            )
            session.execute(
                text("""UPDATE provider_authorizations SET access_token_enc=:access,
                refresh_token_enc=:refresh,key_version=:key,version=version+1 WHERE id=:id"""),
                {
                    "id": authorization_id,
                    "access": access.value,
                    "refresh": refresh.value if refresh else None,
                    "key": access.key_version,
                },
            )
            record(
                session,
                identity,
                {
                    "action": "connection.token.rotate",
                    "object_type": "authorization",
                    "object_id": authorization_id,
                    "request_id": uuid4(),
                },
            )
