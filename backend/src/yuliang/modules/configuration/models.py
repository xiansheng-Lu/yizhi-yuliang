"""与AdvertisingService V9共用的上下文、字段六态和整数分/比率/微信豆单位。"""

import json
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

type Product = Literal[
    "adq", "beans", "shop", "ocean_marketing", "qianchuan", "dou_plus", "local_push", "juguang"
]
SCENARIOS = {
    "adq": frozenset({"ADQ"}),
    "beans": frozenset({"VIDEO", "LIVE"}),
    "shop": frozenset({"PRODUCT_HOSTING", "SHOP_LIVE"}),
    "ocean_marketing": frozenset({"PROJECT_PROMOTION"}),
    "qianchuan": frozenset({"QIANCHUAN"}),
    "dou_plus": frozenset({"DOU_PLUS"}),
    "local_push": frozenset({"LOCAL_PUSH"}),
    "juguang": frozenset({"XHS_STANDARD", "XHS_SEARCH", "XHS_SIMPLE"}),
}
PRODUCT_CONTEXTS = {
    "adq": ("tencent", "adq", "adq"),
    "beans": ("beans", "beans", "beans"),
    "shop": ("tencent", "shop", "wxpromo"),
    "qianchuan": ("ocean", "qianchuan", "qianchuan"),
    "juguang": ("xhs", "juguang", "juguang"),
}
type FieldId = Literal[
    "name",
    "account",
    "shop",
    "objective",
    "target",
    "placement",
    "optimization",
    "strategy",
    "targetingMode",
    "region",
    "locationType",
    "age",
    "gender",
    "interest",
    "behavior",
    "intention",
    "keyword",
    "behaviorScene",
    "behaviorWindow",
    "behaviorIntensity",
    "customAudience",
    "expansion",
    "hardConstraints",
    "template",
    "creators",
    "groups",
    "budgetType",
    "budget",
    "bidMode",
    "bid",
    "schedule",
    "timeSlots",
    "creative",
    "title",
    "copy",
    "portrait",
    "duration",
    "orderType",
    "fundType",
    "deliveryMode",
    "creativeMethod",
    "productRange",
]
type FieldState = Literal[
    "PUBLIC", "AVAILABLE", "API_PENDING", "AUTH_REQUIRED", "NOT_APPLICABLE", "UNCONFIRMED"
]
type ValueType = Literal["TEXT", "SELECT", "MULTI_SELECT", "INTEGER", "DECIMAL", "RANGE", "MEDIA"]
type FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class V9Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        alias_generator=to_camel,
        populate_by_name=True,
        hide_input_in_errors=True,
    )


class ConfigurationContext(V9Model):
    product: Product
    scenario: str = Field(min_length=1, max_length=30)
    business_entity_id: UUID
    account_id: UUID
    shop_id: str = Field(max_length=36)
    objective_id: str = Field(max_length=128)
    optimization_id: str = Field(max_length=128)
    strategy_id: str = Field(max_length=128)
    target_ids: tuple[str, ...] = Field(max_length=100)
    placement_ids: tuple[str, ...] = Field(max_length=100)
    bid_mode_id: str = Field(max_length=128)
    order_type_id: str = Field(max_length=128)
    fund_type_id: str = Field(max_length=128)
    delivery_mode_id: str = Field(max_length=128)
    creative_method_id: str = Field(max_length=128)

    @model_validator(mode="after")
    def valid_context(self) -> Self:
        if self.scenario not in SCENARIOS[self.product]:
            raise ValueError("产品和场景不匹配")
        if self.shop_id:
            UUID(self.shop_id)
        identifiers = (
            self.objective_id,
            self.optimization_id,
            self.strategy_id,
            self.bid_mode_id,
            self.order_type_id,
            self.fund_type_id,
            self.delivery_mode_id,
            self.creative_method_id,
            *self.target_ids,
            *self.placement_ids,
        )
        if any(any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in v) for v in identifiers):
            raise ValueError("配置标识不能含控制字符或无效Unicode")
        for values in (self.target_ids, self.placement_ids):
            if len(set(values)) != len(values) or any(
                not value or len(value) > 128 or any(ord(c) < 32 for c in value) for value in values
            ):
                raise ValueError("目标/版位须为有界、无重复的实际标识")
        return self

    @property
    def context_key(self) -> str:
        # JS Array.sort按UTF-16排序，跨端不把JSON字符串换成服务端自定义摘要。
        def ordered(items: tuple[str, ...]) -> list[str]:
            return sorted(items, key=lambda item: item.encode("utf-16-be"))

        return json.dumps(
            [
                self.product,
                self.scenario,
                str(self.business_entity_id),
                str(self.account_id),
                self.shop_id,
                self.objective_id,
                self.optimization_id,
                self.strategy_id,
                ordered(self.target_ids),
                ordered(self.placement_ids),
                self.bid_mode_id,
                self.order_type_id,
                self.fund_type_id,
                self.delivery_mode_id,
                self.creative_method_id,
            ],
            separators=(",", ":"),
            ensure_ascii=False,
        )


class ConfigOption(V9Model):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=180)
    available: bool = Field(strict=True)
    reason: str | None = Field(default=None, max_length=300)


