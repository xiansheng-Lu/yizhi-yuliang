"""独立SDK来源只确认固定只读路径；不授予账户、scope或执行能力。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from yuliang.modules.capability.models import Binding

SDK_REVISION: Literal["81d6cc04b58d7d5a5ec9ad0137e43b3b6c52fde9"] = (
    "81d6cc04b58d7d5a5ec9ad0137e43b3b6c52fde9"
)
ProfileName = Literal[
    "ADQ_CAMPAIGNS_V1_3_READ",
    "ADQ_FUNDS_V1_3_READ",
    "ADQ_DAILY_REPORTS_V1_3_READ",
    "ADQ_HOURLY_REPORTS_V1_3_READ",
]


@dataclass(frozen=True)
class RouteProfile:
    native_path: str
    path: str
    source_file: str
    source_sha256: str


PROFILES = {
    "ADQ_CAMPAIGNS_V1_3_READ": RouteProfile(
        "campaigns/get",
        "/v1.3/campaigns/get",
        "pkg/api/api_campaigns.go",
        "9c1787c3601621ad5a7829064d209cebc21c53d7c90f758df740eb821687985e",
    ),
    "ADQ_FUNDS_V1_3_READ": RouteProfile(
        "funds/get",
        "/v1.3/funds/get",
        "pkg/api/api_funds.go",
        "f8409059915f1bf6d4ef315235c1c011da6a063d47359a862e7d36f31567dce6",
    ),
    "ADQ_DAILY_REPORTS_V1_3_READ": RouteProfile(
        "daily_reports/get",
        "/v1.3/daily_reports/get",
        "pkg/api/api_daily_reports.go",
        "ec08ac4d0440ca64207799c75e74934fe463fb8e31276e3897f33d135286faa1",
    ),
    "ADQ_HOURLY_REPORTS_V1_3_READ": RouteProfile(
        "hourly_reports/get",
        "/v1.3/hourly_reports/get",
        "pkg/api/api_hourly_reports.go",
        "1e976e7e34600ea8186bac9877e9eb466dc271d8303d52c4731fc924867fc1d0",
    ),
}


class ReadRouteSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    id: UUID = Field(default_factory=uuid4)
    profile: ProfileName
    sdk_revision: Literal["81d6cc04b58d7d5a5ec9ad0137e43b3b6c52fde9"] = SDK_REVISION
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checked_by: str = Field(min_length=1, max_length=120)
    checked_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def known_source(self) -> Self:
        if self.source_sha256 != PROFILES[self.profile].source_sha256:
            raise ValueError("SDK来源指纹与固定只读协议不一致")
        if self.expires_at <= self.checked_at:
            raise ValueError("SDK路径依据必须有有效窗口")
        return self

    @property
    def proof_ref(self) -> str:
        return f"official-sdk/{SDK_REVISION}/{PROFILES[self.profile].source_file}"


def validate_source(
    binding: Binding, source: ReadRouteSource, record: dict[str, Any], now: datetime
) -> None:
    source = ReadRouteSource.model_validate(source.model_dump())
    profile = PROFILES[source.profile]
    if (
        binding.connector_id,
        binding.provider,
        binding.product_line,
        binding.operation_kind,
        binding.http_method,
        binding.normalized_path,
    ) != ("adq", "tencent", "adq", "READ", "GET", profile.path):
        raise ValueError("SDK来源仅适用于对应ADQ只读接口，不能替换产品、动作或方法")
    if record.get("nativePath") != profile.native_path:
        raise ValueError("SDK来源与原始目录条目不是同一接口")
    if not source.checked_at <= now < source.expires_at:
        raise ValueError("SDK路径依据来自未来或已过期")


def store_source(connection: Connection, binding: Binding, source: ReadRouteSource) -> None:
    connection.execute(
        text("""INSERT INTO capability_route_sources(
      id,tenant_id,binding_id,account_id,profile,payload,checked_at,expires_at)
      VALUES(:id,:tenant,:binding,:account,:profile,CAST(:payload AS jsonb),:checked,:expires)"""),
        {
            "id": source.id,
            "tenant": binding.tenant_id,
            "binding": binding.id,
            "account": binding.account_id,
            "profile": source.profile,
            "payload": source.model_dump_json(),
            "checked": source.checked_at,
            "expires": source.expires_at,
        },
    )


def current_source(
    session: Session | Connection, binding: Binding, now: datetime
) -> ReadRouteSource | None:
    value = session.execute(
        text("""SELECT r.payload,r.checked_at,r.expires_at,c.record_json
      FROM capability_route_sources r
      JOIN endpoint_catalog c ON c.catalog_sha256=:sha AND c.catalog_id=:catalog
       AND c.provider=:provider
      WHERE r.tenant_id=:tenant AND r.binding_id=:binding AND r.account_id=:account"""),
        {
            "sha": binding.catalog_sha256,
            "catalog": binding.catalog_id,
            "provider": binding.provider,
            "tenant": binding.tenant_id,
            "binding": binding.id,
            "account": binding.account_id,
        },
    ).first()
    if value is None:
        return None
    try:
        source = ReadRouteSource.model_validate(value.payload)
        if (source.checked_at, source.expires_at) != (value.checked_at, value.expires_at):
            return None
        validate_source(binding, source, value.record_json, now)
    except ValueError:
        return None
    return source
