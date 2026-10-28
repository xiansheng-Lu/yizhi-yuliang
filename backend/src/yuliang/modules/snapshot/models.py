"""冻结前端ReportingScope；all表示当前授权集合，不表示全租户权限。"""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONNECTORS = {"adq", "wxpromo", "beans", "wxshop", "qianchuan", "douyinshop", "juguang", "redshop"}
PAIR_CONNECTORS = {
    ("tencent", "adq"): "adq",
    ("tencent", "shop"): "wxpromo",
    ("beans", "beans"): "beans",
    ("wxshop", "wxshop"): "wxshop",
    ("ocean", "qianchuan"): "qianchuan",
    ("ocean", "douyinshop"): "douyinshop",
    ("xhs", "juguang"): "juguang",
    ("xhs", "redshop"): "redshop",
}
COLLECTIONS = (
    "tenants",
    "shops",
    "adAccounts",
    "liveSessions",
    "plans",
    "products",
    "materials",
    "tasks",
    "riskPolicies",
    "replaySegments",
    "replayReports",
    "connectors",
)


class ReportingScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    shop_id: str = Field(max_length=36)
    live_session_id: str = Field(max_length=36)
    connector_id: str = Field(max_length=20)
    date: date
    timezone: Literal["Asia/Shanghai"]

    @field_validator("shop_id", "live_session_id")
    @classmethod
    def identifier(cls, value: str) -> str:
        if value != "all":
            return str(UUID(value))
        return value

    @field_validator("connector_id")
    @classmethod
    def connector(cls, value: str) -> str:
        if value != "all" and value not in CONNECTORS:
            raise ValueError("未知连接器")
        return value


def initial_scope() -> ReportingScope:
    return ReportingScope(
        shop_id="all",
        live_session_id="all",
        connector_id="all",
        date=datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).date(),
        timezone="Asia/Shanghai",
    )
