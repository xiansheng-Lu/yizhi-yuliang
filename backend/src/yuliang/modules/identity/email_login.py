"""邮件码独立HMAC、绑定预认证、持久节流与原子消费；SMTP不跨数据库事务。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from yuliang.infrastructure.mail import MailUnavailable
from yuliang.modules.identity.service import digest
from yuliang.platform.errors import ApiError, ErrorCode

if TYPE_CHECKING:
    from yuliang.modules.identity.service import IdentityService


def enabled(instance: IdentityService) -> None:
    if instance.mail is None or instance.email_pepper is None:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "邮件验证码登录尚未开启")


def code_digest(instance: IdentityService, challenge_id: UUID, code: SecretStr) -> str:
    if instance.email_pepper is None:
        raise ValueError("邮件验证码密钥未配置")
    return hmac.new(
        instance.email_pepper,
        f"{challenge_id}|LOGIN|{code.get_secret_value()}".encode(),
        hashlib.sha256,
    ).hexdigest()


def context(
    instance: IdentityService,
    connection: Connection,
    email: str,
    ip: str,
    request_id: str,
    preauth: str,
    csrf: str,
) -> None:
    instance.context(connection, email, ip, request_id)
    for key, value in {
        "app.preauth_hash": digest(preauth),
        "app.preauth_csrf_hash": digest(csrf),
        "app.login_email": email,
        "app.email_daily_limit": str(instance.email_daily_limit),
    }.items():
        connection.execute(
            text("SELECT set_config(:key,:value,true)"), {"key": key, "value": value}
        )


def require_preauth(connection: Connection, preauth: str, csrf: str) -> None:
    row = (
        connection.execute(
            text("SELECT * FROM auth.resolve_preauth(:cookie)"), {"cookie": digest(preauth)}
        )
        .mappings()
        .first()
    )
    if not row or not secrets.compare_digest(row["csrf_token_hash"], digest(csrf)):
        raise ApiError(ErrorCode.FORBIDDEN, "登录防护令牌无效，请重新获取")


def request_code(
    instance: IdentityService, email: str, preauth: str, csrf: str, ip: str, request_id: str
) -> dict[str, Any]:
    enabled(instance)
    challenge_id = uuid4()
    code = SecretStr(f"{secrets.randbelow(1000000):06d}")
    try:
        with instance.auth.begin() as connection:
            require_preauth(connection, preauth, csrf)
            context(instance, connection, email, ip, request_id, preauth, csrf)
            # 服务端先分配ID以计算challengeId|purpose|code HMAC，HTTP不接受这个GUC。
            connection.execute(
                text("SELECT set_config('app.email_challenge_id',:id,true)"),
                {"id": str(challenge_id)},
            )
            result = (
                connection.execute(
                    text("SELECT * FROM auth.issue_challenge(:email,:hash,:preauth,:csrf)"),
                    {
                        "email": email,
                        "hash": code_digest(instance, challenge_id, code),
                        "preauth": digest(preauth),
                        "csrf": digest(csrf),
                    },
                )
                .mappings()
                .one()
            )
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) == "42501":
            raise ApiError(ErrorCode.FORBIDDEN, "登录防护令牌已变化") from None
        raise
    if result["challenge_id"] is None:
        raise ApiError(
            ErrorCode.RATE_LIMITED, "发送过于频繁，请稍后重试", retry_after=result["retry_after"]
        )
    accepted = True
    try:
        if instance.mail is None:
            raise MailUnavailable("邮件发送器未配置")
        instance.mail.send_code(email, code, challenge_id, result["expires_at"])
    except Exception:
        accepted = False
    with instance.auth.begin() as connection:
        context(instance, connection, email, ip, request_id, preauth, csrf)
        changed = connection.execute(
            text("SELECT auth.email_delivery(:id,:accepted)"),
            {"id": challenge_id, "accepted": accepted},
        ).scalar_one()
        if not accepted:
            connection.execute(
                text("SELECT auth.record_event(:email,'EMAIL_DELIVERY_FAILED',:request,:ip)"),
                {
                    "email": instance.audit_digest("email", email),
                    "request": request_id,
                    "ip": instance.audit_digest("ip", ip),
                },
            )
    if not accepted or not changed:
        raise ApiError(ErrorCode.UPSTREAM_ERROR, "邮件服务暂不可用，请稍后重试")
    return {
        "challengeId": str(challenge_id),
        "expiresAt": result["expires_at"].isoformat(),
        "retryAfterSeconds": result["retry_after"],
    }


def login_code(
    instance: IdentityService,
    email: str,
    challenge_id: UUID,
    code: SecretStr,
    preauth: str,
    csrf: str,
    ip: str,
    request_id: str,
) -> tuple[dict[str, Any], str, int]:
    enabled(instance)
    with instance.auth.begin() as connection:
        require_preauth(connection, preauth, csrf)
        context(instance, connection, email, ip, request_id, preauth, csrf)
        retry = max(
            connection.execute(
                text("SELECT auth.rate_limit(:key,'EMAIL_VERIFY')"), {"key": key}
            ).scalar_one()
            for key in (instance.audit_digest("email", email), instance.audit_digest("ip", ip))
        )
        if retry:
            connection.execute(
                text("SELECT auth.record_event(:email,'EMAIL_RATE_LIMITED',:request,:ip)"),
                {
                    "email": instance.audit_digest("email", email),
                    "request": request_id,
                    "ip": instance.audit_digest("ip", ip),
                },
            )
    if retry:
        raise ApiError(ErrorCode.RATE_LIMITED, "验证码尝试过于频繁，请稍后重试", retry_after=retry)
    token, session_csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with instance.auth.begin() as connection:
        context(instance, connection, email, ip, request_id, preauth, csrf)
        # NULL成员候选由函数在邮件码验证后从真实邮箱成员中选择；不在验证前暴露成员关系。
        created = connection.execute(
            text("SELECT auth.email_session(:challenge,:hash,NULL,:token,:csrf)"),
            {
                "challenge": challenge_id,
                "hash": code_digest(instance, challenge_id, code),
                "token": digest(token),
                "csrf": digest(session_csrf),
            },
        ).scalar_one()
        if not created:
            connection.execute(
                text("SELECT auth.record_event(:email,'EMAIL_REJECTED',:request,:ip)"),
                {
                    "email": instance.audit_digest("email", email),
                    "request": request_id,
                    "ip": instance.audit_digest("ip", ip),
                },
            )
    # 错误/尝试计数已提交，再返回错误；不可用异常回滚掉防爆破事实。
    if not created:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "邮箱或验证码不正确，或验证码已失效")
    row = instance.row(token)
    if not row:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "会话不可用")
    lifetime = int((row["absolute_expires_at"] - datetime.now(UTC)).total_seconds())
    return instance.identity(row), token, max(1, lifetime)
