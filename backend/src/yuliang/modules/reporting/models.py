"""事实口径由服务器生成；缺失与0分开，资金值拒绝浮点。"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from yuliang.modules.capability.models import CONNECTOR_CONTEXTS

SAFE_INTEGER = 9007199254740991
MAX_NUMERIC = Decimal("999999999999999999.999999")
Numeric = Annotated[Decimal, Field(max_digits=24, decimal_places=6)]


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class FactDimensions(Frozen):
    fact_date: date
    granularity: Literal["DAY", "HOUR"]
    fact_hour: int | None = Field(default=None, strict=True, ge=0, le=23)
    provider: str
    product_line: str
    entity_kind: Literal["SHOP", "ACCOUNT", "CAMPAIGN", "LIVE", "PRODUCT", "SKU", "VIDEO"]
    native_entity_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    shop_id: UUID
    account_id: UUID | None = None
    campaign_id: UUID | None = None
    live_id: UUID | None = None
    product_id: UUID | None = None
    sku_id: UUID | None = None
    metric_name: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,39}$")
    unit: Literal["MINOR", "COUNT", "RATE", "RATIO", "HOURS", "BEAN"]
    currency: Literal["CNY", "BEAN"] | None = None
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    attribution_window: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,79}$")
    refund_policy: Literal["GROSS", "NET_OF_REFUND", "EXCLUDE_REFUND", "NOT_APPLICABLE", "UNKNOWN"]
    basis_currency: Literal["CNY", "BEAN"] | None = None

    @model_validator(mode="after")
    def dimensions_match(self) -> "FactDimensions":
        if (self.provider, self.product_line) not in CONNECTOR_CONTEXTS.values():
            raise ValueError("未知产品线上下文")
        if (self.granularity == "HOUR") != (self.fact_hour is not None):
            raise ValueError("小时粒度须有小时，自然日不能混入小时")
        if self.sku_id is not None and self.product_id is None:
            raise ValueError("SKU必须关联商品")
        if self.campaign_id is not None and self.account_id is None:
            raise ValueError("计划必须关联账户")
        required = {
            "ACCOUNT": self.account_id,
            "CAMPAIGN": self.campaign_id,
            "LIVE": self.live_id,
            "PRODUCT": self.product_id,
            "SKU": self.sku_id,
            "SHOP": self.shop_id,
        }
        if self.entity_kind in required and required[self.entity_kind] is None:
            raise ValueError("统计对象缺少真实目录引用")
        expected = "CNY" if self.unit == "MINOR" else "BEAN" if self.unit == "BEAN" else None
        if self.currency != expected:
            raise ValueError("单位与币种不匹配")
        if self.unit not in {"RATE", "RATIO"} and self.basis_currency is not None:
            raise ValueError("只有比率可声明分子分母币种")
        return self

    @property
    def comparable_key(self) -> str:
        return "|".join(
            [
                "v1",
                self.timezone,
                self.attribution_window,
                self.refund_policy,
                self.currency or "-",
                self.unit,
                self.basis_currency or "-",
            ]
        )

    def business_key(self) -> dict[str, Any]:
        return self.model_dump(mode="json") | {"comparable_key": self.comparable_key}


def decimal_input(value: Any) -> Any:
    if value is None or isinstance(value, Decimal):
        return value
    if type(value) is int:
        return Decimal(value)
    raise ValueError("事实值必须是整数或Decimal，拒绝浮点/布尔和隐式字符串转换")


class MetricSample(Frozen):
    dimensions: FactDimensions
    value: Numeric | None = None
    numerator: Numeric | None = None
    denominator: Numeric | None = None

    @field_validator("value", "numerator", "denominator", mode="before")
    @classmethod
    def exact_input(cls, value: Any) -> Any:
        return decimal_input(value)

    @model_validator(mode="after")
    def value_matches(self) -> "MetricSample":
        if self.dimensions.unit in {"RATE", "RATIO"}:
            if self.value is not None or (self.numerator is None) != (self.denominator is None):
                raise ValueError("比率只接受同口径的分子分母，不接受平台比率或比率平均")
        else:
            if self.numerator is not None or self.denominator is not None:
                raise ValueError("非比率事实不能混入比率分子分母")
            if self.value is not None and self.dimensions.unit in {"MINOR", "BEAN", "COUNT"}:
                if self.value != self.value.to_integral_value() or abs(self.value) > SAFE_INTEGER:
                    raise ValueError("金额、豆和计数必须为JSON安全整数，0允许")
        if self.normalized_value is not None and abs(self.normalized_value) > MAX_NUMERIC:
            raise ValueError("事实数值超过存储边界")
        return self

    @property
    def normalized_value(self) -> Decimal | None:
        if self.dimensions.unit not in {"RATE", "RATIO"}:
            return self.value
        if self.numerator is None or self.denominator is None or self.denominator == 0:
            return None
        with localcontext() as context:
            context.prec = 60
            return (self.numerator / self.denominator).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )


class SourceProof(Frozen):
    sync_run_id: UUID
    page_no: int = Field(strict=True, ge=0)
    outbound_id: UUID
    source_updated_at: AwareDatetime | None
    fetched_at: AwareDatetime
    source_version: int | None = Field(default=None, strict=True, ge=0, le=9223372036854775807)
    version_stream: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,79}$")

    @model_validator(mode="after")
    def version_has_meaning(self) -> "SourceProof":
        if (self.source_version is None) != (self.version_stream is None):
            raise ValueError("来源版本须同时声明实际可排序的版本流")
        return self


def aggregate_ratio(samples: tuple[MetricSample, ...]) -> Decimal | None:
    if not samples:
        return None
    first = samples[0].dimensions
    if first.unit not in {"RATE", "RATIO"}:
        raise ValueError("只能从比率分子分母重算")
    if first.refund_policy == "UNKNOWN" or first.attribution_window.upper() in {
        "UNKNOWN",
        "UNCONFIRMED",
    }:
        return None
    keys = [tuple(sorted(sample.dimensions.business_key().items())) for sample in samples]
    if len(set(keys)) != len(keys):
        raise ValueError("不能重复计算同一事实维度")

    def grain(key: FactDimensions) -> tuple[str, str, tuple[bool, ...]]:
        return (
            key.granularity,
            key.entity_kind,
            tuple(
                getattr(key, name) is not None
                for name in ("account_id", "campaign_id", "live_id", "product_id", "sku_id")
            ),
        )

    if any(
        sample.dimensions.metric_name != first.metric_name
        or grain(sample.dimensions) != grain(first)
        or sample.dimensions.comparable_key != first.comparable_key
        or (sample.dimensions.provider, sample.dimensions.product_line)
        != (first.provider, first.product_line)
        for sample in samples
    ):
        raise ValueError("产品线、币种或归因退款口径不可比")
    if any(sample.numerator is None or sample.denominator is None for sample in samples):
        return None
    numerator = sum(
        (sample.numerator for sample in samples if sample.numerator is not None), Decimal(0)
    )
    denominator = sum(
        (sample.denominator for sample in samples if sample.denominator is not None), Decimal(0)
    )
    if denominator == 0:
        return None
    with localcontext() as context:
        context.prec = 60
        return (numerator / denominator).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
