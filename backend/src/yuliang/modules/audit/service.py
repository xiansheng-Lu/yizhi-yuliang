"""追加审计只接收受控字段、UUID与不可逆摘要，不接受自由文本载荷。"""

import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from yuliang.platform.db import VerifiedIdentity
from yuliang.platform.errors import ApiError, ErrorCode

Action = Literal[
    "profile.update",
    "shop.update",
    "member.status",
    "role.grant",
    "role.revoke",
    "scope.change",
    "project.create",
    "conversation.move",
    "message.send",
    "conversation.attach",
    "task.project",
    "platform.request",
]
ObjectType = Literal[
    "user",
    "shop",
    "member",
    "role",
    "project",
    "conversation",
    "message",
    "job",
    "platform_request",
]
ChangedField = Literal["displayName", "name", "status", "permissions", "scope"]


class AuditDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    changed_fields: list[ChangedField] = Field(default_factory=list, max_length=8)
    result: Literal["APPLIED", "DENIED"] = "APPLIED"


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    action: Action
    object_type: ObjectType
    object_id: UUID
    request_id: UUID
    before_hash: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    after_hash: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    detail: AuditDetail = Field(default_factory=AuditDetail)


def record(session: Session, identity: VerifiedIdentity, data: dict[str, Any]) -> int:
    try:
        entry = AuditEntry.model_validate(data)
    except ValidationError:
        raise ValueError("审计只接受受控字段和摘要") from None
    valid = session.execute(
        text("""
      SELECT EXISTS (SELECT 1 FROM memberships WHERE id=:member AND user_id=:user
        AND tenant_id=:tenant AND status='ACTIVE')
    """),
        {"member": identity.membership_id, "user": identity.user_id, "tenant": identity.tenant_id},
    ).scalar_one()
    if not valid:
        raise ApiError(ErrorCode.FORBIDDEN, "审计身份不可用")
    return int(
        session.execute(
            text("""
      INSERT INTO audit_logs(tenant_id,actor_id,action,object_type,object_id,
        before_hash,after_hash,request_id,detail)
      VALUES(:tenant,:actor,:action,:type,:object,:before,:after,:request,CAST(:detail AS jsonb))
      RETURNING id
    """),
            {
                "tenant": identity.tenant_id,
                "actor": identity.user_id,
                "action": entry.action,
                "type": entry.object_type,
                "object": str(entry.object_id),
                "before": entry.before_hash,
                "after": entry.after_hash,
                "request": entry.request_id,
                "detail": json.dumps(entry.detail.model_dump()),
            },
        ).scalar_one()
    )


def by_request(
    session: Session, identity: VerifiedIdentity, request_id: UUID
) -> list[dict[str, Any]]:
    # 关联查询仍通过业务事务与RLS；没有全局认证事件或跨租户通用查询接口。
    return [
        dict(row)
        for row in session.execute(
            text("""
      SELECT id,actor_id,action,object_type,object_id,
        before_hash,after_hash,request_id,detail,created_at
      FROM audit_logs WHERE tenant_id=:tenant AND request_id=:request
      ORDER BY created_at,id LIMIT 100
    """),
            {"tenant": identity.tenant_id, "request": request_id},
        ).mappings()
    ]
