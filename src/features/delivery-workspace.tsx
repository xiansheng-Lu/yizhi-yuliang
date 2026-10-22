"use client";
import { useEffect, useState } from "react";
import {
  ArrowClockwise,
  CheckCircle,
  Clock,
  Info,
  Plug,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  availabilityNames,
  isDeliveryPage,
  platforms,
  scopeKey,
  time,
  type DeliveryPage,
  type PlatformScope,
} from "@/lib/delivery";
import { DeliveryProvider, useDelivery } from "./delivery/context";
import { Goals } from "./delivery/goals";
import { Plans } from "./delivery/plans";
import { Decisions } from "./delivery/decisions";
import { Budgets } from "./delivery/budgets";
import { Executions } from "./delivery/executions";
import { Status } from "./delivery/ui";
import "@/styles/delivery.css";
export { isDeliveryPage };
export function DeliveryWorkspace({ pageId }: { pageId: DeliveryPage }) {
  return (
    <DeliveryProvider>
      <DeliveryContent pageId={pageId} />
    </DeliveryProvider>
  );
}
function DeliveryContent({ pageId }: { pageId: DeliveryPage }) {
  const d = useDelivery();
  const [clock, setClock] = useState<number | null>(null);
  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);
  const selected = d.data.accounts.find(
    (a) =>
      a.id === d.scope.accountId &&
      (d.scope.platform === "all" || a.platform === d.scope.platform) &&
      (d.scope.shopId === "all" || a.shopId === d.scope.shopId),
  );
  const accounts = d.data.accounts.filter(
    (a) =>
      (d.scope.platform === "all" || a.platform === d.scope.platform) &&
      (d.scope.shopId === "all" || a.shopId === d.scope.shopId),
  );
  const maxAge = Math.min(
    ...d.data.capabilities.map((c) => c.maxAgeSeconds),
    300,
  );
  const stale =
    d.data.sync.state === "STALE" ||
    (!!clock &&
      !!d.data.sync.syncedAt &&
      clock - Date.parse(d.data.sync.syncedAt) > maxAge * 1000);
  const source =
    d.data.source === "MOCK" ? "演示数据 / 正式写入隔离" : "API 数据";
  const statistics = `${d.scope.start} 至 ${d.scope.end} · Asia/Shanghai`;
  return (
    <div className="delivery-workspace" aria-label="多平台投放管理">
      <section className="dw-context" aria-label="投放管理统一平台工具栏">
        <div className="dw-context-fields">
          <label>
            投放平台
            <select
              aria-label="投放平台"
              value={d.scope.platform}
              onChange={(e) =>
                d.changeScope({ platform: e.target.value as PlatformScope })
              }
            >
              <option value="all">全平台 · 经营管理</option>
              <option value="adq">腾讯广告 ADQ</option>
              <option value="beans">视频号微信豆加热</option>
            </select>
          </label>
          <label>
            经营店铺
            <select
              aria-label="经营店铺"
              value={d.scope.shopId}
              onChange={(e) => d.changeScope({ shopId: e.target.value })}
            >
              <option value="all">全部授权店铺</option>
              {d.data.shops.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            投放账户
            <select
              aria-label="投放账户"
              value={selected?.id ?? "all"}
              onChange={(e) => d.changeScope({ accountId: e.target.value })}
            >
              <option value="all">
                {d.scope.platform === "all" ? "跨账户只读汇总" : "全部关联账户"}
              </option>
              {d.scope.platform !== "all" &&
                accounts.map((a) => (
                  <option value={a.id} key={a.id}>
                    {a.name} · {a.id}
                  </option>
                ))}
            </select>
          </label>
          <label>
            开始日期
            <input
              type="date"
              aria-label="投放统计开始日期"
              value={d.scope.start}
              max={d.scope.end}
              onChange={(e) => {
                if (e.target.value) d.changeScope({ start: e.target.value });
              }}
            />
          </label>
          <label>
            结束日期
            <input
              type="date"
              aria-label="投放统计结束日期"
              min={d.scope.start}
              value={d.scope.end}
              onChange={(e) => {
                if (e.target.value) d.changeScope({ end: e.target.value });
              }}
            />
          </label>
          <button
            className="button secondary dw-refresh"
            disabled={d.loading || d.busy}
            onClick={d.refresh}
          >
            <ArrowClockwise size={15} className={d.loading ? "dw-spin" : ""} />
            {d.loading ? "同步中" : "同步"}
          </button>
        </div>
        <div className="dw-context-status">
          <span>
            <Plug size={13} />
            {selected
              ? availabilityNames[selected.authorization]
              : "选择具体账户查看授权"}
          </span>
          <span>
            <Clock size={13} />
            最近同步 {time(d.data.sync.syncedAt)}
          </span>
          <span>
            {d.scope.platform === "all"
              ? "全平台"
              : platforms[d.scope.platform].name}{" "}
            · {source}
          </span>
          <Status
            tone={
              stale || d.error
                ? "red"
                : d.data.sync.state === "READY"
                  ? "green"
                  : "neutral"
            }
          >
            {d.loading
              ? "加载中"
              : d.error
                ? "同步失败"
                : stale
                  ? "数据已过期"
                  : d.data.sync.state === "NOT_INTEGRATED"
                    ? "新增接口待接入"
                    : d.data.sync.state === "FORBIDDEN"
                      ? "无权限"
                      : d.data.sync.state === "UNAUTHORIZED"
                        ? "未授权"
                        : d.data.sync.state === "EMPTY"
                          ? "暂无数据"
                          : "已同步"}
          </Status>
        </div>
      </section>
      <div
        className={`dw-source-notice ${d.data.source === "MOCK" ? "demo" : ""}`}
      >
        <Info size={15} />
        <span>{d.data.sync.message}</span>
      </div>
      <p className="dw-statistic-time">
        统计范围 {statistics}
        {d.scope.liveSessionId && ` · 场次 ${d.scope.liveSessionId}`} ·
        微信小店是交易店铺对象，ADQ与微信豆是投放渠道。
      </p>
      {(d.error || stale) && (
        <div className="dw-feedback warning" role="alert">
          <WarningCircle size={16} />
          {d.error || "平台数据已过期，请同步后再进行投放修改。"}
          {d.error && "。保留当前范围最后一次可用数据，不展示其他账户的缓存。"}
        </div>
      )}
      {d.message && (
        <div className="dw-feedback" role="status">
          <CheckCircle size={16} />
          <span>{d.message}</span>
        </div>
      )}
      <div key={scopeKey(d.scope)} aria-busy={d.loading}>
        {pageId === "plans" ? (
          <Plans />
        ) : pageId === "goals" ? (
          <Goals />
        ) : pageId === "decisions" ? (
          <Decisions />
        ) : pageId === "budgets" ? (
          <Budgets />
        ) : (
          <Executions />
        )}
      </div>
    </div>
  );
}
