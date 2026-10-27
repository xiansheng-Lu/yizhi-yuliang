"""六方法端口及服务端上下文；不把受理回执当作已回读成功。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, model_validator

from yuliang.modules.capability.models import CONNECTOR_CONTEXTS, Evidence
from yuliang.platform.db import VerifiedIdentity


def adapter_product_line(connector_id: str, wire_product: str) -> str:
    expected = CONNECTOR_CONTEXTS.get(connector_id)
    if expected is None or expected[1] != wire_product:
        raise ValueError("连接器与产品线不匹配")
    # V9实际传输枚举为shop；后端适配器目录沿用架构命名wxpromo，显式在边界映射。
    return "wxpromo" if connector_id == "wxpromo" else wire_product


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObjectRef(Frozen):
    tenant_id: int = Field(strict=True, gt=0)
    account_id: UUID
    connector_id: str
    provider: str
    product_line: str
    native_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

    @model_validator(mode="after")
    def bound_product(self) -> "ObjectRef":
        if CONNECTOR_CONTEXTS.get(self.connector_id) != (self.provider, self.product_line):
            raise ValueError("对象引用产品线上下文不匹配")
        return self


@dataclass(frozen=True)
class GatewayContext:
    """仅限服务端已核对身份范围；仍须另过能力、额度、审批和执行守卫。"""

    identity: VerifiedIdentity
    shop_id: UUID
    account_id: UUID
    native_account_id: str
    connector_id: str
    provider: str
    product_line: str
    authorization_id: UUID
    request_id: UUID
    binding_id: UUID
    capability_version: int
    catalog_id: str
    catalog_sha256: str

    def __post_init__(self) -> None:
        if CONNECTOR_CONTEXTS.get(self.connector_id) != (self.provider, self.product_line):
            raise ValueError("连接器上下文不匹配")
        if type(self.capability_version) is not int or self.capability_version <= 0:
            raise ValueError("需要已核对能力版本")
        self.assert_ref(
            ObjectRef(
                tenant_id=self.identity.tenant_id,
                account_id=self.account_id,
                connector_id=self.connector_id,
                provider=self.provider,
                product_line=self.product_line,
                native_id=self.native_account_id,
            )
        )
        ResourceQuery(operation="context", catalog_id=self.catalog_id, parameters={})
        if len(self.catalog_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.catalog_sha256
        ):
            raise ValueError("需要已核对目录摘要")

    @property
    def adapter_product(self) -> str:
        return adapter_product_line(self.connector_id, self.product_line)

    def assert_ref(self, value: ObjectRef) -> None:
        if (
            value.tenant_id,
            value.account_id,
            value.connector_id,
            value.provider,
            value.product_line,
        ) != (
            self.identity.tenant_id,
            self.account_id,
            self.connector_id,
            self.provider,
            self.product_line,
        ):
            raise ValueError("目标不属于当前租户、账户或产品线")


class ResourceQuery(Frozen):
    operation: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,79}$")
    catalog_id: str = Field(pattern=r"^[0-9a-f]{20}$")
    parameters: dict[str, Any]
    target: ObjectRef | None = None


class PlatformChange(ResourceQuery):
    frozen_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlatformCommand(PlatformChange):
    execution_id: UUID
    attempt_id: UUID
    idempotency_key: UUID


class PlatformResourcePage(Frozen):
    records: tuple[dict[str, Any], ...]
    next_cursor: str | None
    source_at: AwareDatetime | None
    fetched_at: AwareDatetime


class ChangePreview(Frozen):
    normalized_changes: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    requires_approval: bool
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlatformReceipt(Frozen):
    disposition: Literal["ACCEPTED", "REJECTED", "UNKNOWN"]
    execution_id: UUID
    attempt_id: UUID
    external_id: str | None
    trace_id: str | None


class ReadbackReference(Frozen):
    execution_id: UUID
    attempt_id: UUID
    target: ObjectRef
    expected_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReadbackValue(Frozen):
    verification: Literal["MATCHED", "MISMATCH", "UNKNOWN"]
    observed: dict[str, Any] | None
    source_at: AwareDatetime | None
    fetched_at: AwareDatetime


class AuthorizationRef(ObjectRef):
    authorization_id: UUID
    authorization_version: int = Field(strict=True, gt=0)


class TokenSet(Frozen):
    access_token: SecretStr = Field(repr=False)
    refresh_token: SecretStr | None = Field(repr=False)
    expires_at: datetime
    scopes: tuple[str, ...]

    @model_validator(mode="after")
    def aware_expiry(self) -> "TokenSet":
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("令牌到期时间必须带时区")
        return self


class AssertionResult(Frozen):
    binding_id: UUID
    capability_version: int = Field(strict=True, gt=0)
    assertions: tuple[Evidence, ...]
    unavailable_reason: str | None


@runtime_checkable
class PlatformGateway(Protocol):
    async def read(self, ctx: GatewayContext, query: ResourceQuery) -> PlatformResourcePage: ...
    async def preview(self, ctx: GatewayContext, change: PlatformChange) -> ChangePreview: ...
    async def submit(self, ctx: GatewayContext, command: PlatformCommand) -> PlatformReceipt: ...
    async def readback(self, ctx: GatewayContext, ref: ReadbackReference) -> ReadbackValue: ...
    async def refresh_token(self, auth: AuthorizationRef) -> TokenSet: ...
    async def capability_probe(self, ctx: GatewayContext) -> AssertionResult: ...
