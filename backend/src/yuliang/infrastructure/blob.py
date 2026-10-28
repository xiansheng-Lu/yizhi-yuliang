"""显式私有S3/MinIO边界；固定服务端key、短签名、精确版本和实际SHA256。"""

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from pydantic import SecretStr

from yuliang.infrastructure.media_probe import MediaMismatch, validate_media
from yuliang.settings import Settings


class BlobError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__("私有对象存储校验未通过")
        self.reason = reason


class S3Client(Protocol):
    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_bucket_policy(self, **kwargs: Any) -> dict[str, Any]: ...
    def generate_presigned_url(self, *args: Any, **kwargs: Any) -> str: ...
    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def close(self) -> None: ...


_private: ContextVar[bool] = ContextVar("private_blob_logs", default=False)


class PrivateBlobLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _private.get() and record.name.startswith(
            ("botocore", "boto3", "s3transfer", "urllib3", "httpx", "httpcore")
        ):
            record.msg = "私有对象存储库事件（请求细节已省略）"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


@contextmanager
def private_blob_logs() -> Iterator[None]:
    token = _private.set(True)
    guard = PrivateBlobLogs()
    handlers = set(logging.getLogger().handlers)
    for name, logger in logging.root.manager.loggerDict.copy().items():
        if name.startswith(
            ("botocore", "boto3", "s3transfer", "urllib3", "httpx", "httpcore")
        ) and isinstance(logger, logging.Logger):
            handlers.update(logger.handlers)
    if logging.lastResort:
        handlers.add(logging.lastResort)
    for handler in handlers:
        handler.addFilter(guard)
    try:
        yield
    finally:
        for handler in handlers:
            handler.removeFilter(guard)
        _private.reset(token)


@dataclass(frozen=True)
class VerifiedBlob:
    version_id: str
    size: int
    mime: str
    sha256: str


@dataclass(frozen=True)
class VersionCheck:
    checked_at: datetime
    token: str = field(repr=False)
    context: tuple[str, str, int, str, str, UUID] | None = field(default=None, repr=False)


