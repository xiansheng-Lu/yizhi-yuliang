import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from yuliang.infrastructure.mail import MailUnavailable, SMTPMailer, SMTPSettings
from yuliang.modules.identity.routes import EmailCodeLogin, EmailCodeRequest
from yuliang.settings import Settings


def smtp_settings(**changes: Any) -> SMTPSettings:
    values: dict[str, Any] = {
        "host": "smtp.example.invalid",
        "port": 465,
        "security": "ssl",
        "username": "private-login-identity",
        "password": "private-smtp-credential",
        "YULIANG_SMTP_FROM": "sender@example.invalid",
    }
    return SMTPSettings(_env_file=None, **(values | changes))


def test_mail_settings_and_login_code_do_not_reflect_secrets() -> None:
    settings = smtp_settings()
    assert "private-login" not in repr(settings) and "private-smtp" not in repr(settings)
    assert EmailCodeRequest(email=" Test@Example.Invalid ").email == "test@example.invalid"
    with pytest.raises(ValidationError) as error:
        EmailCodeLogin(
            email="test@example.invalid", challengeId=uuid4(), code="12345x", method="email_code"
        )
    assert "12345x" not in str(error.value)
    with pytest.raises(ValidationError):
        smtp_settings(security="plain")


def test_email_pepper_must_be_independent(settings: Settings) -> None:
    values = settings.model_dump() | {
        "identity_api_enabled": True,
        "auth_audit_secret": SecretStr("a" * 32),
        "allowed_origins": ["http://testserver"],
        "email_login_enabled": True,
    }
    for value in (None, SecretStr("short"), settings.session_secret, SecretStr("a" * 32)):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **(values | {"email_code_secret": value}))


class FakeSMTP:
    def __init__(self, *, reject: bool = False, quit_error: bool = False) -> None:
        self.events: list[str] = []
        self.reject = reject
        self.quit_error = quit_error

    def ehlo(self) -> None:
        self.events.append("EHLO")

    def starttls(self, *, context: ssl.SSLContext) -> None:
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        self.events.append("STARTTLS")

    def login(self, username: str, password: str) -> None:
        self.events.append("LOGIN")
        if self.reject:
            raise smtplib.SMTPAuthenticationError(535, b"private-provider-response")

    def send_message(
        self, message: EmailMessage, *, from_addr: str, to_addrs: list[str]
    ) -> dict[str, tuple[int, bytes]]:
        assert from_addr == "sender@example.invalid" and to_addrs == ["receiver@example.invalid"]
        assert "123456" in message.get_content()
        self.events.append("DATA")
        return {}

    def quit(self) -> None:
        self.events.append("QUIT")
        if self.quit_error:
            raise smtplib.SMTPServerDisconnected("private-provider-response")

    def close(self) -> None:
        self.events.append("CLOSE")


def test_ssl_certificate_verification_and_accepted_quit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSMTP(quit_error=True)

    def factory(host: str, port: int, *, timeout: int, context: ssl.SSLContext) -> FakeSMTP:
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        fake.events.append("SSL")
        return fake

    monkeypatch.setattr(smtplib, "SMTP_SSL", factory)
    SMTPMailer(smtp_settings()).send_code(
        "receiver@example.invalid", SecretStr("123456"), uuid4(), datetime.now(UTC)
    )
    assert fake.events == ["SSL", "LOGIN", "DATA", "QUIT", "CLOSE"]


def test_starttls_precedes_authentication_and_errors_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSMTP(reject=True)

    def factory(host: str, port: int, *, timeout: int) -> FakeSMTP:
        return fake

    monkeypatch.setattr(smtplib, "SMTP", factory)
    with pytest.raises(MailUnavailable) as error:
        SMTPMailer(smtp_settings(security="starttls", port=587)).send_code(
            "receiver@example.invalid", SecretStr("123456"), uuid4(), datetime.now(UTC)
        )
    assert fake.events == ["EHLO", "STARTTLS", "EHLO", "LOGIN", "QUIT"]
    assert "private-provider" not in str(error.value) and error.value.__cause__ is None
