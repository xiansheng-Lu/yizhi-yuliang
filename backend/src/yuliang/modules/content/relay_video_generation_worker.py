"""后台执行中转站视频同步生成：唯一领取、真实字节校验、MP4实测时长与私有存储。"""

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from yuliang.infrastructure.blob import BlobError, BlobStore
from yuliang.infrastructure.mp4 import Mp4Error, mp4_duration_seconds
from yuliang.modules.content.relay_video_calls import RelayVideoCall, VideoCallStore
from yuliang.modules.content.video_generation_config import VideoTransportConfig

MAX_VIDEO_BYTES = 104857600
MAX_SETTLED_MICRO_MINOR = 9223372036854775807


class RelayVideoError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class VideoBlobWriter(Protocol):
    def put_bytes(self, key: str, body: bytes, mime: str, upload: UUID) -> None: ...


@dataclass(frozen=True)
class VideoArtifact:
    provider_task_id: str
    data: bytes
    sha256: str
    measured_duration_seconds: int


def _external(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and not any(ord(character) < 32 for character in value)
    )


def _receipt(response: httpx.Response) -> tuple[str, str, str | None]:
    status = response.status_code
    if not 200 <= status < 300:
        if 400 <= status < 500:
            raise RelayVideoError("PROVIDER_REJECTED", f"视频中转站拒绝请求（HTTP {status}）")
        raise RelayVideoError(
            "OUTCOME_UNKNOWN", f"视频中转站返回异常响应（HTTP {status}），结果待核对"
        )
    try:
        payload: Any = response.json()
    except ValueError:
        raise RelayVideoError(
            "OUTCOME_UNKNOWN", "视频中转站响应不可解析，结果与费用待核对"
        ) from None
    if not isinstance(payload, dict):
        raise RelayVideoError("RESPONSE_INVALID", "视频中转站响应结构非法")
    state = payload.get("status")
    if state == "failed":
        raise RelayVideoError("PROVIDER_FAILED", "视频中转站返回失败状态")
    if state != "succeeded":
        raise RelayVideoError("OUTCOME_UNKNOWN", "视频中转站返回未确认状态，结果与费用待核对")
    external = payload.get("id")
    if not _external(external):
        raise RelayVideoError("RESPONSE_INVALID", "视频中转站任务标识非法")
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise RelayVideoError("RESPONSE_INVALID", "视频中转站未返回结果地址")
    first = results[0]
    if not isinstance(first, dict):
        raise RelayVideoError("RESPONSE_INVALID", "视频中转站结果地址非法")
    url_value = first.get("url")
    if not isinstance(url_value, str):
        raise RelayVideoError("RESPONSE_INVALID", "视频中转站结果地址非法")
    url = urlsplit(url_value)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise RelayVideoError("RESPONSE_INVALID", "视频结果下载地址不是可信HTTPS地址")
    declared = first.get("sha256")
    if declared is not None and (
        not isinstance(declared, str) or re.fullmatch(r"[0-9a-f]{64}", declared) is None
    ):
        raise RelayVideoError("RESPONSE_INVALID", "视频中转站声明摘要非法")
    return str(external), url_value, declared


def _artifact(response: httpx.Response, declared: str | None) -> tuple[bytes, str, int]:
    if not 200 <= response.status_code < 300:
        raise RelayVideoError(
            "ARTIFACT_DOWNLOAD_FAILED", f"视频结果下载失败（HTTP {response.status_code}）"
        )
    mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if mime != "video/mp4":
        raise RelayVideoError("ARTIFACT_MIME_INVALID", "视频结果声明类型不是video/mp4")
    data = response.content
    if not 0 < len(data) <= MAX_VIDEO_BYTES:
        raise RelayVideoError("ARTIFACT_MIME_INVALID", "视频结果大小超出私有存储边界")
    digest = hashlib.sha256(data).hexdigest()
    if declared is not None and digest != declared:
        raise RelayVideoError("ARTIFACT_CHECKSUM_MISMATCH", "视频结果摘要与实际字节不符")
    try:
        seconds = mp4_duration_seconds(data)
    except Mp4Error:
        raise RelayVideoError(
            "MP4_DURATION_UNREADABLE", "视频结果无法解析实际时长，不能以请求时长代替"
        ) from None
    return data, digest, seconds


