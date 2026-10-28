"""PlatformService上下文；原生标识与内部UUID分开，操作映射须有维护依据。"""

from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from yuliang.modules.configuration.models import Product, V9Model

type ProductLine = Product | Literal["wxshop"]
type Provider = Literal["tencent", "ocean", "xhs", "beans", "wxshop"]
type PlatformResource = Literal[
    "campaigns",
    "promotionOrders",
    "adgroups",
    "creatives",
    "targeting",
    "keywords",
    "reports",
    "funds",
    "orders",
    "products",
    "stock",
    "lives",
    "compass",
    "settlements",
    "aftersales",
    "distribution",
    "audit",
    "decisions",
    "goals",
    "allocations",
    "executions",
]
type EditableField = Literal["name", "budget", "bid", "status"]
type VerificationStage = Literal[
    "directory", "path", "applicationScope", "account", "backend", "requestSchema", "execution"
]


class OperationDeclaration(V9Model):
    id: UUID = Field(default_factory=uuid4)
    binding_id: UUID
    binding_version: int = Field(strict=True, gt=0)
    resource: PlatformResource
    editable_fields: tuple[EditableField, ...] = Field(default=(), max_length=4)
    authorization_id: UUID | None = None
    authorization_version: int | None = Field(default=None, strict=True, gt=0)
    required_scopes: tuple[str, ...] = Field(default=(), max_length=30)
    proof_ref: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,180}$")
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def actual_evidence(self) -> Self:
        if self.expires_at <= self.checked_at:
            raise ValueError("资源操作依据必须有有效期")
        if len(set(self.editable_fields)) != len(self.editable_fields):
            raise ValueError("可编辑字段不能重复")
        if self.authorization_id is None and (
            self.authorization_version is not None or self.required_scopes
        ):
            raise ValueError("不得为不存在的授权附加版本或scope")
        if self.authorization_id is not None and (
            self.authorization_version is None or not self.required_scopes
        ):
            raise ValueError("授权依据须绑定实际版本及scope")
        if any(not scope or len(scope) > 80 for scope in self.required_scopes):
            raise ValueError("scope须为实际有界代码")
        return self


class StageEvidence(V9Model):
    verified: bool
    proof_id: str | None
    checked_at: AwareDatetime | None
    reason: str


class IntegrationOperation(V9Model):
    catalog_id: str
    resource: PlatformResource
    kind: Literal["READ", "WRITE"]
    account_ids: tuple[UUID, ...]
    evidence: dict[VerificationStage, StageEvidence]
    permission: str
    editable_fields: tuple[EditableField, ...]


class IntegrationAccount(V9Model):
    id: UUID
    native_id: str
    name: str
    business_entity_id: UUID
    provider: Provider
    product_line: ProductLine
    authorization: Literal["AUTHORIZED", "UNAUTHORIZED", "FORBIDDEN", "NOT_INTEGRATED"]


class IntegrationShop(V9Model):
    id: UUID
    name: str
    business_entity_id: UUID
    provider: str


class IntegrationEntity(V9Model):
    id: UUID
    name: str


class IntegrationLink(V9Model):
    account_id: UUID
    shop_id: UUID
    state: Literal["BOUND", "AUTHORIZED"]
    checked_at: AwareDatetime


class IntegrationContext(V9Model):
    contract_version: Literal["platform-workspace-v9.0"] = "platform-workspace-v9.0"
    source: Literal["API"] = "API"
    tenant_id: str
    product_line: ProductLine
    checked_at: AwareDatetime
    expires_at: AwareDatetime
    entities: tuple[IntegrationEntity, ...]
    shops: tuple[IntegrationShop, ...]
    accounts: tuple[IntegrationAccount, ...]
    links: tuple[IntegrationLink, ...]
    operations: tuple[IntegrationOperation, ...]
