"use client";
import { useState } from "react";
import {
  businessTotals,
  connectorNames,
  money,
  type FrontendSnapshot,
} from "@/lib/domain";
import { displayTime, type ReportingScope } from "@/lib/command-job";
import { scopedPlans } from "@/lib/reporting";
import { useWorkspace } from "./provider";
import { Badge, EmptyState, Metric } from "./ui";
import { invalidateSnapshotRead } from "@/services/snapshot-reader";

export function ScopeControls({ snapshot }: { snapshot: FrontendSnapshot }) {
  const { service, reload } = useWorkspace();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function change(patch: Partial<ReportingScope>) {
    if (!service || busy) return;
    setBusy(true);
    setError("");
    try {
      window.dispatchEvent(new Event("yuliang:scope-changing"));
      invalidateSnapshotRead(service);
      await service.setScope({ ...snapshot.scope, ...patch });
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "统计范围未保存");
    } finally {
      window.dispatchEvent(new Event("yuliang:scope-settled"));
      setBusy(false);
    }
  }
  const scope = snapshot.scope;
  return (
    <section className="scope-controls task-form" aria-label="统一统计范围">
      <div className="scope-fields">
        <label>
          当前店铺
          <select
            aria-label="当前店铺"
            value={scope.shop_id}
            disabled={busy}
            onChange={(e) =>
              void change({ shop_id: e.target.value, live_session_id: "all" })
            }
          >
            <option value="all">全部授权店铺</option>
            {snapshot.shops.map((x) => (
              <option key={x.id} value={x.id}>
                {x.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          当前直播间
          <select
            aria-label="当前直播间"
            value={scope.live_session_id}
            disabled={busy}
            onChange={(e) => void change({ live_session_id: e.target.value })}
          >
            <option value="all">全部关联场次</option>
            {snapshot.liveSessions
              .filter(
                (x) => scope.shop_id === "all" || x.shopId === scope.shop_id,
              )
              .map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
          </select>
        </label>
        <label>
          平台筛选
          <select
            aria-label="平台筛选"
            value={scope.connector_id}
            disabled={busy}
            onChange={(e) => void change({ connector_id: e.target.value })}
          >
            <option value="all">全部平台</option>
            {snapshot.connectors.map((x) => (
              <option key={x.id} value={x.id}>
                {connectorNames[x.id]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <small>
        统计日 {scope.date} · {scope.timezone} · 支付归因 GMV / 同范围广告消耗 ·
        含已暂停计划历史消耗
      </small>
      {error && (
        <p role="alert" className="feedback">
          {error}
        </p>
      )}
    </section>
  );
}
export function CoreMetrics({ snapshot }: { snapshot: FrontendSnapshot }) {
  const totals = businessTotals(scopedPlans(snapshot));
  return (
    <section className="metrics-grid" aria-label="三个核心经营指标">
      <Metric
        label="今日广告消耗"
        value={money(totals.spend)}
        note={`${snapshot.scope.date} · Asia/Shanghai`}
      />
      <Metric
        label="支付GMV"
        value={money(totals.gmv)}
        note="同一店铺 / 场次 / 平台 · 支付归因"
      />
      <Metric
        label="投放ROI"
        value={totals.roi === null ? "暂无口径" : totals.roi.toFixed(2)}
        note="支付GMV ÷ 广告消耗"
      />
    </section>
  );
}
export function LiveSummary({
  snapshot,
  liveId,
}: {
  snapshot: FrontendSnapshot;
  liveId?: string | null;
}) {
  const roomId = liveId ?? snapshot.scope.live_session_id;
  const room = snapshot.liveSessions.find((x) => x.id === roomId);
  if (!room)
    return (
      <EmptyState
        title="请选择一个直播间"
        description="切换上方场次后可查看对应直播状态与更新时间。"
      />
    );
  const plans = scopedPlans(snapshot, {
    ...snapshot.scope,
    shop_id: room.shopId,
    live_session_id: room.id,
  });
  const totals = businessTotals(plans);
  return (
    <div className="live-summary" aria-label="关联直播情况">
      <div className="row between">
        <h3>{room.name}</h3>
        <Badge tone="blue">
          {snapshot.source} ·{" "}
          {room.status === "MOCK_LIVE" ? "演示直播中" : "状态未知"}
        </Badge>
      </div>
      <p>数据更新时间：{displayTime(room.sourceUpdatedAt)} · Asia/Shanghai</p>
      <div className="live-figures">
        <div>
          <span>直播间在线</span>
          <b>{room.online ?? "缺失"}</b>
        </div>
        <div>
          <span>商品点击</span>
          <b>{room.productClicks ?? "缺失"}</b>
        </div>
      </div>
      <div className="facts">
        <div>
          <span>当前筛选广告消耗</span>
          <b>{money(totals.spend)}</b>
        </div>
        <div>
          <span>当前筛选支付GMV</span>
          <b>{money(totals.gmv)}</b>
        </div>
        <div>
          <span>投放ROI</span>
          <b>{totals.roi?.toFixed(2) ?? "暂无口径"}</b>
        </div>
        <div>
          <span>投放情况</span>
          <b>
            {plans.filter((x) => x.enabled).length} / {plans.length} 个计划开启
          </b>
        </div>
      </div>
      <div className="notice">
        {snapshot.source === "MOCK"
          ? "异常观察（演示样例）：商品点击后的支付转化需要关注；请结合货盘与主播话术核对。固定采样，不是实时 AI 分析。"
          : "异常分析待后端提供，不根据样例推断真实结论。"}
      </div>
    </div>
  );
}
