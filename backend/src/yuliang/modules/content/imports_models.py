"""IndexedDB content-v1仅元数据；不接收文件路径、二进制或客户端文件证明。"""

import hashlib
import json
from typing import Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from yuliang.modules.content.models import Document


class ImportOwner(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    mode: Literal["API"]
    tenantId: str
    userId: str
    shopId: str


class LocalAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    id: str
    name: str
    kind: Literal["image", "video", "audio", "text"]
    productId: str
    documentId: str | None
    mime: str
    size: int
    duration: float
    createdAt: str
    text: str | None = None
    accountLinks: list[dict[str, object]] = Field(default_factory=list, max_length=100)


class LocalState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    schema_version: Literal[1] = Field(alias="schema")
    revision: int = Field(ge=0, le=9223372036854775807)
    documents: list[Document] = Field(max_length=500)
    assets: list[LocalAsset] = Field(max_length=1000)

    @model_validator(mode="after")
    def unique(self) -> Self:
        for rows in (self.documents, self.assets):
            if len({v.id for v in rows}) != len(rows):
                raise ValueError("本地ID重复，禁止覆盖条目")
        if sum(len(d.model_dump_json()) for d in self.documents) > 8_000_000:
            raise ValueError("导入元数据过大")
        return self


class ImportBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    importKey: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
    owner: ImportOwner
    state: LocalState
    productMap: dict[str, str] = Field(max_length=500)
    assetMap: dict[str, str] = Field(max_length=1000)
    decisions: dict[str, Literal["COPY"]] = Field(default_factory=dict, max_length=500)

    @model_validator(mode="after")
    def bounded_manifest(self) -> Self:
        if len(self.model_dump_json()) > 12_000_000:
            raise ValueError("导入元数据清单过大")
        products = {d.productId for d in self.state.documents if d.productId}
        assets = {a.id for a in self.state.assets}
        documents = {d.id for d in self.state.documents}
        if (
            not self.productMap.keys() <= products
            or not self.assetMap.keys() <= assets
            or not self.decisions.keys() <= documents
        ):
            raise ValueError("映射或冲突决策不属于本地清单")
        return self

    def digest(self) -> str:
        raw = json.dumps(
            self.model_dump(by_alias=True),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def cloud_id(self, tenant: int, user: UUID, shop: UUID, local_id: str) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            json.dumps(
                [
                    "yuliang-content-import-v1",
                    tenant,
                    str(user),
                    str(shop),
                    self.importKey,
                    local_id,
                ],
                separators=(",", ":"),
            ),
        )
