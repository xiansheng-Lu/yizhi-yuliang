"""独立项目已用的固定只读协议，装配至既有准入/存档端口；不开放写方法。"""

import json
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from yuliang.modules.connectors.base import (
    AssertionResult,
    AuthorizationRef,
    ChangePreview,
    GatewayContext,
    PlatformChange,
    PlatformCommand,
    PlatformReceipt,
    PlatformResourcePage,
    ReadbackReference,
    ReadbackValue,
    ResourceQuery,
    TokenSet,
)
from yuliang.modules.connectors.governed import GovernedPlatformClient
from yuliang.modules.connectors.http import HttpExchange, OperationSpec
from yuliang.modules.connectors.outcomes import BusinessVerdict
from yuliang.platform.errors import ApiError, ErrorCode


class ReadParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AdqPage(ReadParameters):
    account_id: str
    fields: list[str]
    pagination_mode: str = "PAGINATION_MODE_NORMAL"
    page: int = Field(default=1, ge=1, le=100000)
    page_size: int = Field(default=100, ge=1, le=100)


class ProductPage(ReadParameters):
    page_size: int = Field(default=100, ge=1, le=100)
    next_key: str = Field(default="", max_length=2048)


class EmptyRead(ReadParameters):
    pass


# 历史v1.3可用路径明确保留，不将目录统一v3.0前缀冒充实测路径。
READS: dict[str, tuple[str, Literal["GET", "POST"], str, type[BaseModel], tuple[str, ...], str]] = {
    "adq.account": (
        "adq",
        "GET",
        "/v3.0/advertiser/get",
        AdqPage,
        ("account_id", "system_status", "daily_budget"),
        "list",
    ),
    "adq.campaigns": (
        "adq",
        "GET",
        "/v1.3/campaigns/get",
        AdqPage,
        ("campaign_id", "campaign_name", "configured_status", "daily_budget"),
        "list",
    ),
    "adq.funds": (
        "adq",
        "GET",
        "/v1.3/funds/get",
        AdqPage,
        ("fund_type", "balance", "fund_status", "realtime_cost"),
        "list",
    ),
    "wxshop.info": ("wxshop", "GET", "/channels/ec/basics/info/get", EmptyRead, (), "info"),
    "wxshop.products": (
        "wxshop",
        "POST",
        "/channels/ec/product/list/get",
        ProductPage,
        (),
        "product_ids",
    ),
    "wxshop.balance": ("wxshop", "POST", "/channels/ec/funds/getbalance", EmptyRead, (), "balance"),
}


def native_signer(request: httpx.Request, credential: SecretStr) -> httpx.Request:
    additions: dict[str, str | int] = {"access_token": credential.get_secret_value()}
    if request.url.host == "api.e.qq.com":
        additions.update(timestamp=int(time.time()), nonce=secrets.token_hex(12))
    request.url = request.url.copy_merge_params(additions)
    return request


def decode_verdict(response: HttpExchange) -> BusinessVerdict:
    try:
        data = json.loads(response.body)
        if response.http_status != 200 or not isinstance(data, dict):
            return BusinessVerdict()
        code = data.get("code", data.get("errcode"))
        if type(code) is not int:
            return BusinessVerdict()
        if code == 0:
            return BusinessVerdict("CONFIRMED_SUCCESS")
        rejection: Literal["AUTH", "OTHER"] = (
            "AUTH" if code in {11002, 11012, 40001, 42001} else "OTHER"
        )
        return BusinessVerdict("CONFIRMED_REJECTION", rejection)
    except (ValueError, TypeError):
        return BusinessVerdict()


def read_spec(operation: str, catalog_id: str, digest: str) -> OperationSpec:
    connector, method, path, model, _, _ = READS[operation]
    return OperationSpec(
        key=operation,
        catalog_id=catalog_id,
        catalog_sha256=digest,
        connector_id=connector,
        provider="tencent" if connector == "adq" else "wxshop",
        product_line=connector,
        host="api.e.qq.com" if connector == "adq" else "api.weixin.qq.com",
        method=method,
        path=path,
        effect="READ",
        parameters_model=model,
        account_parameter="account_id" if connector == "adq" else None,
        cursor_parameter="next_key" if operation == "wxshop.products" else None,
        idempotent_read=True,
    )


