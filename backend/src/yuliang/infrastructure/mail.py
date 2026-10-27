"""SMTP只走经过验证的TLS，凭据/验证码不输出，不以SMTP接受冒充实际收件。"""

import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SMTPSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YULIANG_SMTP_",
        env_file="deployment/.env.smtp.local",
        extra="forbid",
        hide_input_in_errors=True,
    )
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    security: Literal["ssl", "starttls"]
    username: SecretStr
    password: SecretStr
    sender: str = Field(validation_alias="YULIANG_SMTP_FROM", repr=False)
    test_to: str | None = Field(default=None, repr=False)

    @field_validator("sender", "test_to")
    @classmethod
    def valid_address(cls, value: str | None) -> str | None:
        import re

        if value is not None and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("邮件地址不合法")
        return value


class MailUnavailable(Exception):
    pass


class MailSender(Protocol):
    def send_code(
        self, email: str, code: SecretStr, challenge_id: UUID, expires_at: datetime
    ) -> None: ...


class SMTPMailer:
    def __init__(self, settings: SMTPSettings) -> None:
        self.settings = settings

    def send_code(
        self, email: str, code: SecretStr, challenge_id: UUID, expires_at: datetime
    ) -> None:
        message = EmailMessage()
        message["Subject"] = "易致·驭量 登录验证码"
        message["From"] = formataddr(("易致·驭量", self.settings.sender))
        message["To"] = email
        message["Date"] = format_datetime(datetime.now(UTC))
        message["Message-ID"] = make_msgid(
            idstring=challenge_id.hex, domain=self.settings.sender.split("@", 1)[1]
        )
        message.set_content(
            "您的登录验证码为："
            + code.get_secret_value()
            + "\n验证码5分钟内有效，且仅能使用一次。请勿转发给他人。\n如非本人操作，请忽略本邮件。"
        )
        connection: smtplib.SMTP | None = None
        try:
            context = ssl.create_default_context()
            if self.settings.security == "ssl":
                connection = smtplib.SMTP_SSL(
                    self.settings.host, self.settings.port, timeout=12, context=context
                )
            else:
                connection = smtplib.SMTP(self.settings.host, self.settings.port, timeout=12)
                connection.ehlo()
                connection.starttls(context=context)
                connection.ehlo()
            connection.login(
                self.settings.username.get_secret_value(), self.settings.password.get_secret_value()
            )
            refused = connection.send_message(
                message, from_addr=self.settings.sender, to_addrs=[email]
            )
            if refused:
                raise MailUnavailable("邮件服务未接受收件地址")
        except Exception:
            raise MailUnavailable("邮件服务暂不可用") from None
        finally:
            if connection is not None:
                # DATA已确认后的QUIT故障不能改写投递接受事实；不打印服务商异常。
                try:
                    connection.quit()
                except Exception:
                    connection.close()
