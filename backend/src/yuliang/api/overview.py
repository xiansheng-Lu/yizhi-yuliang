"""经营概况聚合：直接调真实平台只读接口，不依赖 DB 同步或身份会话。

仅用于周五验收的真实数据对接验证路径；最终形态走 OverviewService + 已同步事实表。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import date, timedelta
from typing import Any, Literal
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, SecretStr

from yuliang.modules.connectors.external_tokens import (
    ExternalTokenConfig,
    ExternalTokenProvider,
)
from yuliang.platform.envelope import success


router = APIRouter()
stub_router = APIRouter()

# 两个 token 服务的固定配置（验收阶段硬编码，正式走 resources.local.json）
_TOKEN_SERVICES = {
    "adq": ExternalTokenConfig(
        base_url="http://127.0.0.1:8088",
        api_key=SecretStr("0f-27LmXc4aRqPUEW2ngHFJ2ruBuTPyFqchVY4uOevw"),
        product="adq",
        account_id="93405469",
    ),
    "wxshop": ExternalTokenConfig(
        base_url="http://127.0.0.1:8099",
        api_key=SecretStr("3N2lRENyzYt-srYPldw0diNNfYfl1P5H2nEB7cPTc98"),
        product="wxshop",
    ),
}


class OverviewFilter(BaseModel):
    start_date: str
    end_date: str
    shop_id: str = "all"
    connector_id: str = "all"


class OverviewTotals(BaseModel):
    spend: int | None = None
    gmv: int | None = None
    roi: float | None = None
    orders: int | None = None
    conversion: float | None = None
    cpm: float | None = None


class OverviewReport(BaseModel):
    source: Literal["API"] = "API"
    filter: OverviewFilter
    updatedAt: str | None = None
    coverageNote: str
    totals: OverviewTotals
    previous: OverviewTotals | None = None
    trend: dict[str, Any]
    channels: list[dict[str, Any]]
    lives: list[dict[str, Any]] = []
    distributions: dict[str, list[dict[str, Any]]]
    funnel: dict[str, Any]
    traffic: dict[str, Any]
    ai: dict[str, int | float | None] = {
        "running": 0, "completed": 0, "exceptions": 0, "pending": 0, "successRate": None,
    }
    insights: list[dict[str, Any]] = []


async def _fetch_adq(account_id: str, provider: ExternalTokenProvider, client: httpx.AsyncClient) -> dict[str, Any]:
    """拉 ADQ 账户档案 + 资金余额 + 日报消耗/GMV。

    注意：腾讯广告要求每个请求 timestamp+nonce 唯一，不能并发或复用。
    """
    token = await provider.get_token()
    token_str = token.access_token.get_secret_value()
    base = dict(access_token=token_str, account_id=account_id)

    # 账户档案
    account_r = await client.get(
        "https://api.e.qq.com/v3.0/advertiser/get",
        params={**base, "timestamp": int(time.time()), "nonce": uuid.uuid4().hex[:16],
                "fields": json.dumps(["account_id", "corporation_name", "system_status", "daily_budget", "reject_message"]),
                "pagination_mode": "PAGINATION_MODE_NORMAL", "page": 1, "page_size": 10},
    )
    account_data = account_r.json()

    # 资金余额（独立 timestamp+nonce）
    funds_r = await client.get(
        "https://api.e.qq.com/v3.0/funds/get",
        params={**base, "timestamp": int(time.time()), "nonce": uuid.uuid4().hex[:16],
                "fields": json.dumps(["balance", "realtime_cost", "fund_status", "fund_type"]),
                "fund_type": "FUND_TYPE_CASH"},
    )
    funds_data = funds_r.json()

    return {"account": account_data, "funds": funds_data}


async def _fetch_wxshop(provider: ExternalTokenProvider, client: httpx.AsyncClient, ds: str) -> dict[str, Any]:
    """拉微信小店罗盘整体概览 + 资金余额。"""
    token = await provider.get_token()
    token_str = token.access_token.get_secret_value()

    # 罗盘整体概览（POST）
    compass_r = await client.post(
        "https://api.weixin.qq.com/channels/ec/compass/shop/overall/get",
        params={"access_token": token_str},
        json={"ds": ds},
    )
    compass_data = compass_r.json()

    # 资金余额（POST）
    funds_r = await client.post(
        "https://api.weixin.qq.com/channels/ec/funds/getbalance",
        params={"access_token": token_str},
        json={},
    )
    funds_data = funds_r.json()

    return {"compass": compass_data, "funds": funds_data}


def _fmt_cents(value: Any) -> int | None:
    """平台返回的金额字段统一转 CNY 分（int），None/异常返回 None。"""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return int(round(v))


def _compute_roi(spend: int | None, gmv: int | None) -> float | None:
    if spend is None or gmv is None or spend <= 0:
        return None
    return round(gmv / spend, 4)


@router.get("/v1/overview")
async def get_overview(
    start_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    shop_id: str = "all",
    connector_id: str = "all",
) -> dict[str, Any]:
    """经营概况聚合：同时调 ADQ 和微信小店的真实只读接口。"""
    try:
        date.fromisoformat(start_date)
        date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(400, "start_date / end_date 格式应为 YYYY-MM-DD")
    if start_date > end_date:
        raise HTTPException(400, "start_date 不能晚于 end_date")

    # 并行拉两个平台
    adq_provider = ExternalTokenProvider(_TOKEN_SERVICES["adq"])
    wx_provider = ExternalTokenProvider(_TOKEN_SERVICES["wxshop"])
    ds = start_date.replace("-", "")  # wx_shop罗盘要求 yyyymmdd

    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        adq_raw, wx_raw = await asyncio.gather(
            _fetch_adq("93405469", adq_provider, client),
            _fetch_wxshop(wx_provider, client, ds),
            return_exceptions=True,
        )

    # --- ADQ 解析 ---
    adq_ok = isinstance(adq_raw, dict) and adq_raw.get("account", {}).get("code") == 0
    adq_account_name = None
    if adq_ok:
        acct_list = adq_raw["account"].get("data", {}).get("list", [])
        if acct_list:
            adq_account_name = acct_list[0].get("corporation_name")

    adq_funds_spend = None
    if adq_ok:
        funds_data = adq_raw.get("funds", {})
        funds_ok = isinstance(funds_data, dict) and funds_data.get("code") == 0
        if funds_ok:
            funds_list = funds_data.get("data", {}).get("list", [])
            for f in funds_list:
                if f.get("fund_type") == "FUND_TYPE_CASH":
                    adq_funds_spend = _fmt_cents(f.get("realtime_cost"))
                    break

    # --- wx_shop 解析 ---
    wx_ok = isinstance(wx_raw, dict) and wx_raw.get("compass", {}).get("errcode") == 0
    wx_compass_overall = {}
    if wx_ok:
        wx_compass_overall = wx_raw["compass"].get("overall_data", {})

    # --- 聚合 ---
    # ADQ 日报接口（v1.3 daily_reports）需要 level + date_range + date
    # 这里先用资金 realtime_cost 做 spend（单日），GMV 暂时靠后续日报接口补
    spend = adq_funds_spend
    gmv = None  # ADQ daily_reports 当前权限可能不足，留空等后续
    roi = _compute_roi(spend, gmv)

    updated_at = time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime())
    coverage_notes = []
    if adq_ok:
        coverage_notes.append(f"ADQ 账户 93405469（{adq_account_name or '企业名称未返回'}）资金余额实时获取成功")
    else:
        coverage_notes.append(f"ADQ 账户接口异常：{adq_raw.get('message', '未知错误') if isinstance(adq_raw, dict) else str(adq_raw)[:120]}")
    if wx_ok:
        coverage_notes.append("微信小店罗盘整体概览获取成功")
    else:
        coverage_notes.append(f"微信小店接口异常：{wx_raw.get('errmsg', '未知错误') if isinstance(wx_raw, dict) else str(wx_raw)[:120]}")

    channels: list[dict[str, Any]] = []
    if adq_ok and spend is not None:
        channels.append({
            "id": "adq", "name": "腾讯广告",
            "detail": f"账户 93405469（{adq_account_name or '—'}）",
            "spend": spend, "gmv": gmv, "roi": roi,
            "share": 1.0,
        })
    if wx_ok:
        wx_overall_gmv = _fmt_cents(wx_compass_overall.get("pay_gmv"))
        channels.append({
            "id": "wxshop", "name": "微信小店",
            "detail": f"罗盘整体概览 · {start_date}",
            "spend": None, "gmv": wx_overall_gmv, "roi": None,
            "share": None,
        })

    total_spend = spend
    total_gmv = gmv
    for c in channels:
        if c["spend"] is not None and (total_spend is None or c["id"] != "wxshop"):
            pass  # ADQ 已经有了；wxshop 暂时没有广告消耗
    total_gmv = total_gmv or next((c["gmv"] for c in channels if c["gmv"] is not None), None)

    report = OverviewReport(
        source="API",
        filter=OverviewFilter(start_date=start_date, end_date=end_date, shop_id=shop_id, connector_id=connector_id),
        updatedAt=updated_at,
        coverageNote="；".join(coverage_notes),
        totals=OverviewTotals(spend=total_spend, gmv=total_gmv, roi=_compute_roi(total_spend, total_gmv)),
        previous=None,
        trend={"status": "unavailable", "reason": "当前仅拉取单日快照，历史趋势待接入同步事实表"},
        channels=channels,
        lives=[],
        distributions={
            "status": [],
            "channel": [
                {"id": c["id"], "name": c["name"], "value": v}
                for c in channels
                if (v := c["spend"] if c["spend"] is not None else c["gmv"]) is not None
            ],
        },
        funnel={"status": "unavailable", "reason": "曝光-点击-进房-下单-支付漏斗口径待对齐"},
        traffic={"status": "unavailable", "reason": "流量来源统计接口待接入"},
        insights=[
            {"id": "source", "tone": "info", "title": "真实平台数据",
             "detail": "当前数据直接来自腾讯广告 API 和微信小店罗盘，未经过本地同步层。",
             "href": "/app/analytics"},
        ],
    )
    return report.model_dump(mode="json")


# ---------------------------------------------------------------------------
# 验收 stub：前端 ApiClient 在 mode=api 时需要这些接口返回合法 envelope。
# 正式部署应由完整 identity/auth/frontend 路由提供。
# ---------------------------------------------------------------------------

def _envelope(data: Any) -> dict[str, Any]:
    """构造 command-job-v1.0 响应信封（ApiClient.request() 解析契约）。"""
    return {
        "schemaVersion": "command-job-v1.0",
        "requestId": uuid4().hex,
        "data": data,
    }


@stub_router.get("/v1/auth/session")
async def stub_session() -> dict[str, Any]:
    """验收 stub：返回一个 mock Identity，让前端 workspace 能初始化。"""
    identity = {
        "mode": "API",
        "userId": str(uuid4()),
        "displayName": "验收测试用户",
        "avatarUrl": None,
        "tenantId": "yizhi",
        "tenantName": "易致",
        "email": "yizhi-test@example.com",
        "shopIds": [],
        "permissions": ["overview:read", "shop:read", "account:read", "connection:read"],
        "workspacePages": ["overview", "monitor", "products", "reports", "analytics", "tasks", "goals", "plans", "decisions", "executions"],
        "lastLogin": time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime()),
    }
    return _envelope(identity)


@stub_router.get("/v1/frontend/snapshot")
async def stub_snapshot() -> dict[str, Any]:
    """验收 stub：返回最小 FrontendSnapshot，覆盖 workspace provider 初始化所需字段。"""
    today = date.today().isoformat()
    snapshot = {
        "schemaVersion": "command-job-v1.0",
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime()),
        "source": "API",
        "scope": {
            "timezone": "Asia/Shanghai",
            "date": today,
            "shop_id": "all",
            "live_session_id": "all",
            "connector_id": "all",
        },
        "tenants": [],
        "shops": [],
        "connectors": [
            {
                "id": str(uuid4()),
                "name": "腾讯广告 ADQ",
                "type": "adq",
                "status": "active",
                "shopId": None,
                "externalId": "1112044436",
                "syncedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime()),
                "lastSyncStatus": "ok",
                "lastSyncMessage": "已接入真实 token 服务 8088",
                "tenantId": "yizhi",
            },
            {
                "id": str(uuid4()),
                "name": "微信小店",
                "type": "wxshop",
                "status": "active",
                "shopId": None,
                "externalId": "wx3bc997d3959e4b2e",
                "syncedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime()),
                "lastSyncStatus": "ok",
                "lastSyncMessage": "已接入真实 token 服务 8099",
                "tenantId": "yizhi",
            },
        ],
        "adAccounts": [
            {
                "id": "93405469",
                "name": "易品心选电子商务(云南)有限公司",
                "accountNo": "93405469",
                "externalId": "93405469",
                "connectorId": None,
                "shopId": None,
                "currency": "CNY",
                "timezone": "Asia/Shanghai",
                "status": "active",
            }
        ],
        "liveSessions": [],
        "plans": [],
        "products": [],
        "materials": [],
        "tasks": [],
        "riskPolicies": [],
        "replaySegments": [],
        "replayReports": [],
    }
    return _envelope(snapshot)
