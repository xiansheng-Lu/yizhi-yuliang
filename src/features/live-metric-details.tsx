"use client";
import type { ReactNode } from "react";
import Link from "next/link";
import { ArrowClockwise, ArrowLeft, ArrowRight, ChartLineUp, Info, Lightning, ShieldCheck } from "@phosphor-icons/react";
import { connectorNames, type FrontendSnapshot } from "@/lib/domain";
import { finiteValue, formatLiveTime, type LiveModules, type LiveStatus } from "@/lib/live-room";
import { liveMetricDefinitions, liveMetricHref, liveMetricIds, liveMetricMoney, livePlanAmounts, type LiveMetricId } from "@/lib/live-metric";

export interface LiveMetricSummary { id: LiveMetricId; name: string; value: string; unit: string; detail: string }
export function LiveMetricDetails({ metric, metrics, snapshot, modules, status, statusBar, scopeControls, readTime,
  plansReadTime, refreshing, waiting, canCreate, canManagePlans, search, onRefresh, onOperation }: {
  metric: LiveMetricId | null; metrics: LiveMetricSummary[]; snapshot: FrontendSnapshot; modules: LiveModules; status: LiveStatus;
  statusBar: ReactNode; scopeControls: ReactNode; readTime: ReactNode; plansReadTime: ReactNode;
  refreshing: boolean; waiting: boolean; canCreate: boolean; canManagePlans: boolean; search: string;
  onRefresh: () => void; onOperation: (text: string) => void;
}) {
  const home = liveMetricHref(null, search);
  const room = modules.live.data?.room ?? snapshot.liveSessions.find((item) => item.id === snapshot.scope.live_session_id);
  const selected = !!room;
  if (!metric) return <div className="live-room"><Link href={home} className="lr-back"><ArrowLeft size={16} />返回作战室</Link><section className="lr-select-guide"><h1>未找到该指标</h1><p>请返回作战室，选择在线人数、观看、GMV、订单、消耗或ROI。</p></section></div>;
  const definition = liveMetricDefinitions[metric];
  const current = metrics.find((item) => item.id === metric)!;
  const plans = modules.plans.data ?? [];
  const hasMixedWindows = new Set(plans.map((plan) => plan.attributionWindow)).size > 1;
  const platform = snapshot.scope.connector_id === "all" ? "全部平台" : connectorNames[snapshot.scope.connector_id as keyof typeof connectorNames] ?? snapshot.scope.connector_id;
  const count = (value: unknown) => finiteValue(value) === null ? "--" : Number(value).toLocaleString("zh-CN");
  const productClicks = selected && status !== "not_started" && modules.live.data?.platformCovered ? room?.productClicks : null;
  return <div className="live-room lr-detail" data-source={snapshot.source} data-live-status={status}>
    <nav className="lr-detail-breadcrumb" aria-label="指标详情导航"><Link href={home}><ArrowLeft size={15} />AI直播作战室</Link><span>/</span><span aria-current="page">{definition.name}详情</span></nav>
    <div className="lr-heading"><div><span className="lr-kicker">实时经营指标 / 指标详情</span><h1>{definition.name}详情</h1><p>查看当前场次的口径、数据来源与可用明细。</p></div><div className="lr-heading-actions"><Link href={home} className="button secondary"><ArrowLeft size={16} />返回大屏</Link><button className="button primary" disabled={!selected || refreshing || waiting} onClick={onRefresh}><ArrowClockwise size={16} className={refreshing ? "lr-spin" : ""} />{refreshing ? "刷新中…" : waiting ? "稍后可刷新" : "刷新数据"}</button></div></div>
    {statusBar}
    {scopeControls}
    <nav className="lr-detail-tabs" aria-label="切换经营指标">{liveMetricIds.map((id) => <Link key={id} href={liveMetricHref(id, search)} aria-current={id === metric ? "page" : undefined}>{liveMetricDefinitions[id].name}</Link>)}</nav>
    <section className="lr-panel lr-detail-summary" aria-label={`${definition.name}概览`}>
      <div className="lr-detail-value"><span>{definition.name}</span>{modules[definition.module].status === "loading" ? <div className="lr-skeleton" role="status" aria-label={`${definition.name}加载中`}><span /><span /></div> : <strong>{current.value}<small>{current.value === "--" ? "" : current.unit}</small></strong>}<span>{status === "ended" ? "本场最终或最新快照" : status === "not_started" ? "未开播 · 暂无经营数据" : current.detail}</span></div>
      <div className="lr-detail-context"><h2>{room?.name ?? "尚未选择直播间"}</h2><p>{snapshot.shops.find((shop) => shop.id === room?.shopId)?.name ?? "--"} · {platform} · {snapshot.scope.date} · 上海时区</p><p>{definition.definition}</p><div className="lr-detail-source"><Info size={14} />{snapshot.source === "MOCK" ? "固定模拟样本，非真实平台数据" : "数据来自现有快照接口"}</div></div>
      <div className="lr-detail-times">{readTime}</div>
    </section>
    {!selected ? <section className="lr-select-guide"><h2>请选择具体直播场次</h2><p>选择后查看同店铺、场次与平台的指标明细。</p></section> : definition.breakdown === "plans" ? <section className="lr-panel" aria-label="关联计划数据明细">
      <header className="lr-panel-heading"><h2><ChartLineUp size={18} />关联计划数据明细</h2><span className="lr-muted">同一统计范围 · {plans.length} 个关联计划</span></header>
      {hasMixedWindows && <p className="lr-inline-notice">归因窗口不一致，汇总GMV与ROI显示 --。各计划保留自己的数据与口径，不能直接合计。</p>}
      <div className="lr-table-scroll"><table><thead><tr><th>计划 / 平台</th><th>日预算</th><th className={metric === "spend" ? "lr-active-column" : ""}>广告消耗</th><th className={metric === "gmv" ? "lr-active-column" : ""}>支付归因GMV</th><th className={metric === "roi" ? "lr-active-column" : ""}>投放ROI</th><th>状态 / 归因口径</th><th>平台更新</th><th>操作</th></tr></thead><tbody>{plans.map((plan) => {
        const account = snapshot.adAccounts.find((item) => item.id === plan.adAccountId);
        const amounts = livePlanAmounts(plan, status === "not_started");
        return <tr key={plan.id}><td><b>{plan.name}</b><small>{account ? connectorNames[account.connectorId] : "--"} · {plan.nativePlanId}</small></td><td>{liveMetricMoney(plan.budgetMinor)}</td><td className={metric === "spend" ? "lr-active-column" : ""}>{liveMetricMoney(amounts.spend)}</td><td className={metric === "gmv" ? "lr-active-column" : ""}>{liveMetricMoney(amounts.gmv)}</td><td className={metric === "roi" ? "lr-active-column" : ""}>{amounts.roi?.toFixed(2) ?? "--"}</td><td><span className={`lr-plan-status ${plan.enabled ? "enabled" : ""}`}>{snapshot.source === "MOCK" ? "模拟 · " : ""}{plan.enabled ? "投放开启" : "投放暂停"}</span><small>{plan.attributionWindow || "口径未提供"}</small></td><td>{formatLiveTime(plan.sourceUpdatedAt)}</td><td><button disabled={!canCreate} onClick={() => onOperation(`调整${plan.name}的日预算`)}>申请调整预算 <ArrowRight size={12} /></button></td></tr>;
      })}</tbody></table>{!plans.length && <p className="lr-table-empty">当前筛选范围暂无关联计划</p>}</div>
      {plansReadTime}<p className="lr-footnote">支付归因GMV不代表直播间全量成交。金额为CNY；ROI沿用既有计算口径。每项计划保留独立更新时间。</p>
    </section> : definition.breakdown === "audience" ? <section className="lr-panel" aria-label="观看数据明细"><header className="lr-panel-heading"><h2><ChartLineUp size={18} />观看数据明细</h2><span className="lr-muted">平台场次快照</span></header><div className="lr-detail-audience">{[{ name: "当前在线", value: metrics.find((item) => item.id === "online")?.value ?? "--" }, { name: "累计观看", value: metrics.find((item) => item.id === "viewers")?.value ?? "--" }, { name: "商品点击", value: count(productClicks) }].map((item) => <div key={item.name}><span>{item.name}</span><strong>{item.value}</strong></div>)}</div><div className="lr-detail-empty"><ChartLineUp size={26} /><h3>分时趋势与流量来源待接入</h3><p>当前接口没有分时在线、进房、离开、观看时长和流量来源明细，暂不绘制趋势或计算变化率。</p></div>{readTime}</section> : <section className="lr-panel" aria-label="成交订单明细"><header className="lr-panel-heading"><h2><ChartLineUp size={18} />成交订单明细</h2><span className="lr-muted">仅展示接口已提供的数据</span></header><div className="lr-detail-empty"><Info size={26} /><h3>订单明细接口待接入</h3><p>当前快照不含订单号、商品、支付时间、订单金额和订单状态，暂不能展示订单列表或计算转化率。</p><p>现有货盘中的商品订单统计没有完整的场次、平台和时间口径，不能作为本场订单明细。</p></div>{readTime}</section>}
    <section className="lr-panel lr-detail-actions" aria-label="指标操作入口"><div><h2><Lightning size={18} />下一步操作</h2><p>从明细发起操作申请，核对对象和风险后进入现有人工审批流程。</p><span><ShieldCheck size={14} />操作按钮只填写指令草稿</span></div><div><button className="button primary" disabled={!selected || !canCreate} onClick={() => onOperation(definition.breakdown === "plans" ? "把预算调到5000" : "看看现在直播间情况")}><Lightning size={16} />{definition.breakdown === "plans" ? "申请投放操作" : "查看直播诊断"}</button>{canManagePlans && <Link href="/app/plans" className="button secondary">投放计划 <ArrowRight size={14} /></Link>}</div></section>
  </div>;
}
