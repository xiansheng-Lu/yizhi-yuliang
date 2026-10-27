"""可信认证子系统：受限函数、Argon2id、持久节流、数据库会话与独立审计摘要。"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import Any

import redis
import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import Connection, create_engine, text

from yuliang.infrastructure.mail import MailSender, SMTPMailer, SMTPSettings
from yuliang.modules.access.catalog import workspace_pages
from yuliang.platform.db import VerifiedIdentity, business_transaction
from yuliang.platform.errors import ApiError, ErrorCode
from yuliang.settings import Settings

HASHER = PasswordHasher()
DUMMY_HASH = HASHER.hash("unusable-dummy-verification-input")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class IdentityService:
    def __init__(self, settings: Settings, mail_sender: MailSender | None = None) -> None:
        self.auth = create_engine(
            settings.auth_database_url.get_secret_value(), hide_parameters=True
        )
        self.app = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
        if settings.auth_audit_secret is None:
            raise ValueError("缺少独立认证审计密钥")
        self.audit_key = settings.auth_audit_secret.get_secret_value().encode()
        self.mail: MailSender | None = None
        self.email_pepper: bytes | None = None
        self.email_daily_limit = settings.email_daily_limit
        if settings.email_login_enabled:
            if settings.email_code_secret is None:
                raise ValueError("邮件验证码密钥未配置")
            self.email_pepper = settings.email_code_secret.get_secret_value().encode()
            self.mail = mail_sender if mail_sender is not None else SMTPMailer(SMTPSettings())
        self.cache = redis.Redis.from_url(
            settings.redis_url.get_secret_value(), socket_connect_timeout=0.2, socket_timeout=0.2
        )

    def close(self) -> None:
        self.auth.dispose()
        self.app.dispose()
        self.cache.close()

    def audit_digest(self, kind: str, value: str) -> str:
        return hmac.new(self.audit_key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()

    def context(self, connection: Connection, email: str, ip: str, request_id: str) -> None:
        for key, value in {
            "app.email_digest": self.audit_digest("email", email),
            "app.ip_digest": self.audit_digest("ip", ip),
            "app.request_id": request_id,
        }.items():
            connection.execute(
                text("SELECT set_config(:key,:value,true)"), {"key": key, "value": value}
            )

    def row(self, token: str | None) -> dict[str, Any] | None:
        if not token or len(token) > 128:
            return None
        with self.auth.begin() as connection:
            row = (
                connection.execute(
                    text("SELECT * FROM auth.resolve_session(:token)"), {"token": digest(token)}
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def identity(self, row: dict[str, Any]) -> dict[str, Any]:
        verified = VerifiedIdentity(row["tenant_id"], row["membership_id"], row["user_id"])
        with business_transaction(self.app, verified) as session:
            permissions = list(
                session.execute(
                    text("""
              SELECT DISTINCT p.permission_code FROM role_grants g JOIN role_permissions p
              ON (p.tenant_id,p.role_id)=(g.tenant_id,g.role_id)
              WHERE g.membership_id=:member ORDER BY p.permission_code
            """),
                    {"member": verified.membership_id},
                ).scalars()
            )
            shops = list(
                session.execute(
                    text("""
              SELECT DISTINCT shop.id FROM shops shop WHERE shop.deleted_at IS NULL AND EXISTS (
                SELECT 1 FROM data_scopes scope WHERE scope.membership_id=:member
                AND (scope.permissions_mask IS NULL
                  OR scope.permissions_mask && CAST(:permissions AS text[]))
                AND (scope.scope_type='ALL_TENANT' OR scope.shop_id=shop.id
                  OR scope.entity_id=shop.entity_id OR EXISTS (
                    SELECT 1 FROM entity_links link WHERE link.shop_id=shop.id
                      AND link.account_id=scope.account_id))
              ) ORDER BY shop.id
            """),
                    {"member": verified.membership_id, "permissions": permissions},
                ).scalars()
            )
        return {
            "userId": str(row["user_id"]),
            "displayName": row["display_name"],
            "email": row["email"],
            "tenantId": str(row["tenant_id"]),
            "shopIds": [str(shop) for shop in shops],
            "permissions": permissions,
            "workspacePages": workspace_pages(set(permissions)),
            "mode": "API",
        }

    def csrf(self, token: str | None) -> tuple[str, datetime, str | None]:
        row = self.row(token)
        csrf = secrets.token_urlsafe(32)
        with self.auth.begin() as connection:
            if row and token:
                changed = connection.execute(
                    text("SELECT auth.rotate_csrf(:token,:csrf,:version)"),
                    {
                        "token": digest(token),
                        "csrf": digest(csrf),
                        "version": row["session_version"],
                    },
                ).scalar_one()
                if not changed:
                    raise ApiError(ErrorCode.VERSION_CONFLICT, "会话已变化，请重新获取防护令牌")
                return csrf, row["expires_at"], None
            preauth = secrets.token_urlsafe(32)
            expires = connection.execute(
                text("SELECT auth.issue_preauth(:cookie,:csrf)"),
                {"cookie": digest(preauth), "csrf": digest(csrf)},
            ).scalar_one()
            return csrf, expires, preauth

    def require_csrf(self, token: str, csrf: str) -> None:
        with self.auth.begin() as connection:
            valid = connection.execute(
                text("SELECT auth.check_csrf(:token,:csrf)"),
                {"token": digest(token), "csrf": digest(csrf)},
            ).scalar_one()
        if not valid:
            raise ApiError(ErrorCode.FORBIDDEN, "防护令牌失效，请重新获取；不要自动重放原请求")

    def login(
        self, email: str, password: str, preauth: str, csrf: str, ip: str, request_id: str
    ) -> tuple[dict[str, Any], str, int]:
        email_hash, ip_hash = self.audit_digest("email", email), self.audit_digest("ip", ip)
        with self.auth.begin() as connection:
            challenge = (
                connection.execute(
                    text("SELECT * FROM auth.resolve_preauth(:cookie)"), {"cookie": digest(preauth)}
                )
                .mappings()
                .first()
            )
            if not challenge or not secrets.compare_digest(
                challenge["csrf_token_hash"], digest(csrf)
            ):
                raise ApiError(ErrorCode.FORBIDDEN, "登录防护令牌无效，请重新获取")
            retries = [
                connection.execute(
                    text("SELECT auth.rate_limit(:key,'PASSWORD')"), {"key": key}
                ).scalar_one()
                for key in (email_hash, ip_hash)
            ]
            retry = max(retries)
            credentials = (
                None
                if retry
                else connection.execute(
                    text("SELECT * FROM auth.credentials(:email)"), {"email": email}
                )
                .mappings()
                .first()
            )
            if retry:
                connection.execute(
                    text("SELECT auth.record_event(:email,'PASSWORD_RATE_LIMITED',:request,:ip)"),
                    {"email": email_hash, "request": request_id, "ip": ip_hash},
                )
        if retry:
            raise ApiError(
                ErrorCode.RATE_LIMITED, "登录尝试过于频繁，请稍后重试", retry_after=retry
            )
        valid: bool
        try:
            valid = HASHER.verify(
                credentials["password_hash"]
                if credentials and credentials["password_hash"]
                else DUMMY_HASH,
                password,
            )
        except (VerificationError, InvalidHashError):
            valid = False
        if not valid or not credentials or not credentials["password_hash"]:
            with self.auth.begin() as connection:
                connection.execute(
                    text("SELECT auth.record_event(:email,'PASSWORD_REJECTED',:request,:ip)"),
                    {"email": email_hash, "request": request_id, "ip": ip_hash},
                )
            raise ApiError(ErrorCode.UNAUTHENTICATED, "邮箱或密码不正确")
        token, session_csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.auth.begin() as connection:
            member = (
                connection.execute(
                    text("SELECT * FROM auth.memberships(:user)"), {"user": credentials["user_id"]}
                )
                .mappings()
                .first()
            )
            if not member:
                raise ApiError(ErrorCode.UNAUTHENTICATED, "邮箱或密码不正确")
            self.context(connection, email, ip, request_id)
            for key, value in {
                "app.preauth_hash": digest(preauth),
                "app.preauth_csrf_hash": digest(csrf),
            }.items():
                connection.execute(
                    text("SELECT set_config(:key,:value,true)"), {"key": key, "value": value}
                )
            created = connection.execute(
                text("SELECT auth.create_session(:user,:member,:token,:csrf)"),
                {
                    "user": credentials["user_id"],
                    "member": member["membership_id"],
                    "token": digest(token),
                    "csrf": digest(session_csrf),
                },
            ).scalar_one()
            if not created:
                raise ApiError(ErrorCode.FORBIDDEN, "登录状态已变化，请重新获取防护令牌")
        row = self.row(token)
        if not row:
            raise ApiError(ErrorCode.UNAUTHENTICATED, "会话不可用")
        lifetime = int((row["absolute_expires_at"] - datetime.now(UTC)).total_seconds())
        return self.identity(row), token, max(1, lifetime)

    def session(self, token: str | None) -> dict[str, Any] | None:
        row = self.row(token)
        if not row or not token:
            return None
        with self.auth.begin() as connection:
            connection.execute(
                text("SELECT auth.renew_session(:token,:version)"),
                {"token": digest(token), "version": row["session_version"]},
            )
        current = self.row(token)
        return self.identity(current) if current else None

    def logout(self, token: str, csrf: str, ip: str, request_id: str) -> None:
        row = self.row(token)
        if not row:
            return
        self.require_csrf(token, csrf)
        with self.auth.begin() as connection:
            self.context(connection, row["email"], ip, request_id)
            connection.execute(text("SELECT auth.revoke_session(:token)"), {"token": digest(token)})
        try:
            self.cache.delete("session:" + digest(token))
        except redis.RedisError:
            structlog.get_logger().warning(
                "session_cache_invalidation_failed", request_id=request_id
            )
