"""进程启动配置：必填、显式校验、错误和repr隐藏凭据。"""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YULIANG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        hide_input_in_errors=True,
    )

    environment: Literal["local", "test", "dev", "staging", "prod"] = "local"
    database_url: SecretStr
    auth_database_url: SecretStr
    redis_url: SecretStr
    broker_url: SecretStr
    session_secret: SecretStr
    allowed_origins: list[str] = Field(default_factory=list)

    @field_validator("session_secret")
    @classmethod
    def secret_length(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("会话密钥至少32字符")
        return value

    @field_validator("database_url", "auth_database_url")
    @classmethod
    def postgres_url(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if (
            parsed.scheme != "postgresql+psycopg"
            or not parsed.hostname
            or not parsed.path.strip("/")
        ):
            raise ValueError("需要postgresql+psycopg数据库地址和库名")
        return value

    @field_validator("redis_url")
    @classmethod
    def redis_connection(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if parsed.scheme not in ("redis", "rediss") or not parsed.hostname:
            raise ValueError("需要Redis连接地址")
        return value

    @field_validator("broker_url")
    @classmethod
    def amqp_connection(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if parsed.scheme not in ("amqp", "amqps") or not parsed.hostname:
            raise ValueError("需要RabbitMQ AMQP地址")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def exact_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in origin
            ):
                raise ValueError("CORS必须是明确的scheme+host+port来源")
        return value
