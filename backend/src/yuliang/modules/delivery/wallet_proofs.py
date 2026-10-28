"""钱包财务含义和金额单位需独立来源依据，不从余额字段名或资金类型猜测。"""

from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, text

from yuliang.modules.capability.service import maintenance

WalletMetric = Literal["BALANCE", "IN_TRANSIT", "UNSETTLED", "CREDIT", "REALTIME_COST"]
METRICS: tuple[WalletMetric, ...] = (
    "BALANCE",
    "IN_TRANSIT",
    "UNSETTLED",
    "CREDIT",
    "REALTIME_COST",
)


class WalletMeasureProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    id: UUID = Field(default_factory=uuid4)
    source_id: UUID
    metric: WalletMetric
    source_field: Literal["native_balance", "native_realtime_cost"]
    fund_types: tuple[str, ...] = Field(min_length=1, max_length=1000)
    currency: Literal["CNY"]
    native_unit: Literal["CNY_MINOR", "CNY_YUAN_INTEGER"]
    aggregation: Literal["SUM_VERIFIED_DISTINCT_FUND_TYPES"]
    proof_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,179}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checked_by: str = Field(min_length=1, max_length=120)
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def coherent(self) -> Self:
        import re

        if (
            self.expires_at <= self.checked_at
            or (self.metric == "REALTIME_COST") != (self.source_field == "native_realtime_cost")
            or len(set(self.fund_types)) != len(self.fund_types)
            or any(not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", v) for v in self.fund_types)
        ):
            raise ValueError("口径依据需要独立资金类型、明确单位和有效时间窗口")
        return self


def register_wallet_proof(
    connection: Connection, tenant_id: int, proof: WalletMeasureProof
) -> UUID:
    maintenance(connection)
    proof = WalletMeasureProof.model_validate(proof.model_dump())
    if not proof.checked_at <= datetime.now(UTC) < proof.expires_at:
        raise ValueError("口径依据来自未来或已过期")
    row = connection.execute(
        text("""SELECT b.connector_id,b.provider,b.product_line,
      b.operation_kind,b.http_method,b.normalized_path,s.resource_kind FROM sync_sources s
      JOIN capability_bindings b ON (b.tenant_id,b.id)=(s.tenant_id,s.binding_id)
      WHERE s.tenant_id=:tenant AND s.id=:source"""),
        dict(tenant=tenant_id, source=proof.source_id),
    ).first()
    if row is None or tuple(row) != (
        "adq",
        "tencent",
        "adq",
        "READ",
        "GET",
        "/v1.3/funds/get",
        "adq.finance.funds",
    ):
        raise ValueError("财务口径依据不能指向小店、微信豆、广告计划预算或其他接口")
    old = connection.execute(
        text("SELECT payload FROM adq_wallet_measure_proofs WHERE id=:id"), dict(id=proof.id)
    ).scalar_one_or_none()
    if old is not None:
        if old != proof.model_dump(mode="json"):
            raise ValueError("同一财务依据ID不能覆盖来源、范围或单位")
        return proof.id
    connection.execute(
        text("""INSERT INTO adq_wallet_measure_proofs(id,tenant_id,source_id,metric,
      payload,checked_at,expires_at) VALUES(:id,:tenant,:source,:metric,CAST(:payload AS jsonb),
      :checked,:expires)"""),
        dict(
            id=proof.id,
            tenant=tenant_id,
            source=proof.source_id,
            metric=proof.metric,
            payload=proof.model_dump_json(),
            checked=proof.checked_at,
            expires=proof.expires_at,
        ),
    )
    return proof.id
