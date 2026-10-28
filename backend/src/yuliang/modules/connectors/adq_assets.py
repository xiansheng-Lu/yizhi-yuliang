"""固定原生层级的账户/计划只读镜像，不推定金额单位、店铺归属或写能力。"""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.orm import Session

from yuliang.modules.connectors.adq_paging import verify_adq_page
from yuliang.modules.connectors.adq_read_scope import require_adq_read
from yuliang.modules.connectors.base import GatewayContext
from yuliang.modules.connectors.governance import CallDeferred
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import HttpExchange, OperationSpec
from yuliang.modules.connectors.native_reads import decode_verdict
from yuliang.modules.connectors.redaction import ArchivePolicy
from yuliang.modules.connectors.sync_store import (
    MirrorRecord,
    PagePermit,
    SyncConflict,
    SyncLease,
    SyncPage,
    SyncStore,
)
from yuliang.platform.db import business_transaction
from yuliang.platform.errors import ApiError, ErrorCode

AssetKind = Literal["ACCOUNT", "CAMPAIGN"]
FIELDS: dict[AssetKind, tuple[str, ...]] = {
    "ACCOUNT": ("account_id", "business_alias", "system_status", "daily_budget"),
    "CAMPAIGN": ("campaign_id", "campaign_name", "configured_status", "daily_budget"),
}
MIRROR_POLICY = ArchivePolicy(
    fields=frozenset(
        {
            "native_id",
            "native_status",
            "native_daily_budget",
            "protocol_version",
            "kind",
            "budget_unit",
            "currency",
            "budget_minor",
            "target_binding",
        }
    )
)


class AdqAssetParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    fields: list[str]
    page: int = Field(default=1, strict=True, ge=1, le=100000)
    page_size: int = Field(default=100, strict=True, ge=1, le=100)


class AdqAccountParameters(AdqAssetParameters):
    pagination_mode: Literal["PAGINATION_MODE_NORMAL"] = "PAGINATION_MODE_NORMAL"


def asset_spec(ctx: GatewayContext, kind: AssetKind) -> OperationSpec:
    if (ctx.connector_id, ctx.provider, ctx.product_line) != ("adq", "tencent", "adq"):
        raise ValueError("ADQ镜像不能跨连接器或产品线")
    return OperationSpec(
        key=f"adq.assets.{kind.lower()}",
        catalog_id=ctx.catalog_id,
        catalog_sha256=ctx.catalog_sha256,
        connector_id="adq",
        provider="tencent",
        product_line="adq",
        host="api.e.qq.com",
        method="GET",
        path="/v3.0/advertiser/get" if kind == "ACCOUNT" else "/v1.3/campaigns/get",
        effect="READ",
        parameters_model=AdqAccountParameters if kind == "ACCOUNT" else AdqAssetParameters,
        account_parameter="account_id",
        cursor_parameter="page",
        initial_cursor="1",
        idempotent_read=True,
    )


def native_integer(value: Any, *, positive: bool = False) -> str:
    # SDK为int64；不接受float/bool，不经浮点数或JS安全整数范围截断。
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]{0,18}", value):
        number = int(value)
    else:
        raise ValueError("原生整数不是可精确核对的int64")
    if not (1 if positive else 0) <= number <= 9223372036854775807:
        raise ValueError("原生整数超出已核对的int64范围")
    return str(number)


@dataclass(frozen=True)
class AssetPage:
    sync: SyncPage
    wire_page: int
    page_size: int
    total_number: int
    total_page: int


