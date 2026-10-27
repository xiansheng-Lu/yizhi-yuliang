"""受控范围变更；身份哈希和requestId由服务端传入，不接受请求体的角色/租户声明。"""

import json
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session


class ScopeGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    scope_type: Literal["ALL_TENANT", "ENTITY", "SHOP", "ACCOUNT"] = Field(alias="scopeType")
    entity_id: UUID | None = Field(default=None, alias="entityId")
    shop_id: UUID | None = Field(default=None, alias="shopId")
    account_id: UUID | None = Field(default=None, alias="accountId")
    permissions: list[str] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def exact_target(self) -> Self:
        present = {
            name
            for name in ("ENTITY", "SHOP", "ACCOUNT")
            if getattr(
                self, {"ENTITY": "entity_id", "SHOP": "shop_id", "ACCOUNT": "account_id"}[name]
            )
            is not None
        }
        expected = set() if self.scope_type == "ALL_TENANT" else {self.scope_type}
        if present != expected:
            raise ValueError("范围必须精确指定一个匹配类型的对象，ALL_TENANT不带对象")
        return self


class MemberScopeChange(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    expected_version: int = Field(alias="expectedVersion", strict=True, gt=0)
    status: Literal["ACTIVE", "REMOVED"]
    scopes: list[ScopeGrant] = Field(max_length=100)


def change_member_scope(
    session: Session,
    actor_session_hash: str,
    target: UUID,
    change: MemberScopeChange,
    request_id: str,
) -> int:
    scopes = [
        scope.model_dump(mode="json", by_alias=True, exclude_none=True) for scope in change.scopes
    ]
    value = session.execute(
        text(
            "SELECT access.change_member_scope(:actor,:target,:version,:status,"
            "CAST(:scopes AS jsonb),:request_id)"
        ),
        {
            "actor": actor_session_hash,
            "target": target,
            "version": change.expected_version,
            "status": change.status,
            "scopes": json.dumps(scopes),
            "request_id": request_id,
        },
    ).scalar_one()
    return int(value)