class BlobStore:
    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access: SecretStr,
        secret: SecretStr,
        *,
        region: str = "us-east-1",
        local: bool = False,
    ) -> None:
        url = urlsplit(endpoint)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or url.scheme == "http"
            and (not local or url.hostname not in {"127.0.0.1", "localhost", "::1"})
            or re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket) is None
        ):
            raise ValueError("对象存储须为显式可信私有端点和合法桶")
        self.bucket = bucket
        self._attest_key = secrets.token_bytes(32)
        with private_blob_logs():
            self.client = cast(
                S3Client,
                boto3.client(
                    "s3",
                    endpoint_url=endpoint,
                    aws_access_key_id=access.get_secret_value(),
                    aws_secret_access_key=secret.get_secret_value(),
                    region_name=region,
                    config=Config(
                        signature_version="s3v4",
                        s3={"addressing_style": "path"},
                        connect_timeout=5,
                        read_timeout=10,
                        retries={"max_attempts": 0},
                        proxies={},
                    ),
                ),
            )

    @classmethod
    def from_settings(cls, settings: Settings) -> "BlobStore|None":
        if not settings.blob_enabled:
            return None
        if (
            not settings.blob_endpoint_url
            or not settings.blob_bucket
            or not settings.blob_access_key
            or not settings.blob_secret_key
        ):
            raise ValueError("对象存储配置缺失")
        return cls(
            settings.blob_endpoint_url,
            settings.blob_bucket,
            settings.blob_access_key,
            settings.blob_secret_key,
            region=settings.blob_region,
            local=settings.environment in {"local", "test"},
        )

    @staticmethod
    def key(tenant: int, shop: UUID, upload: UUID) -> str:
        if type(tenant) is not int or tenant <= 0:
            raise ValueError("租户无效")
        return f"tenants/{tenant}/shops/{shop}/uploads/{upload}"

    @staticmethod
    def _key(key: str) -> None:
        if (
            re.fullmatch(r"tenants/[1-9][0-9]*/shops/[0-9a-f-]{36}/uploads/[0-9a-f-]{36}", key)
            is None
        ):
            raise BlobError("INVALID_KEY")

    def check_bucket(self) -> None:
        with private_blob_logs():
            try:
                if self.client.get_bucket_versioning(Bucket=self.bucket).get("Status") != "Enabled":
                    raise BlobError("VERSIONING_REQUIRED")
                try:
                    policy = self.client.get_bucket_policy(Bucket=self.bucket)
                except ClientError as exc:
                    if exc.response.get("Error", {}).get("Code") != "NoSuchBucketPolicy":
                        raise
                else:
                    if policy.get("Policy"):
                        raise BlobError("BUCKET_POLICY_REQUIRES_PRIVATE_REVIEW")
            except (ClientError, BotoCoreError):
                raise BlobError("STORAGE_UNAVAILABLE") from None

    def upload_url(
        self, key: str, size: int, mime: str, sha256: str, upload: UUID, *, expires: int
    ) -> dict[str, Any]:
        self._key(key)
        if (
            type(expires) is not int
            or not 1 <= expires <= 900
            or type(size) is not int
            or not 0 < size <= 104857600
        ):
            raise ValueError("上传签名边界非法")
        if re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise ValueError("校验和非法")
        self.check_bucket()
        checksum = base64.b64encode(bytes.fromhex(sha256)).decode("ascii")
        with private_blob_logs():
            try:
                url = self.client.generate_presigned_url(
                    "put_object",
                    Params=dict(
                        Bucket=self.bucket,
                        Key=key,
                        ContentType=mime,
                        ContentLength=size,
                        ChecksumSHA256=checksum,
                        Metadata={"upload-id": str(upload)},
                    ),
                    ExpiresIn=expires,
                    HttpMethod="PUT",
                )
            except (ClientError, BotoCoreError):
                raise BlobError("SIGN_UNAVAILABLE") from None
        return dict(
            method="PUT",
            url=url,
            headers={
                "Content-Type": mime,
                "Content-Length": str(size),
                "x-amz-checksum-sha256": checksum,
                "x-amz-meta-upload-id": str(upload),
            },
        )

    def verify(self, key: str, size: int, mime: str, sha256: str, upload: UUID) -> VerifiedBlob:
        self._key(key)
        self.check_bucket()
        with private_blob_logs():
            try:
                head = self.client.head_object(Bucket=self.bucket, Key=key, ChecksumMode="ENABLED")
                version = head.get("VersionId")
                if not isinstance(version, str) or version in {"", "null"}:
                    raise BlobError("VERSION_REQUIRED")
                if (
                    head.get("ContentLength") != size
                    or head.get("ContentType") != mime
                    or head.get("Metadata", {}).get("upload-id") != str(upload)
                ):
                    raise BlobError("OBJECT_METADATA_MISMATCH")
                actual = self.client.get_object(Bucket=self.bucket, Key=key, VersionId=version)
                if actual.get("ContentLength") != size or actual.get("ContentType") != mime:
                    raise BlobError("OBJECT_METADATA_MISMATCH")
                body = actual["Body"]
                digest = hashlib.sha256()
                total = 0
                with tempfile.TemporaryFile(mode="w+b") as file:
                    try:
                        while block := body.read(min(1048576, size + 1 - total)):
                            total += len(block)
                            if total > size:
                                raise BlobError("OBJECT_SIZE_MISMATCH")
                            digest.update(block)
                            file.write(block)
                    finally:
                        body.close()
                    if total != size or digest.hexdigest() != sha256:
                        raise BlobError("OBJECT_CHECKSUM_MISMATCH")
                    try:
                        validate_media(file, mime, size)
                    except MediaMismatch:
                        raise BlobError("OBJECT_TYPE_MISMATCH") from None
                return VerifiedBlob(version, size, mime, sha256)
            except (ClientError, BotoCoreError):
                raise BlobError("OBJECT_UNAVAILABLE") from None

    def check_version(
        self, key: str, version: str, size: int, mime: str, sha256: str, asset: UUID
    ) -> VersionCheck:
        """仅核对已确认的精确版本；不回退最新对象，也不把可签名当可读。"""
        self._key(key)
        if not version or version == "null":
            raise BlobError("VERSION_REQUIRED")
        self.check_bucket()
        with private_blob_logs():
            try:
                head = self.client.head_object(
                    Bucket=self.bucket, Key=key, VersionId=version, ChecksumMode="ENABLED"
                )
                if (
                    head.get("VersionId") != version
                    or head.get("ContentLength") != size
                    or head.get("ContentType") != mime
                    or head.get("Metadata", {}).get("upload-id") != str(asset)
                ):
                    raise BlobError("VERSION_METADATA_MISMATCH")
                checksum = head.get("ChecksumSHA256")
                if checksum is not None and checksum != base64.b64encode(
                    bytes.fromhex(sha256)
                ).decode("ascii"):
                    raise BlobError("VERSION_CHECKSUM_MISMATCH")
            except (ClientError, BotoCoreError):
                raise BlobError("VERSION_UNREADABLE") from None

        checked = datetime.now(UTC)
        return VersionCheck(
            checked,
            self._version_token(key, version, size, mime, sha256, asset, checked),
            (key, version, size, mime, sha256, asset),
        )

    def _version_token(
        self,
        key: str,
        version: str,
        size: int,
        mime: str,
        sha256: str,
        asset: UUID,
        checked: datetime,
    ) -> str:
        raw = json.dumps(
            [self.bucket, key, version, size, mime, sha256, str(asset), checked.isoformat()],
            separators=(",", ":"),
        ).encode()
        return hmac.new(self._attest_key, raw, hashlib.sha256).hexdigest()

    def valid_version_check(
        self,
        check: VersionCheck,
        key: str,
        version: str,
        size: int,
        mime: str,
        sha256: str,
        asset: UUID,
    ) -> bool:
        if (
            re.fullmatch(r"[0-9a-f]{64}", check.token) is None
            or check.checked_at.tzinfo is None
            or not 0 <= (datetime.now(UTC) - check.checked_at).total_seconds() <= 60
        ):
            return False
        return hmac.compare_digest(
            check.token,
            self._version_token(key, version, size, mime, sha256, asset, check.checked_at),
        )

    def download_url(
        self, key: str, version: str, *, expires: int = 60, check: VersionCheck | None = None
    ) -> str:
        self._key(key)
        if (
            check is None
            or check.context is None
            or check.context[:2] != (key, version)
            or not self.valid_version_check(check, *check.context)
        ):
            raise BlobError("VERIFIED_VERSION_REQUIRED")
        if not version or version == "null" or not 1 <= expires <= 300:
            raise ValueError("下载版本或有效期非法")
        with private_blob_logs():
            try:
                return self.client.generate_presigned_url(
                    "get_object",
                    Params=dict(
                        Bucket=self.bucket,
                        Key=key,
                        VersionId=version,
                        ResponseContentDisposition="attachment",
                        ResponseContentType="application/octet-stream",
                    ),
                    ExpiresIn=expires,
                )
            except (ClientError, BotoCoreError):
                raise BlobError("SIGN_UNAVAILABLE") from None

    def close(self) -> None:
        self.client.close()
