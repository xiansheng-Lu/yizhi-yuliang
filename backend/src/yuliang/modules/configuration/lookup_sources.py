"""资源适用上下文必须由独立维护依据冻结，不能由客户端关键词生成。"""

import hashlib
from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from yuliang.modules.capability.service import maintenance
from yuliang.modules.configuration.models import ConfigurationContext, V9Model


class LookupSourceProof(V9Model):
    id: UUID = Field(default_factory=uuid4)
    context: ConfigurationContext
    binding_version: int = Field(strict=True, gt=0)
    binding_id: UUID
    authorization_id: UUID
    authorization_version: int = Field(strict=True, gt=0)
    required_scopes: tuple[str, ...] = Field(min_length=1, max_length=20)
    kind: Literal["audiences"] = "audiences"
    eligibility: Literal["ONLINE_SUCCESS_VERIFIED_FOR_EXACT_CONTEXT"]
    max_age_seconds: int = Field(strict=True, ge=1, le=86400)
    proof_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,179}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checked_by: str = Field(min_length=1, max_length=120)
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (
            self.context.product != "adq"
            or not self.context.shop_id
            or self.expires_at <= self.checked_at
            or len(set(self.required_scopes)) != len(self.required_scopes)
            or any(not s or len(s) > 80 for s in self.required_scopes)
        ):
            raise ValueError("人群来源须明确ADQ店铺上下文、scope及有效期")
        return self


def register_lookup_source(
    connection: Connection, tenant_id: int, proof: LookupSourceProof
) -> UUID:
    maintenance(connection)
    proof = LookupSourceProof.model_validate(proof.model_dump())
    if not proof.checked_at <= datetime.now(UTC) < proof.expires_at:
        raise ValueError("资源依据已过期或来自未来")
    row = connection.execute(
        text("""SELECT connector_id,provider,product_line,operation_kind,http_method,
      normalized_path,account_id,revision
      FROM capability_bindings WHERE tenant_id=:tenant AND id=:id"""),
        dict(tenant=tenant_id, id=proof.binding_id),
    ).first()
    if row is None or tuple(row) != (
        "adq",
        "tencent",
        "adq",
        "READ",
        "GET",
        "/v3.0/custom_audiences/get",
        proof.context.account_id,
        proof.binding_version,
    ):
        raise ValueError("资源来源绑定不是ADQ自定义人群只读")
    old = connection.execute(
        text("SELECT payload,active FROM resource_lookup_contexts WHERE id=:id"), dict(id=proof.id)
    ).first()
    if old is not None:
        if old.payload != proof.model_dump(mode="json", by_alias=True) or not old.active:
            raise ValueError("资源依据ID不可覆盖或复活")
        return proof.id
    connection.execute(
        text(
            "UPDATE resource_lookup_contexts SET active=false "
            "WHERE tenant_id=:tenant AND context_key=:key AND active"
        ),
        dict(tenant=tenant_id, key=proof.context.context_key),
    )
    connection.execute(
        text("""INSERT INTO resource_lookup_contexts
      (id,tenant_id,account_id,binding_id,context_key,context_digest,payload,checked_at,expires_at)
      VALUES(:id,:tenant,:account,:binding,:key,:digest,
      CAST(:payload AS jsonb),:checked,:expires)"""),
        dict(
            id=proof.id,
            tenant=tenant_id,
            account=proof.context.account_id,
            binding=proof.binding_id,
            key=proof.context.context_key,
            digest=hashlib.sha256(proof.context.context_key.encode()).hexdigest(),
            payload=proof.model_dump_json(by_alias=True),
            checked=proof.checked_at,
            expires=proof.expires_at,
        ),
    )
    return proof.id


def lookup_source(session: Session, context: ConfigurationContext) -> LookupSourceProof | None:
    row = session.execute(
        text("""SELECT id,payload,checked_at,expires_at FROM resource_lookup_contexts
      WHERE account_id=:account AND context_key=:key AND active
      AND checked_at<=clock_timestamp() AND expires_at>clock_timestamp()"""),
        dict(account=context.account_id, key=context.context_key),
    ).first()
    if row is None:
        return None
    proof = LookupSourceProof.model_validate(row.payload)
    return (
        proof
        if (proof.id, proof.context.context_key, proof.checked_at, proof.expires_at)
        == (row.id, context.context_key, row.checked_at, row.expires_at)
        else None
    )
