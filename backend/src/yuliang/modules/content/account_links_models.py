"""只接受意图目标和并发版本；身份/名称/原生ID/时间由服务器产生。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ProductLine = Literal["adq", "shop", "beans", "qianchuan", "juguang"]


class LinkChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    productLine: ProductLine
    accountId: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    expectedVersion: int = Field(strict=True, ge=0, le=2147483646)

    @model_validator(mode="after")
    def identifier(self) -> "LinkChange":
        from uuid import UUID

        UUID(self.accountId)
        return self


class LinkCancel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    expectedVersion: int = Field(strict=True, ge=1, le=2147483646)
