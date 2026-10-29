"""第三方中转站图像生成适配器：同步生图、实际字节校验、复用上传/素材登记。

该中转站只有同步生成端点，没有按请求键回查端点，因此 query 一律保持 UNKNOWN，
不伪造进度，也不为试探花费真实额度。真实付费调用只在显式配置与上层SENT栅栏后发生。
"""

import hashlib
import io
import json
import re
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import SecretStr

from yuliang.infrastructure.blob import BlobError, BlobStore
from yuliang.infrastructure.media_probe import MediaMismatch, validate_media
from yuliang.modules.aikernel.cost_models import BillingOutcome
from yuliang.modules.content.generation_provider import ProviderResult
from yuliang.modules.content.service import ContentService
from yuliang.modules.content.uploads import UploadService
from yuliang.modules.content.uploads_models import UploadCreate
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

IMAGE_MIME = frozenset({"image/png", "image/jpeg", "image/webp"})
IMAGE_EXTENSION = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
IMAGE_SIZE = "1024x1024"
MAX_IMAGE_BYTES = 20971520
TIMEOUT_SECONDS = 120.0
ENDPOINT_PATTERN = r"https://[^/?#@\s]+/[^?#\s]+"


class ImageAssetWriter(Protocol):
    """把生成图片写入内部私有存储并登记为已确认素材，返回素材UUID。"""

    def write(self, request_key: str, document_id: str, data: bytes, mime: str) -> UUID: ...


