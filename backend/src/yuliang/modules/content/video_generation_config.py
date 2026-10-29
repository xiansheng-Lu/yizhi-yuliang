"""视频供应商配置只从显式环境变量读取；缺失或歧义一律拒绝，不静默降级。"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from pydantic import SecretStr

from yuliang.modules.content.video_generation_models import VideoPrice
from yuliang.platform.errors import ApiError, ErrorCode

ENDPOINT = "YULIANG_VIDEOGEN_PRIMARY_ENDPOINT"
MODEL = "YULIANG_VIDEOGEN_PRIMARY_MODEL"
KEY = "YULIANG_VIDEOGEN_PRIMARY_KEY"
PRICE_PER_SECOND = "YULIANG_VIDEOGEN_PRIMARY_PRICE_PER_SECOND"
PRICE_PER_CALL = "YULIANG_VIDEOGEN_PRIMARY_PRICE_PER_CALL"
RESOLUTION = "YULIANG_VIDEOGEN_PRIMARY_RESOLUTION"
ASPECT_RATIO = "YULIANG_VIDEOGEN_PRIMARY_ASPECT_RATIO"

# 中转站只接受这些取值；不给默认值，避免替用户静默决定分辨率与费用。
RESOLUTIONS = frozenset({"480p", "768p", "1080p"})
ASPECT_RATIOS = frozenset({"portrait", "landscape"})

# 环境变量按“元”填写，数据库价目按minor+micro-minor保存。
_MINOR_PER_YUAN = Decimal(100)
_MICRO_PER_MINOR = Decimal(1000000)


@dataclass(frozen=True)
class VideoTransportConfig:
    """后台执行只需连接信息；价格、分辨率等已在调用记录中冻结。"""

    endpoint: str
    model: str
    key: SecretStr


@dataclass(frozen=True)
class VideoProviderConfig(VideoTransportConfig):
    resolution: str
    aspect_ratio: str
    price: VideoPrice


def _unconfigured(reason: str) -> ApiError:
    return ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, f"视频生成供应商未配置：{reason}")


def _text(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise _unconfigured(f"缺少{name}")
    return value


def _endpoint(values: Mapping[str, str]) -> str:
    endpoint = _text(values, ENDPOINT)
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.scheme == "http"
        and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
    ):
        raise _unconfigured(f"{ENDPOINT}不是可信HTTPS端点")
    return endpoint


def _choice(values: Mapping[str, str], name: str, allowed: frozenset[str]) -> str:
    value = _text(values, name)
    if value not in allowed:
        raise _unconfigured(f"{name}取值非法，仅允许{'/'.join(sorted(allowed))}")
    return value


def _micro_minor(name: str, value: str) -> int:
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise _unconfigured(f"{name}不是合法金额") from None
    if not amount.is_finite() or amount <= 0:
        raise _unconfigured(f"{name}必须为正数，不能以0元估价")
    converted = amount * _MINOR_PER_YUAN * _MICRO_PER_MINOR
    if converted != converted.to_integral_value() or converted > 9223372036854775807:
        raise _unconfigured(f"{name}精度或范围超出账本能力")
    return int(converted)


def _environ(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def video_generation_priced(environ: Mapping[str, str] | None = None) -> bool:
    """单价为空即视为未配置；只有显式单价才允许装配真实付费适配器。"""
    values = _environ(environ)
    return bool(values.get(PRICE_PER_SECOND, "").strip() or values.get(PRICE_PER_CALL, "").strip())


def load_video_transport_config(
    environ: Mapping[str, str] | None = None,
) -> VideoTransportConfig:
    values = _environ(environ)
    return VideoTransportConfig(
        endpoint=_endpoint(values),
        model=_text(values, MODEL),
        key=SecretStr(_text(values, KEY)),
    )


def load_video_provider_config(
    environ: Mapping[str, str] | None = None,
) -> VideoProviderConfig:
    values = _environ(environ)
    transport = load_video_transport_config(values)
    resolution = _choice(values, RESOLUTION, RESOLUTIONS)
    aspect_ratio = _choice(values, ASPECT_RATIO, ASPECT_RATIOS)
    per_second = values.get(PRICE_PER_SECOND, "").strip()
    per_call = values.get(PRICE_PER_CALL, "").strip()
    if per_second and per_call:
        raise _unconfigured("按秒与按次单价同时存在，计价模式歧义")
    if not per_second and not per_call:
        raise _unconfigured(f"缺少{PRICE_PER_SECOND}或{PRICE_PER_CALL}")
    if per_second:
        rate = _micro_minor(PRICE_PER_SECOND, per_second)
        price = VideoPrice(mode="PER_SECOND", micro_minor_per_unit=rate)
    else:
        rate = _micro_minor(PRICE_PER_CALL, per_call)
        price = VideoPrice(mode="PER_CALL", micro_minor_per_unit=rate)
    return VideoProviderConfig(
        endpoint=transport.endpoint,
        model=transport.model,
        key=transport.key,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        price=price,
    )