def request_artifact(
    *,
    config: VideoTransportConfig,
    prompt: str,
    duration_seconds: int,
    resolution: str,
    aspect_ratio: str,
    transport: httpx.BaseTransport | None = None,
    timeout_seconds: float = 1200.0,
) -> VideoArtifact:
    if not 1200.0 <= timeout_seconds <= 3600.0:
        raise ValueError("视频生成超时不得低于1200秒")
    headers = {
        "Authorization": f"Bearer {config.key.get_secret_value()}",
        "Accept": "application/json",
    }
    body = {
        "model": config.model,
        "prompt": prompt,
        "aspectRatio": aspect_ratio,
        "resolution": resolution,
        "duration": duration_seconds,
    }
    try:
        with httpx.Client(
            transport=transport,
            timeout=timeout_seconds,
            follow_redirects=False,
        ) as client:
            receipt = client.post(config.endpoint, json=body, headers=headers)
            external, url, declared = _receipt(receipt)
            downloaded = client.get(url, headers={"Accept": "video/mp4"})
            data, digest, seconds = _artifact(downloaded, declared)
    except httpx.TimeoutException:
        raise RelayVideoError("OUTCOME_UNKNOWN", "视频中转站响应超时，结果与费用待核对") from None
    except httpx.TransportError:
        raise RelayVideoError("OUTCOME_UNKNOWN", "视频中转站连接中断，结果与费用待核对") from None
    return VideoArtifact(
        provider_task_id=external,
        data=data,
        sha256=digest,
        measured_duration_seconds=seconds,
    )


def _settled_micro_minor(call: RelayVideoCall, measured_seconds: int) -> int:
    if call.pricing_mode == "PER_SECOND":
        amount = call.unit_price_micro_minor * measured_seconds
    elif call.pricing_mode == "PER_CALL":
        amount = call.unit_price_micro_minor
    else:
        raise RelayVideoError("PRICE_MODE_INVALID", "视频计价模式非法")
    if amount > MAX_SETTLED_MICRO_MINOR:
        raise RelayVideoError("PRICE_RANGE_INVALID", "视频费用超出账本范围")
    return amount


def execute_relay_video_call(
    *,
    store: VideoCallStore,
    blob: VideoBlobWriter,
    call_id: UUID,
    config: VideoTransportConfig,
    transport: httpx.BaseTransport | None = None,
    timeout_seconds: float = 1200.0,
) -> dict[str, Any]:
    """条件更新领取后只执行一次；任何失败都落FAILED，绝不自动重试。"""
    call = store.claim(call_id)
    if call is None:
        return {"callId": str(call_id), "state": "SKIPPED"}
    try:
        artifact = request_artifact(
            config=config,
            prompt=call.prompt,
            duration_seconds=call.duration_seconds,
            resolution=call.resolution,
            aspect_ratio=call.aspect_ratio,
            transport=transport,
            timeout_seconds=timeout_seconds,
        )
        settled = _settled_micro_minor(call, artifact.measured_duration_seconds)
        blob_key = BlobStore.video_key(call.tenant_id, call.shop_id, call.id)
        blob.put_bytes(blob_key, artifact.data, "video/mp4", call.id)
    except RelayVideoError as error:
        store.fail(call.id, expected="RUNNING", error_code=error.code, error_message=error.message)
        return {"callId": str(call.id), "state": "FAILED", "errorCode": error.code}
    except (BlobError, ValueError):
        store.fail(
            call.id,
            expected="RUNNING",
            error_code="BLOB_WRITE_FAILED",
            error_message="视频产物写入私有存储失败",
        )
        return {"callId": str(call.id), "state": "FAILED", "errorCode": "BLOB_WRITE_FAILED"}
    store.succeed(
        call.id,
        provider_task_id=artifact.provider_task_id,
        blob_key=blob_key,
        mime="video/mp4",
        size_bytes=len(artifact.data),
        sha256=artifact.sha256,
        measured_duration_seconds=artifact.measured_duration_seconds,
        settled_amount_micro_minor=settled,
    )
    return {
        "callId": str(call.id),
        "state": "SUCCEEDED",
        "sha256": artifact.sha256,
        "sizeBytes": len(artifact.data),
        "measuredDurationSeconds": artifact.measured_duration_seconds,
    }
