"""上传入参、实际文件解码和临时签名日志保护。"""

import io
import logging
from typing import Any
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import SecretStr, ValidationError

from yuliang.infrastructure.blob import BlobError, BlobStore, private_blob_logs
from yuliang.infrastructure.media_probe import MediaMismatch, validate_media
from yuliang.modules.content.uploads_models import UploadCreate


def value() -> dict[str, Any]:
    return dict(
        clientRequestKey=str(uuid4()),
        name="图片.png",
        kind="image",
        mime="image/png",
        size=10,
        sha256="a" * 64,
    )


@pytest.mark.parametrize(
    "changes",
    [
        dict(kind="video", mime="video/mp4"),
        dict(kind="audio", mime="audio/mpeg"),
        dict(object_key="tenants/other"),
        dict(bucket="public"),
        dict(size=True),
        dict(size=0),
        dict(size=20971521),
        dict(mime="image/svg+xml"),
        dict(name="bad\nname"),
        dict(sha256="bad"),
        dict(clientRequestKey="bad"),
    ],
)
def test_upload_boundaries_fail_closed(changes: dict[str, Any]) -> None:
    v = value()
    v.update(changes)
    with pytest.raises(ValidationError):
        UploadCreate.model_validate(v)


@pytest.mark.parametrize("mime", ["image/png", "image/jpeg", "image/webp", "text/plain"])
def test_real_bytes_must_match_declared_mime(mime: str) -> None:
    if mime == "text/plain":
        data = "文本".encode()
    else:
        target = io.BytesIO()
        Image.new("RGB", (2, 2)).save(
            target, format={"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}[mime]
        )
        data = target.getvalue()
    validate_media(io.BytesIO(data), mime, len(data))
    with pytest.raises(MediaMismatch):
        validate_media(io.BytesIO(b"\xffnot-real"), mime, 10)


def test_no_ssrf_keys_or_signature_logging(caplog: Any) -> None:
    with pytest.raises(ValueError):
        BlobStore(
            "http://example.com",
            "private-bucket",
            SecretStr("synthetic"),
            SecretStr("synthetic"),
            local=True,
        )
    with pytest.raises(BlobError):
        BlobStore._key("../tenants/other")
    caplog.set_level(logging.DEBUG)
    with private_blob_logs():
        logging.getLogger("botocore.auth").debug("secret signature %s", "DO-NOT-LOG")
    assert "DO-NOT-LOG" not in caplog.text
    assert "请求细节已省略" in caplog.text


def test_corrupt_png_structure_is_rejected_as_media_mismatch() -> None:
    target = io.BytesIO()
    Image.new("RGB", (2, 2)).save(target, format="PNG")
    content = bytearray(target.getvalue())
    content[29] ^= 1
    with pytest.raises(MediaMismatch):
        validate_media(io.BytesIO(content), "image/png", len(content))
