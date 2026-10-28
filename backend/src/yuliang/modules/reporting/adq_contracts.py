"""报表事实口径由独立维护依据确认；可见账户关联不证明整账户业绩归属。"""

from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from yuliang.modules.capability.service import maintenance
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.reporting.models import Frozen

ReportKind = Literal["DAILY", "HOURLY"]
TimeLine = Literal["REQUEST_TIME", "REPORTING_TIME", "ACTIVE_TIME"]
MoneyUnit = Literal["CNY_MINOR", "CNY_YUAN_INTEGER"]


class ReportContract(Frozen):
    id: UUID = Field(default_factory=uuid4)
    account_id: UUID
    native_account_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    binding_id: UUID
    shop_id: UUID
    kind: ReportKind
    time_line: TimeLine
    attribution: Literal["VERIFIED_WHOLE_ACCOUNT_EXCLUSIVE_SHOP"]
    timezone: Literal["Asia/Shanghai"]
    attribution_window: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,59}$")
    refund_policy: Literal["GROSS", "NET_OF_REFUND", "EXCLUDE_REFUND", "NOT_APPLICABLE"]
    cost_unit: MoneyUnit | None = None
    order_amount_unit: MoneyUnit | None = None
    order_amount_is_gmv: bool = Field(default=False, strict=True)
    hour_encoding: Literal["HOUR_OF_DAY"] | None = None
    proof_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,179}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checked_by: str = Field(min_length=1, max_length=120)
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (
            self.expires_at <= self.checked_at
            or self.attribution_window.upper() in {"UNKNOWN", "UNCONFIRMED"}
            or self.kind == "DAILY"
            and self.hour_encoding is not None
            or self.order_amount_is_gmv
            and self.order_amount_unit is None
        ):
            raise ValueError("报表口径依据需明确有效期、归因、单位和适用粒度")
        return self


def register_report_contract(connection: Connection, tenant_id: int, proof: ReportContract) -> UUID:
    maintenance(connection)
    proof = ReportContract.model_validate(proof.model_dump())
    if not proof.checked_at <= datetime.now(UTC) < proof.expires_at:
        raise ValueError("报表口径依据来自未来或已过期")
    row = connection.execute(
        text("""SELECT b.connector_id,b.provider,b.product_line,b.operation_kind,
        b.http_method,b.normalized_path,a.native_id FROM capability_bindings b
        JOIN ad_accounts a ON (a.tenant_id,a.id)=(b.tenant_id,b.account_id)
        JOIN entity_links l ON (l.tenant_id,l.account_id)=(b.tenant_id,b.account_id)
        WHERE b.tenant_id=:tenant AND b.id=:binding AND b.account_id=:account
        AND l.shop_id=:shop"""),
        dict(
            tenant=tenant_id, binding=proof.binding_id, account=proof.account_id, shop=proof.shop_id
        ),
    ).first()
    path = "/v1.3/daily_reports/get" if proof.kind == "DAILY" else "/v1.3/hourly_reports/get"
    if row is None or tuple(row) != (
        "adq",
        "tencent",
        "adq",
        "READ",
        "GET",
        path,
        proof.native_account_id,
    ):
        raise ValueError("报表口径只能绑定对应ADQ整账户只读报表")
    old = connection.execute(
        text("SELECT payload,active FROM adq_report_contracts WHERE id=:id"), dict(id=proof.id)
    ).first()
    if old is not None:
        if old.payload != proof.model_dump(mode="json") or not old.active:
            raise ValueError("既有报表口径ID不可覆盖或复活")
        return proof.id
    connection.execute(
        text("""UPDATE adq_report_contracts SET active=false
        WHERE tenant_id=:tenant AND binding_id=:binding AND active"""),
        dict(tenant=tenant_id, binding=proof.binding_id),
    )
    connection.execute(
        text("""INSERT INTO adq_report_contracts(id,tenant_id,account_id,binding_id,shop_id,
        kind,payload,checked_at,expires_at) VALUES(:id,:tenant,:account,:binding,:shop,
        :kind,CAST(:payload AS jsonb),:checked,:expires)"""),
        dict(
            id=proof.id,
            tenant=tenant_id,
            account=proof.account_id,
            binding=proof.binding_id,
            shop=proof.shop_id,
            kind=proof.kind,
            payload=proof.model_dump_json(),
            checked=proof.checked_at,
            expires=proof.expires_at,
        ),
    )
    return proof.id


def current_report_contract(
    session: Session, ctx: GatewayContext, kind: ReportKind, time_line: TimeLine
) -> ReportContract | None:
    row = session.execute(
        text("""SELECT payload,checked_at,expires_at FROM adq_report_contracts
        WHERE binding_id=:binding AND account_id=:account AND shop_id=:shop AND kind=:kind
        AND active AND checked_at<=clock_timestamp() AND expires_at>clock_timestamp()"""),
        dict(binding=ctx.binding_id, account=ctx.account_id, shop=ctx.shop_id, kind=kind),
    ).first()
    if row is None:
        return None
    proof = ReportContract.model_validate(row.payload)
    if (proof.checked_at, proof.expires_at) != (row.checked_at, row.expires_at):
        return None
    if (proof.account_id, proof.native_account_id, proof.binding_id, proof.shop_id, proof.kind) != (
        ctx.account_id,
        ctx.native_account_id,
        ctx.binding_id,
        ctx.shop_id,
        kind,
    ):
        return None
    return proof if proof.time_line == time_line else None
