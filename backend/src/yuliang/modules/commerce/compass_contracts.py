"""独立维护的罗盘口径依据；业务运行身份不得自授单位或经营归属。"""

from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from yuliang.modules.capability.service import maintenance
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.reporting.models import Frozen


class CompassContract(Frozen):
    id: UUID = Field(default_factory=uuid4)
    account_id: UUID
    native_account_id: str = Field(min_length=1, max_length=128)
    binding_id: UUID
    shop_id: UUID
    attribution: Literal["VERIFIED_WHOLE_SHOP"]
    timezone: Literal["Asia/Shanghai"]
    money_unit: Literal["CNY_MINOR", "CNY_YUAN_INTEGER"]
    refund_policy: Literal["GROSS", "NET_OF_REFUND", "EXCLUDE_REFUND"]
    dedup_policy: Literal["PLATFORM_DEDUPLICATED_ORDERS"]
    definition_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,59}$")
    pay_gmv_is_business_gmv: Literal[True]
    proof_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,179}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checked_by: str = Field(min_length=1, max_length=120)
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.expires_at <= self.checked_at or self.definition_key.upper() in {
            "UNKNOWN",
            "UNCONFIRMED",
        }:
            raise ValueError("经营事实必须有明确口径与有效期")
        return self


def register_compass_contract(
    connection: Connection, tenant_id: int, proof: CompassContract
) -> UUID:
    maintenance(connection)
    proof = CompassContract.model_validate(proof.model_dump())
    if not proof.checked_at <= datetime.now(UTC) < proof.expires_at:
        raise ValueError("口径依据已过期或来自未来")
    row = connection.execute(
        text("""SELECT b.connector_id,b.provider,b.product_line,b.operation_kind,b.http_method,
      b.normalized_path,a.native_id
      FROM capability_bindings b JOIN ad_accounts a ON (a.tenant_id,a.id)=(b.tenant_id,b.account_id)
      JOIN entity_links l ON (l.tenant_id,l.account_id)=(b.tenant_id,b.account_id)
      WHERE b.tenant_id=:tenant AND b.id=:binding AND b.account_id=:account AND l.shop_id=:shop"""),
        dict(
            tenant=tenant_id, binding=proof.binding_id, account=proof.account_id, shop=proof.shop_id
        ),
    ).first()
    if row is None or tuple(row) != (
        "wxshop",
        "wxshop",
        "wxshop",
        "READ",
        "POST",
        "/channels/ec/compass/shop/overall/get",
        proof.native_account_id,
    ):
        raise ValueError("口径依据只能绑定指定小店经营只读来源")
    links = (
        connection.execute(
            text(
                "SELECT shop_id FROM entity_links WHERE tenant_id=:tenant AND account_id=:account"
            ),
            dict(tenant=tenant_id, account=proof.account_id),
        )
        .scalars()
        .all()
    )
    if links != [proof.shop_id]:
        raise ValueError("全店经营归属必须唯一核验")
    old = connection.execute(
        text("SELECT payload,active FROM shop_compass_contracts WHERE id=:id"), dict(id=proof.id)
    ).first()
    if old is not None:
        if old.payload != proof.model_dump(mode="json") or not old.active:
            raise ValueError("口径ID不可覆盖或复活")
        return proof.id
    connection.execute(
        text(
            "UPDATE shop_compass_contracts SET active=false "
            "WHERE tenant_id=:tenant AND binding_id=:binding AND active"
        ),
        dict(tenant=tenant_id, binding=proof.binding_id),
    )
    connection.execute(
        text("""INSERT INTO shop_compass_contracts(id,tenant_id,account_id,binding_id,
      shop_id,payload,checked_at,expires_at)
      VALUES(:id,:tenant,:account,:binding,:shop,CAST(:payload AS jsonb),:checked,:expires)"""),
        dict(
            id=proof.id,
            tenant=tenant_id,
            account=proof.account_id,
            binding=proof.binding_id,
            shop=proof.shop_id,
            payload=proof.model_dump_json(),
            checked=proof.checked_at,
            expires=proof.expires_at,
        ),
    )
    return proof.id


def current_compass_contract(session: Session, ctx: GatewayContext) -> CompassContract | None:
    row = session.execute(
        text(
            """SELECT payload,checked_at,expires_at FROM shop_compass_contracts
        WHERE binding_id=:binding AND account_id=:account AND shop_id=:shop
        AND active AND checked_at<=clock_timestamp() AND expires_at>clock_timestamp()"""
        ),
        dict(binding=ctx.binding_id, account=ctx.account_id, shop=ctx.shop_id),
    ).first()
    if row is None:
        return None
    proof = CompassContract.model_validate(row.payload)
    if (
        proof.account_id,
        proof.native_account_id,
        proof.binding_id,
        proof.shop_id,
        proof.checked_at,
        proof.expires_at,
    ) != (
        ctx.account_id,
        ctx.native_account_id,
        ctx.binding_id,
        ctx.shop_id,
        row.checked_at,
        row.expires_at,
    ):
        return None
    links = (
        session.execute(
            text("SELECT shop_id FROM entity_links WHERE account_id=:account"),
            dict(account=ctx.account_id),
        )
        .scalars()
        .all()
    )
    return proof if links == [ctx.shop_id] else None