def parse_page(operation: str, response: HttpExchange, page: int = 1) -> PlatformResourcePage:
    if decode_verdict(response).decision != "CONFIRMED_SUCCESS":
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "平台未确认只读请求成功")
    data = json.loads(response.body)
    selector = READS[operation][-1]
    cursor: str | None = None
    if operation.startswith("adq."):
        payload = data.get("data")
        if not isinstance(payload, dict) or not isinstance(payload.get("list"), list):
            raise ValueError("ADQ响应缺少明确的list，不能当作空数据")
        rows = payload["list"]
        info = payload.get("page_info", {})
        if not isinstance(info, dict) or type(info.get("total_page")) is not int:
            raise ValueError("缺少分页总数，不能宣告同步完整")
        if info["total_page"] > page:
            cursor = str(page + 1)
    elif selector == "product_ids":
        if not isinstance(data.get("product_ids"), list) or "next_key" not in data:
            raise ValueError("商品列表或分页终止标识缺失")
        rows = [{"product_id": str(native)} for native in data["product_ids"]]
        value = data["next_key"]
        if value is not None and not isinstance(value, str):
            raise ValueError("商品分页游标类型不正确")
        cursor = value or None
    elif selector == "balance":
        # 实测采集器与旧mock字段不同：保留原生字段，不在缺失时回落为0。
        amount = "available_amount" if "available_amount" in data else "balance"
        pending = "pending_amount" if "pending_amount" in data else "pending_balance"
        if amount not in data or pending not in data:
            raise ValueError("结算余额字段缺失，不能当作0")
        rows = [{key: data[key] for key in (amount, pending)}]
    else:
        # 保留明确接口载荷，不将缺失资金响应变成余额0。
        payload = data.get(selector)
        if payload is None:
            raise ValueError("平台响应缺少已声明数据字段")
        if not isinstance(payload, dict):
            raise ValueError("平台数据结构与已声明字段不符")
        rows = [payload]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("平台记录必须为对象")
    if operation.startswith("adq."):
        fields = frozenset(READS[operation][4])
        rows = [{key: value for key, value in row.items() if key in fields} for row in rows]
    elif selector == "info":
        fields = frozenset({"nickname", "subject_type", "open_timestamp"})
        rows = [{key: value for key, value in row.items() if key in fields} for row in rows]
    # 本批不采集订单PII；未映射接口不能通过call_path绕过白名单。
    return PlatformResourcePage(
        records=tuple(rows), next_cursor=cursor, source_at=None, fetched_at=response.received_at
    )


class ReadOnlyNativeAdapter:
    """凭据与账户范围由服务端resolver核对；所有业务出站必须经过Governed客户端。"""

    def __init__(
        self,
        client: GovernedPlatformClient,
        tokens: Callable[[GatewayContext], Awaitable[SecretStr]],
        connector_id: str,
        allowed_interfaces: tuple[str, ...],
    ) -> None:
        self.client, self.tokens = client, tokens
        self.connector_id, self.allowed = connector_id, frozenset(allowed_interfaces)

    async def read(self, ctx: GatewayContext, query: ResourceQuery) -> PlatformResourcePage:
        definition = READS.get(query.operation)
        if (
            definition is None
            or definition[0] != self.connector_id
            or ctx.connector_id != self.connector_id
            or query.catalog_id != ctx.catalog_id
            or definition[2] not in self.allowed
            or query.target is not None
        ):
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "该应用未核验此只读接口")
        if not set(query.parameters) <= {"page", "page_size", "next_key"}:
            raise ValueError("平台身份、字段与路由由服务器固定")
        spec = read_spec(query.operation, ctx.catalog_id, ctx.catalog_sha256)
        parameters = dict(query.parameters)
        if self.connector_id == "adq":
            parameters.update(account_id=ctx.native_account_id, fields=list(definition[4]))
        validated = spec.parameters_model.model_validate(parameters, strict=True)
        result = await self.client.send(
            ctx, spec, uuid4(), validated.model_dump(), await self.tokens(ctx), lane="SYNC"
        )
        if result.exchange is None:
            raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "平台读取未产生可核对响应")
        return parse_page(query.operation, result.exchange, parameters.get("page", 1))

    async def preview(self, ctx: GatewayContext, change: PlatformChange) -> ChangePreview:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "本次仅接入只读能力")

    async def submit(self, ctx: GatewayContext, command: PlatformCommand) -> PlatformReceipt:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "未授予平台写许可")

    async def readback(self, ctx: GatewayContext, ref: ReadbackReference) -> ReadbackValue:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "写操作读回未在本次资源包启用")

    async def refresh_token(self, auth: AuthorizationRef) -> TokenSet:
        raise ApiError(ErrorCode.CAPABILITY_UNAVAILABLE, "续期须通过校验会话和PG锁的授权服务")

    async def capability_probe(self, ctx: GatewayContext) -> AssertionResult:
        return AssertionResult(
            binding_id=ctx.binding_id,
            capability_version=ctx.capability_version,
            assertions=(),
            unavailable_reason="实际权限需逐接口取证，不从token推定",
        )
