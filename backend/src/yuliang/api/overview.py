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
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, SecretStr

from yuliang.modules.connectors.external_tokens import (
    ExternalTokenConfig,
    ExternalTokenProvider,
)

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
        "running": 0,
        "completed": 0,
        "exceptions": 0,
        "pending": 0,
        "successRate": None,
    }
    insights: list[dict[str, Any]] = []


_ADQ_API = "https://api.e.qq.com"
_BM_CACHE_TTL = 3600.0  # 商务管家子账号列表缓存 1 小时
_bm_sub_cache: dict[str, tuple[float, list[str]]] = {}


def _adq_params(token_str: str, **extra: Any) -> dict[str, Any]:
    """腾讯广告全局参数：每个请求必须独立 timestamp+nonce。"""
    return {
        "access_token": token_str,
        "timestamp": int(time.time()),
        "nonce": uuid.uuid4().hex[:16],
        **extra,
    }


async def _list_adq_token_accounts(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """从 token 服务读取全部已授权 ADQ 账号（账号发现的单一事实来源）。

    返回项含 account_id / account_role_type，商务管家账号的 role 含 BUSINESS。
    """
    cfg = _TOKEN_SERVICES["adq"]
    r = await client.get(
        f"{cfg.base_url}/api/accounts",
        headers={"X-API-Key": cfg.api_key.get_secret_value()},
    )
    data = r.json()
    if not data.get("ok"):
        return []
    return [a for a in data.get("accounts", []) if a.get("account_id")]


async def _adq_token_for(account_id: str) -> str:
    """按 token 服务中的账号 id 取有效 access_token。"""
    cfg = _TOKEN_SERVICES["adq"]
    provider = ExternalTokenProvider(
        ExternalTokenConfig(
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            product=cfg.product,
            account_id=account_id,
        )
    )
    token = await provider.get_token()
    return token.access_token.get_secret_value()


async def _fetch_bm_subaccounts(bm_id: str, token_str: str, client: httpx.AsyncClient) -> list[str]:
    """枚举商务管家旗下广告主子账号（分页拉全，结果缓存 1 小时）。"""
    now = time.time()
    cached = _bm_sub_cache.get(bm_id)
    if cached and now - cached[0] < _BM_CACHE_TTL:
        return cached[1]
    subs: list[str] = []
    page = 1
    while True:
        r = await client.get(
            f"{_ADQ_API}/v1.3/business_manager_relations/get",
            params=_adq_params(token_str, page=page, page_size=50),
        )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(
                f"business_manager_relations/get code={data.get('code')} "
                f"msg={data.get('message_cn') or data.get('message')}"
            )
        items = data.get("data", {}).get("list", [])
        for it in items:
            aid = it.get("account_id") or (it.get("advertiser_info") or {}).get("account_id")
            if aid:
                subs.append(str(aid))
        if len(items) < 50:
            break
        page += 1
    _bm_sub_cache[bm_id] = (now, subs)
    return subs


async def _fetch_adq_account(
    account_id: str, token_str: str, client: httpx.AsyncClient, day: str
) -> dict[str, Any]:
    """拉单个 ADQ 账号的档案 + 资金余额 + 当日实时消耗。"""

    async def _get(path: str, **extra: Any) -> dict[str, Any]:
        r = await client.get(
            f"{_ADQ_API}/{path}", params=_adq_params(token_str, account_id=account_id, **extra)
        )
        return r.json()

    account = await _get(
        "v3.0/advertiser/get",
        fields=json.dumps(["account_id", "corporation_name", "system_status", "daily_budget"]),
        pagination_mode="PAGINATION_MODE_NORMAL",
        page=1,
        page_size=10,
    )
    funds = await _get(
        "v3.0/funds/get",
        fields=json.dumps(["balance", "realtime_cost", "fund_status", "fund_type"]),
        fund_type="FUND_TYPE_CASH",
    )
    rtcost = await _get("v1.3/realtime_cost/get", level="REPORT_LEVEL_ADVERTISER", date=day)
    return {"account_id": account_id, "account": account, "funds": funds, "rtcost": rtcost}


async def _fetch_adq_all(client: httpx.AsyncClient, day: str) -> dict[str, Any]:
    """发现全部 ADQ 账号并并发拉取数据。

    账号来源：token 服务已授权列表。其中商务管家账号展开为旗下全部
    子账号（共用商务管家 token），普通广告主账号用自身 token。
    """
    ts_accounts = await _list_adq_token_accounts(client)

    # 并发取所有账号的 token
    token_map: dict[str, str] = {}

    async def _load(aid: str) -> None:
        token_map[aid] = await _adq_token_for(aid)

    await asyncio.gather(*(_load(str(a["account_id"])) for a in ts_accounts))

    # 展开目标账号
    targets: list[tuple[str, str]] = []  # (account_id, token_str)
    bm_sub_count = 0
    for a in ts_accounts:
        aid = str(a["account_id"])
        role = (a.get("account_role_type") or "").upper()
        if "BUSINESS" in role:
            subs = await _fetch_bm_subaccounts(aid, token_map[aid], client)
            bm_sub_count += len(subs)
            targets.extend((s, token_map[aid]) for s in subs)
        else:
            targets.append((aid, token_map[aid]))

    sem = asyncio.Semaphore(4)

    async def _one(aid: str, tok: str) -> dict[str, Any]:
        async with sem:
            return await _fetch_adq_account(aid, tok, client, day)

    results = await asyncio.gather(
        *(_one(aid, tok) for aid, tok in targets), return_exceptions=True
    )
    accounts = [r for r in results if isinstance(r, dict)]
    errors = [str(r)[:120] for r in results if not isinstance(r, dict)]
    return {
        "accounts": accounts,
        "target_count": len(targets),
        "bm_sub_count": bm_sub_count,
        "errors": errors,
    }


async def _fetch_wxshop(
    provider: ExternalTokenProvider, client: httpx.AsyncClient, ds: str
) -> dict[str, Any]:
    """拉微信小店罗盘整体概览 + 资金余额。

    罗盘数据 T+1，若传入的 ds 当天 data 为空，自动回退到前一天，
    并通过 effective_ds 返回实际生效的日期，供前端展示。
    """
    token = await provider.get_token()
    token_str = token.access_token.get_secret_value()

    # 先试主日期
    compass_r = await client.post(
        "https://api.weixin.qq.com/channels/ec/compass/shop/overall/get",
        params={"access_token": token_str},
        json={"ds": ds},
    )
    compass_data = compass_r.json()

    effective_ds = ds
    # 若 errcode=0 但 data 为空 dict，尝试回退一天
    if compass_data.get("errcode") == 0 and not compass_data.get("data"):
        try:
            fallback = (date(int(ds[:4]), int(ds[4:6]), int(ds[6:8])) - timedelta(days=1)).strftime(
                "%Y%m%d"
            )
            fallback_r = await client.post(
                "https://api.weixin.qq.com/channels/ec/compass/shop/overall/get",
                params={"access_token": token_str},
                json={"ds": fallback},
            )
            fallback_data = fallback_r.json()
            if fallback_data.get("errcode") == 0 and fallback_data.get("data"):
                compass_data = fallback_data
                effective_ds = fallback
        except (ValueError, IndexError):
            pass

    # 资金余额（POST）
    funds_r = await client.post(
        "https://api.weixin.qq.com/channels/ec/funds/getbalance",
        params={"access_token": token_str},
        json={},
    )
    funds_data = funds_r.json()

    return {"compass": compass_data, "funds": funds_data, "effective_ds": effective_ds}


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
        raise HTTPException(400, "start_date / end_date 格式应为 YYYY-MM-DD") from None
    if start_date > end_date:
        raise HTTPException(400, "start_date 不能晚于 end_date")

    # 并行拉两个平台
    wx_provider = ExternalTokenProvider(_TOKEN_SERVICES["wxshop"])
    ds = start_date.replace("-", "")  # wx_shop罗盘要求 yyyymmdd

    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        adq_all, wx_raw = await asyncio.gather(
            _fetch_adq_all(client, end_date),
            _fetch_wxshop(wx_provider, client, ds),
            return_exceptions=True,
        )

    # --- ADQ 解析（多账号聚合） ---
    adq_ok = isinstance(adq_all, dict)
    adq_rows: list[dict[str, Any]] = []
    adq_total_spend = 0
    adq_spend_known = False
    if adq_ok:
        for acc in adq_all["accounts"]:
            name = None
            if acc.get("account", {}).get("code") == 0:
                acct_list = acc["account"].get("data", {}).get("list", [])
                if acct_list:
                    name = acct_list[0].get("corporation_name")
            cost = None
            if acc.get("rtcost", {}).get("code") == 0:
                rl = acc["rtcost"].get("data", {}).get("list", [])
                if rl:
                    cost = _fmt_cents(rl[0].get("cost"))
            balance = None
            if acc.get("funds", {}).get("code") == 0:
                for f in acc["funds"].get("data", {}).get("list", []):
                    if f.get("fund_type") == "FUND_TYPE_CASH":
                        balance = _fmt_cents(f.get("balance"))
                        break
            if cost is not None:
                adq_total_spend += cost
                adq_spend_known = True
            adq_rows.append(
                {"account_id": acc["account_id"], "name": name, "cost": cost, "balance": balance}
            )

    # --- wx_shop 解析 ---
    wx_ok = isinstance(wx_raw, dict) and wx_raw.get("compass", {}).get("errcode") == 0
    wx_compass_overall = {}
    wx_effective_ds = wx_raw.get("effective_ds") if isinstance(wx_raw, dict) else None
    if wx_ok:
        wx_compass_overall = wx_raw["compass"].get("data", {})

    # --- 聚合 ---
    # spend 取各账号当日实时消耗之和（realtime_cost/get），GMV 来自微信小店罗盘
    spend: int | None = adq_total_spend if adq_spend_known else None
    gmv = None
    orders = None
    _compute_roi(spend, gmv)

    updated_at = time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime())
    coverage_notes = []
    if adq_ok:
        bm_cnt = adq_all["bm_sub_count"]
        src = f"，含商务管家子账号 {bm_cnt} 个" if bm_cnt else ""
        err_cnt = len(adq_all["errors"])
        err_note = f"，{err_cnt} 个账号拉取失败" if err_cnt else ""
        coverage_notes.append(
            f"ADQ 已接入 {len(adq_rows)}/{adq_all['target_count']} 个账号{src}{err_note}，"
            f"{end_date} 实时消耗合计 ¥{adq_total_spend / 100:.2f}"
        )
    else:
        coverage_notes.append(f"ADQ 账号发现异常：{str(adq_all)[:120]}")
    if wx_ok:
        if wx_effective_ds and wx_effective_ds != ds:
            coverage_notes.append(
                f"微信小店罗盘整体概览获取成功（当天数据未生成，已回退至 {wx_effective_ds}）"
            )
        else:
            coverage_notes.append("微信小店罗盘整体概览获取成功")
    else:
        errmsg = wx_raw.get("errmsg", "未知错误") if isinstance(wx_raw, dict) else str(wx_raw)[:120]
        coverage_notes.append(f"微信小店接口异常：{errmsg}")

    channels: list[dict[str, Any]] = []
    for row in adq_rows:
        share = None
        if adq_total_spend > 0 and row["cost"] is not None:
            share = round(row["cost"] / adq_total_spend, 4)
        channels.append(
            {
                "id": f"adq_{row['account_id']}",
                "name": "腾讯广告",
                "detail": f"{row['name'] or '—'}（{row['account_id']}）",
                "spend": row["cost"],
                "gmv": None,
                "roi": None,
                "share": share,
            }
        )
    if wx_ok:
        wx_overall_gmv = _fmt_cents(wx_compass_overall.get("pay_gmv"))
        wx_orders = _fmt_cents(wx_compass_overall.get("pay_order_cnt"))
        _fmt_cents(wx_compass_overall.get("pay_uv"))
        display_ds = wx_effective_ds or ds
        display_label = (
            f"{display_ds[:4]}-{display_ds[4:6]}-{display_ds[6:8]}" if display_ds else start_date
        )
        channels.append(
            {
                "id": "wxshop",
                "name": "微信小店",
                "detail": f"罗盘整体概览 · {display_label}",
                "spend": None,
                "gmv": wx_overall_gmv,
                "orders": wx_orders,
                "roi": None,
                "share": None,
            }
        )
        if wx_overall_gmv is not None:
            gmv = wx_overall_gmv
        if wx_orders is not None:
            orders = wx_orders
        _compute_roi(spend, gmv)

    total_spend = spend
    total_gmv = gmv

    report = OverviewReport(
        source="API",
        filter=OverviewFilter(
            start_date=start_date, end_date=end_date, shop_id=shop_id, connector_id=connector_id
        ),
        updatedAt=updated_at,
        coverageNote="；".join(coverage_notes),
        totals=OverviewTotals(
            spend=total_spend,
            gmv=total_gmv,
            orders=orders,
            roi=_compute_roi(total_spend, total_gmv),
        ),
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
            {
                "id": "source",
                "tone": "info",
                "title": "真实平台数据",
                "detail": "当前数据直接来自腾讯广告 API 和微信小店罗盘，未经过本地同步层。",
                "href": "/app/analytics",
            },
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
        "workspacePages": [
            "overview",
            "monitor",
            "products",
            "reports",
            "analytics",
            "tasks",
            "goals",
            "plans",
            "decisions",
            "executions",
        ],
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