class UploadBackedImageWriter:
    """复用现有上传计划、对象校验和素材登记路径；不另起存储或素材表。"""

    def __init__(self, content: ContentService) -> None:
        blob = content.blob
        if content.shop is None or not isinstance(blob, BlobStore):
            raise ValueError("生成素材落库需要明确店铺与私有对象存储")
        self.content = content
        self.shop = content.shop
        self.blob = blob

    def write(self, request_key: str, document_id: str, data: bytes, mime: str) -> UUID:
        document = UUID(document_id)
        with business_transaction(self.content.engine, self.content.identity) as session:
            row = self.content._row(session, document, True)
        product = row["product_id"]
        uploads = UploadService(
            self.content.engine, self.content.identity, self.shop, self.blob
        )
        value = UploadCreate(
            clientRequestKey=request_key,
            name=f"生成图-{request_key}.{IMAGE_EXTENSION[mime]}",
            kind="image",
            productId=str(product) if product else "",
            documentId=document_id,
            mime=mime,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        plan = uploads.create(value)
        identifier = UUID(str(plan["uploadId"]))
        if plan["state"] == "CONFIRMED":
            # 同一请求键已登记素材时复用原计划；不覆盖已确认对象版本。
            return identifier
        try:
            self.blob.put_bytes(
                self.blob.key(self.content.identity.tenant_id, self.shop, identifier),
                data,
                mime,
                identifier,
            )
        except BlobError:
            raise ApiError(ErrorCode.UPSTREAM_ERROR, "生成产物写入私有存储失败") from None
        return UUID(str(uploads.complete(identifier)["id"]))


class RelayImageGenerationProvider:
    """中转站同步生图：成功只经真实图片字节与素材登记进入 WAITING_REVIEW。"""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: SecretStr,
        price_minor: int,
        writer: ImageAssetWriter,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = TIMEOUT_SECONDS,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path in {"", "/"}
            or re.fullmatch(ENDPOINT_PATTERN, endpoint) is None
        ):
            raise ValueError("图像生图端点需为精确HTTPS生成地址")
        if not model.strip() or not api_key.get_secret_value().strip():
            raise ValueError("图像生图模型或密钥不可为空")
        if type(price_minor) is not int or not 0 < price_minor <= 9223372036854775807:
            raise ValueError("图像生图单价缺失或非法，禁止按0元计价")
        if not isinstance(timeout_seconds, float) or not 1 <= timeout_seconds <= 600:
            raise ValueError("图像生图超时须为1至600秒")
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.price_minor = price_minor
        self.writer = writer
        self.transport = transport
        self.timeout = timeout_seconds

    def submit(
        self, request_key: str, payload_hash: str, parameters: dict[str, object]
    ) -> ProviderResult:
        kind = parameters.get("kind")
        if kind not in {"IMAGE", "GRAPHIC"}:
            raise ApiError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                f"图像生图中转站不支持{kind}生成，未发起付费调用",
            )
        prompt = parameters.get("prompt")
        document_id = parameters.get("documentId")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ApiError(ErrorCode.INVALID_INPUT, "图像生图提示不可为空或全空白")
        if not isinstance(document_id, str):
            raise ApiError(ErrorCode.INVALID_INPUT, "图像生图缺少来源文档范围")
        try:
            UUID(document_id)
        except ValueError:
            raise ApiError(ErrorCode.INVALID_INPUT, "图像生图来源文档标识非法") from None
        headers = {
            "Authorization": f"Bearer {self.api_key.get_secret_value()}",
            "Accept": "application/json",
        }
        body = {"model": self.model, "prompt": prompt, "size": IMAGE_SIZE, "n": 1}
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=self.timeout,
                follow_redirects=False,
            ) as client:
                response = client.post(self.endpoint, json=body, headers=headers)
                external, url, failed = self._receipt(response)
                if failed:
                    return ProviderResult("FAILED", external_id=external)
                if external is None or url is None:
                    raise ApiError(
                        ErrorCode.RESULT_UNKNOWN,
                        "图像生图中转站未返回完整结果，结果与费用待核对",
                    )
                downloaded = client.get(url, headers={"Accept": "image/png,image/jpeg,image/webp"})
                data, mime = self._image(downloaded)
        except httpx.TimeoutException:
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN, "图像生图中转站响应超时，结果与费用待核对"
            ) from None
        except httpx.TransportError:
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN, "图像生图中转站连接中断，结果与费用待核对"
            ) from None
        asset = self.writer.write(request_key, document_id, data, mime)
        proof = self._proof(
            request_key, external, hashlib.sha256(data).hexdigest()
        )
        return ProviderResult(
            "READY_FOR_REVIEW",
            external_id=external,
            asset_id=asset,
            billing=BillingOutcome(
                state="KNOWN",
                amount_minor=self.price_minor,
                currency="CNY",
                proof_hash=proof,
            ),
        )

    def query(self, request_key: str) -> ProviderResult:
        # 已实测该站没有按请求键回查端点（GET 返回404），不尝试探测、不伪造状态。
        return ProviderResult("UNKNOWN")

    def _receipt(self, response: httpx.Response) -> tuple[str | None, str | None, bool]:
        status = response.status_code
        if not 200 <= status < 300:
            if 400 <= status < 500:
                raise ApiError(
                    ErrorCode.UPSTREAM_ERROR, f"图像生图中转站拒绝请求（HTTP {status}）"
                )
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN,
                f"图像生图中转站返回异常响应（HTTP {status}），结果与费用待核对",
            )
        try:
            payload: Any = response.json()
        except ValueError:
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN, "图像生图中转站响应不可解析，结果与费用待核对"
            ) from None
        if not isinstance(payload, dict):
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN, "图像生图中转站响应结构非法，结果与费用待核对"
            )
        external_value = payload.get("id")
        external: str | None = None
        if external_value is not None:
            if not isinstance(external_value, str) or not self._external(external_value):
                raise ApiError(
                    ErrorCode.RESULT_UNKNOWN,
                    "图像生图中转站任务标识非法，结果与费用待核对",
                )
            external = external_value
        state = payload.get("status")
        if state == "failed":
            return external, None, True
        if state != "succeeded":
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN,
                "图像生图中转站返回未确认状态，结果与费用待核对",
            )
        if external is None:
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN, "图像生图中转站缺少任务标识，结果与费用待核对"
            )
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN, "图像生图中转站未返回结果地址，结果与费用待核对"
            )
        first = results[0]
        if not isinstance(first, dict):
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN, "图像生图中转站结果地址非法，结果与费用待核对"
            )
        url_value = first.get("url")
        if not isinstance(url_value, str):
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN, "图像生图中转站结果地址非法，结果与费用待核对"
            )
        url = urlsplit(url_value)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN, "图像结果下载地址不是可信HTTPS地址"
            )
        return external, url_value, False

    @staticmethod
    def _external(value: object) -> bool:
        return (
            isinstance(value, str)
            and 1 <= len(value) <= 128
            and not any(ord(character) < 32 for character in value)
        )

    def _image(self, response: httpx.Response) -> tuple[bytes, str]:
        if not 200 <= response.status_code < 300:
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN,
                f"图像结果下载失败（HTTP {response.status_code}），结果与费用待核对",
            )
        mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if mime not in IMAGE_MIME:
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN, "生成结果声明类型不是受支持图片，结果与费用待核对"
            )
        data = response.content
        if not 0 < len(data) <= MAX_IMAGE_BYTES:
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN, "生成结果大小超出私有图片边界，结果与费用待核对"
            )
        try:
            validate_media(io.BytesIO(data), mime, len(data))
        except MediaMismatch:
            raise ApiError(
                ErrorCode.RESULT_UNKNOWN, "生成结果声明类型与实际字节不符，结果与费用待核对"
            ) from None
        return data, mime

    def _proof(self, request_key: str, external: str, image_sha256: str) -> str:
        # 回执摘要沿用既有账本约定：对规范化账单事实取SHA256，仅作已结算回执凭证。
        receipt = {
            "provider": "relay-image",
            "endpoint": self.endpoint,
            "model": self.model,
            "requestKey": request_key,
            "externalId": external,
            "sha256": image_sha256,
            "amountMinor": self.price_minor,
            "currency": "CNY",
        }
        return hashlib.sha256(
            json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