class FieldCapability(V9Model):
    state: FieldState
    reason: str = Field(min_length=1, max_length=300)
    value_type: ValueType
    required: bool | None = Field(default=None, strict=True)
    options: tuple[ConfigOption, ...] | None = Field(default=None, max_length=500)
    min: FiniteNumber | None = None
    max: FiniteNumber | None = None
    step: FiniteNumber | None = Field(default=None, gt=0)
    precision: int | None = Field(default=None, strict=True, ge=0, le=8)
    unit: Literal["CNY_MINOR", "BEAN", "RATIO"] | None = None
    unit_label: str | None = Field(default=None, max_length=60)
    allowed_min: tuple[FiniteNumber, ...] | None = Field(default=None, max_length=128)
    allowed_max: tuple[FiniteNumber, ...] | None = Field(default=None, max_length=128)
    max_selected: int | None = Field(default=None, strict=True, gt=0, le=1000)
    specs: tuple[Annotated[str, Field(max_length=180)], ...] | None = Field(
        default=None, max_length=100
    )

    @model_validator(mode="after")
    def typed_limits(self) -> Self:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("字段上下限倒置")
        if self.state != "AVAILABLE" and any(
            value is not None
            for value in (
                self.options,
                self.required,
                self.min,
                self.max,
                self.step,
                self.precision,
                self.unit,
                self.allowed_min,
                self.allowed_max,
                self.max_selected,
                self.specs,
            )
        ):
            raise ValueError("未确认可用的字段不能附带可配置选项或限制")
        if self.options is not None and len({o.id for o in self.options}) != len(self.options):
            raise ValueError("枚举标识重复")
        if self.unit in {"CNY_MINOR", "BEAN"} and (
            self.value_type != "INTEGER"
            or self.precision not in {None, 0}
            or any(
                value is not None and not value.is_integer()
                for value in (self.min, self.max, self.step)
            )
        ):
            raise ValueError("金额分/微信豆必须为整数，不能混入元或小数豆")
        if self.unit == "RATIO" and (self.value_type != "DECIMAL" or self.precision is None):
            raise ValueError("比率单位必须对应已核对精度的比率字段")
        bounds = tuple(v for v in (self.min, self.max, self.step) if v is not None)
        bounds += (self.allowed_min or ()) + (self.allowed_max or ())
        if self.value_type == "INTEGER" and any(
            not v.is_integer() or abs(v) > 9007199254740991 for v in bounds
        ):
            raise ValueError("整数边界必须是安全整数")
        return self


class FieldProof(V9Model):
    id: UUID = Field(default_factory=uuid4)
    context: ConfigurationContext
    field_id: FieldId
    capability: FieldCapability
    binding_id: UUID | None = None
    binding_version: int | None = Field(default=None, strict=True, gt=0)
    authorization_id: UUID | None = None
    authorization_version: int | None = Field(default=None, strict=True, gt=0)
    required_scopes: tuple[str, ...] = Field(default=(), max_length=30)
    proof_ref: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,180}$")
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def evidence_required(self) -> Self:
        if self.expires_at <= self.checked_at:
            raise ValueError("字段依据必须有有效窗口")
        if self.capability.state == "AVAILABLE" and (
            self.binding_id is None
            or self.binding_version is None
            or self.authorization_id is None
            or self.authorization_version is None
            or not self.required_scopes
        ):
            raise ValueError("账户可用字段必须绑定实际能力、授权版本与已核对scope")
        if any(not s or len(s) > 80 for s in self.required_scopes):
            raise ValueError("字段scope须为已核对的有界代码")
        if self.capability.state == "PUBLIC" and self.field_id != "name":
            raise ValueError("只有应用草稿名是无平台依据的公共字段")
        return self


class Verification(V9Model):
    catalog_ids: tuple[str, ...]
    path_verified: bool
    application_scope_granted: bool
    account_authorized: bool
    backend_integrated: bool
    request_schema_verified: bool
    execution_verified: bool
    proof_id: str
    checked_at: AwareDatetime


class ConfigurationCapability(V9Model):
    contract_version: Literal["advertising-v9.0"] = "advertising-v9.0"
    source: Literal["API"] = "API"
    tenant_id: str
    context_key: str
    version: str
    checked_at: AwareDatetime
    expires_at: AwareDatetime
    fields: dict[FieldId, FieldCapability]
    authorization: Literal["AUTHORIZED", "UNAUTHORIZED", "FORBIDDEN", "NOT_INTEGRATED"]
    write_channel: Literal["API", "MANUAL", "NONE"]
    write_permission: str
    audience_management: Literal["MANUAL", "PLATFORM_SMART"]
    estimate_supported: bool
    upload_supported: bool
    currency: Literal["CNY", "BEAN"]
    reason: str
    api_version: str
    verification: Verification


class ConfigurationAccount(V9Model):
    id: UUID
    name: str
    business_entity_id: UUID
    shop_ids: tuple[UUID, ...]
    authorization: Literal["AUTHORIZED", "UNAUTHORIZED", "FORBIDDEN", "NOT_INTEGRATED"]
    writable: bool


class ConfigurationShop(V9Model):
    id: UUID
    name: str
    business_entity_id: UUID


class ConfigurationEntity(V9Model):
    id: UUID
    name: str


class ConfigurationCatalog(V9Model):
    contract_version: Literal["advertising-v9.0"] = "advertising-v9.0"
    source: Literal["API"] = "API"
    tenant_id: str
    product: Product
    accounts: tuple[ConfigurationAccount, ...]
    entities: tuple[ConfigurationEntity, ...]
    shops: tuple[ConfigurationShop, ...]
    reason: str