def parse_assets(
    ctx: GatewayContext,
    kind: AssetKind,
    response: HttpExchange,
    requested_page: int,
    page_size: int,
) -> AssetPage:
    if decode_verdict(response).decision != "CONFIRMED_SUCCESS":
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "平台未确认资产读取成功")
    payload = json.loads(response.body).get("data")
    if not isinstance(payload, dict) or not isinstance(payload.get("list"), list):
        raise ValueError("资产响应缺少明确的列表")
    _, pages = verify_adq_page(payload, requested_page, page_size)
    records = []
    for row in payload["list"]:
        if not isinstance(row, dict):
            raise ValueError("资产记录必须为对象")
        id_key, status_key, label_key = (
            ("account_id", "system_status", "business_alias")
            if kind == "ACCOUNT"
            else ("campaign_id", "configured_status", "campaign_name")
        )
        native = native_integer(row.get(id_key), positive=True)
        if (
            kind == "ACCOUNT"
            and native != ctx.native_account_id
            or (
                "account_id" in row
                and native_integer(row["account_id"], positive=True) != ctx.native_account_id
            )
        ):
            raise ValueError("响应包含其他账户的资产，拒绝跨账户拼装")
        status = row.get(status_key)
        label = row.get(label_key)
        if not isinstance(status, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", status):
            raise ValueError("缺少可核对的原生状态，不推定运行状态")
        if label is not None and (
            not isinstance(label, str) or len(label) > 180 or any(ord(c) < 32 for c in label)
        ):
            raise ValueError("资产业务标签不符合有界字段规则")
        values = dict(
            native_id=native,
            native_status=status,
            native_daily_budget=native_integer(row.get("daily_budget")),
            label=label,
            kind=kind,
            protocol_version="3.0" if kind == "ACCOUNT" else "1.3",
            budget_unit="UNCONFIRMED",
            currency=None,
            budget_minor=None,
            target_binding="UNCONFIRMED",
        )
        records.append(MirrorRecord(native_id=native, values=values))
    terminal = requested_page >= pages
    return AssetPage(
        SyncPage(
            tuple(records),
            None if terminal else SecretStr(str(requested_page + 1)),
            terminal,
            response.received_at,
        ),
        requested_page,
        page_size,
        int(payload["page_info"]["total_number"]),
        pages,
    )


class AdqAssetSync:
    def __init__(
        self,
        store: SyncStore,
        client: GovernedPlatformClient,
        tokens: Callable[[GatewayContext], Awaitable[SecretStr]],
        allowed_interfaces: tuple[str, ...],
        required_scopes: tuple[str, ...],
    ) -> None:
        if not required_scopes or len(set(required_scopes)) != len(required_scopes):
            raise ValueError("ADQ同步需要已核对的实际scope映射")
        if store.policy != MIRROR_POLICY:
            raise ValueError("ADQ镜像必须使用固定最小字段投影")
        self.store, self.client, self.tokens = store, client, tokens
        self.allowed, self.scopes = frozenset(allowed_interfaces), frozenset(required_scopes)

    def _scope(self, session: Session, ctx: GatewayContext) -> int:
        return require_adq_read(session, ctx, self.client.application_id, self.scopes)

    async def sync(
        self, ctx: GatewayContext, kind: AssetKind, run_id: UUID, *, page_size: int = 100
    ) -> dict[str, Any]:
        spec = asset_spec(ctx, kind)
        if spec.path not in self.allowed:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "应用未明确核对该资产只读接口")
        native_integer(ctx.native_account_id, positive=True)
        with business_transaction(self.store.engine, ctx.identity) as session:
            version = self._scope(session, ctx)
        ctx = replace(ctx, authorization_version=version)
        parameters = spec.parameters_model.model_validate(
            dict(
                account_id=ctx.native_account_id,
                fields=list(FIELDS[kind]),
                page=1,
                page_size=page_size,
            ),
            strict=True,
        ).model_dump()
        lease = self.store.begin(ctx, spec, f"adq.assets.{kind.lower()}", run_id, parameters)
        while isinstance(lease, SyncLease):
            active_lease = lease
            permit = self.store.prepare(ctx, spec, lease)
            wire_page = int(permit.cursor.get_secret_value()) if permit.cursor else 1
            if wire_page != lease.page_no + 1:
                raise SyncConflict("RESUME_ORIGINAL_RUN")
            page_params = parameters | dict(page=wire_page)

            def guard(
                session: Session,
                permit: PagePermit = permit,
                page_params: dict[str, Any] = page_params,
            ) -> None:
                self._scope(session, ctx)
                self.store.dispatch_guard(session, ctx, spec, permit, page_params)

            try:
                result = await self.client.send(
                    ctx,
                    spec,
                    permit.outbound_id,
                    page_params,
                    await self.tokens(ctx),
                    lane="SYNC",
                    before_persist=guard,
                )
            except CallDeferred:
                self.store.stop(ctx, spec, active_lease, "RATE_LIMITED")
                raise
            if result.exchange is None:
                self.store.stop(ctx, spec, lease, "PARTIAL")
                raise SyncConflict("READ_UNAVAILABLE")
            try:
                page = parse_assets(ctx, kind, result.exchange, wire_page, page_size)

                def project(
                    session: Session, permit: PagePermit = permit, page: AssetPage = page
                ) -> None:
                    self._scope(session, ctx)
                    from yuliang.modules.catalog.adq_mirrors import project_assets

                    project_assets(session, ctx, kind, permit, page)

                lease = self.store.commit_page(ctx, spec, permit, page.sync, project_facts=project)
            except (ValueError, SyncConflict, ApiError):
                self.store.stop(ctx, spec, active_lease, "PARTIAL")
                raise
        return lease
