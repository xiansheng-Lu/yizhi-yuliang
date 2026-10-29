"""进程启动配置：必填、显式校验、错误和repr隐藏凭据。"""

import re
from ipaddress import ip_network
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
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
    identity_api_enabled: bool = False
    auth_audit_secret: SecretStr | None = None
    email_login_enabled: bool = False
    email_code_secret: SecretStr | None = None
    email_daily_limit: int = Field(default=10, ge=1, le=100)
    admin_api_enabled: bool = False
    platform_resources_file: Path | None = None
    blob_enabled: bool = False
    blob_endpoint_url: str | None = None
    blob_bucket: str | None = None
    blob_access_key: SecretStr | None = None
    blob_secret_key: SecretStr | None = None
    blob_region: str = Field(default="us-east-1", pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    blob_upload_ttl_seconds: int = Field(default=300, ge=1, le=900)
    blob_download_ttl_seconds: int = Field(default=60, ge=1, le=300)
    report_public_origin: str | None = None
    report_link_secret: SecretStr | None = None
    report_public_ttl_seconds: int = Field(default=2592000, ge=60, le=31536000)
    report_public_reads_per_minute: int = Field(default=60, ge=1, le=600)
    admin_allowed_networks: list[str] = Field(default_factory=lambda: ["127.0.0.1/32", "::1/128"])
    worker_queue_prefix: str = Field(default="yuliang", pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    operations_allowed_networks: list[str] = Field(
        default_factory=lambda: ["127.0.0.1/32", "::1/128"]
    )

    @model_validator(mode="after")
    def auth_rollout_requirements(self) -> "Settings":
        if self.report_public_origin is not None or self.report_link_secret is not None:
            if (
                not self.identity_api_enabled
                or not self.report_public_origin
                or not self.report_link_secret
            ):
                raise ValueError("报告发布需要身份接口、明确公开源与独立链接密钥")
            origin = urlsplit(self.report_public_origin)
            if (
                origin.scheme not in {"http", "https"}
                or not origin.hostname
                or origin.username
                or origin.password
                or origin.path
                or origin.query
                or origin.fragment
                or "*" in self.report_public_origin
                or re.fullmatch(r"https?://[^/?#@\s]+", self.report_public_origin) is None
                or origin.scheme == "http"
                and (
                    self.environment not in {"local", "test"}
                    or origin.hostname not in {"127.0.0.1", "::1", "localhost", "testserver"}
                )
                or len(self.report_link_secret.get_secret_value().strip()) < 32
                or self.report_link_secret
                in (self.session_secret, self.auth_audit_secret, self.email_code_secret)
            ):
                raise ValueError("报告公开源需精确HTTPS源及独立密钥；HTTP仅供本地测试")
        if self.blob_enabled:
            if not (
                self.identity_api_enabled
                and self.blob_endpoint_url
                and self.blob_bucket
                and self.blob_access_key
                and self.blob_access_key.get_secret_value().strip()
                and self.blob_secret_key
                and self.blob_secret_key.get_secret_value().strip()
            ):
                raise ValueError("私有素材需要身份接口、明确存储地址、私有桶及独立存储凭据")
            endpoint = urlsplit(self.blob_endpoint_url)
            if (
                endpoint.scheme not in {"http", "https"}
                or not endpoint.hostname
                or endpoint.username
                or endpoint.password
                or endpoint.query
                or endpoint.fragment
                or endpoint.path not in {"", "/"}
                or endpoint.scheme == "http"
                and (
                    self.environment not in {"local", "test"}
                    or endpoint.hostname not in {"127.0.0.1", "::1", "localhost"}
                )
                or re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", self.blob_bucket) is None
            ):
                raise ValueError("私有存储需固定源与合法桶名；明文仅允许本地测试回环")
        if self.admin_api_enabled and (
            not self.identity_api_enabled or not self.admin_allowed_networks
        ):
            raise ValueError("管理入口需要身份接口与明确来源网段")
        if (
            self.platform_resources_file is not None
            and not self.identity_api_enabled
            and self.environment not in {"test", "local"}
        ):
            raise ValueError("平台接入需要真实身份与会话接口")
        for network in self.admin_allowed_networks + self.operations_allowed_networks:
            ip_network(network)
        if self.identity_api_enabled:
            if not self.auth_audit_secret or len(self.auth_audit_secret.get_secret_value()) < 32:
                raise ValueError("开放身份接口需要独立的至少32字符认证审计密钥")
            if self.auth_audit_secret == self.session_secret or not self.allowed_origins:
                raise ValueError("认证审计密钥需独立且需要精确来源白名单")
        if self.email_login_enabled:
            if not self.identity_api_enabled or not self.email_code_secret:
                raise ValueError("邮件登录需要身份接口与独立邮件验证码密钥")
            if len(self.email_code_secret.get_secret_value()) < 32:
                raise ValueError("邮件验证码密钥至少32字符")
            if self.email_code_secret in (self.session_secret, self.auth_audit_secret):
                raise ValueError("邮件验证码密钥须独立于会话与审计密钥")
        return self

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
