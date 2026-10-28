"use client";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowClockwise, ArrowRight, Bag, CalendarBlank, CaretDown, CaretRight, ChartBar, CheckCircle, Eye, Info, Robot, ShoppingCart, Sparkle, UsersThree, Wallet, WarningCircle } from "@phosphor-icons/react";
import { connectorNames, money, type FrontendSnapshot } from "@/lib/domain";
import type { ReportingScope } from "@/lib/command-job";
import { overviewKey, percentChange, shanghaiDate, shiftDate, validDateRange, type OverviewFilter, type OverviewMetric, type OverviewReport, type PerformanceRow } from "@/lib/overview";
import { loadSnapshotOverview, overviewPlatforms, type OverviewLoader } from "@/services/overview-adapter";
import { invalidateSnapshotRead } from "@/services/snapshot-reader";
import { useWorkspace } from "@/components/provider";
import { MissingData, OverviewDonut, OverviewFunnel, OverviewTrend, Sparkline } from "@/components/overview-charts";
import "@/styles/overview.css";

function OverviewPanel({ title, action, children, className = "", id }: { title: string; action?: ReactNode; children: ReactNode; className?: string; id?: string }) {
  return <section className={`ov-panel ${className}`} aria-label={title} id={id}>
    <div className="ov-panel-heading"><h2>{title}</h2>{action}</div>{children}
  </section>;
}
function DetailLink({ href = "/app/analytics", children = "查看详情" }: { href?: string; children?: ReactNode }) {
  return <Link className="ov-detail-link" href={href}>{children}<CaretRight size={12} /></Link>;
}
function Performance({ rows, kind, onLive }: { rows: PerformanceRow[]; kind: "channel" | "live"; onLive: (id: string) => void }) {
  if (!rows.length) return <MissingData compact title="当前范围暂无数据" detail={kind === "channel" ? "有可用投放来源后展示渠道表现。" : "选择已有数据的店铺与场次查看。"} />;
  return <div className="ov-performance-scroll"><table className="ov-performance">
    <thead><tr><th>{kind === "channel" ? "渠道" : "直播间 / 场次"}</th><th>广告消耗</th><th>支付GMV</th><th>ROI</th><th><span className="sr-only">消耗占比</span></th></tr></thead>
    <tbody>{rows.map((row, index) => <tr key={row.id}>
      <td><div className="ov-performance-name"><span className={`ov-row-icon ov-row-icon-${index % 4}`}>{kind === "live" ? <UsersThree size={16} /> : <ChartBar size={16} />}</span><div>
        {kind === "live" ? <button type="button" onClick={() => onLive(row.id)} title={row.name}>{row.name}</button> : <span title={row.name}>{row.name}</span>}
        <small>{row.detail}</small></div></div></td>
      <td>{row.spend === null ? "--" : money(row.spend)}</td><td>{row.gmv === null ? "--" : money(row.gmv)}</td><td><b>{row.roi?.toFixed(2) ?? "--"}</b></td>
      <td><div className="ov-data-bar" title={row.share === null ? "缺少消耗占比" : `消耗占比 ${(row.share * 100).toFixed(1)}%`}><i style={{ width: `${Math.min(100, (row.share ?? 0) * 100)}%` }} /></div></td>
    </tr>)}</tbody>
  </table></div>;
}
function OverviewMetrics({ report }: { report: OverviewReport }) {
  const points = report.trend.status === "ready" ? report.trend.data : [];
  const core: { key: OverviewMetric; name: string; icon: ReactNode; note: string }[] = [
    { key: "spend", name: "广告消耗", icon: <Wallet size={21} weight="fill" />, note: "同范围实际消耗" },
    { key: "gmv", name: "支付GMV", icon: <Bag size={21} weight="fill" />, note: "支付归因GMV" },
    { key: "roi", name: "投放ROI", icon: <ChartBar size={21} weight="fill" />, note: "归因GMV ÷ 广告消耗" },
  ];
  return <section className="ov-kpis" aria-label="核心经营指标与辅助指标">
    {core.map(({ key, name, icon, note }) => {
      const change = percentChange(report.totals[key], report.previous?.[key] ?? null);
      return <article className={`ov-kpi ov-kpi-${key}`} key={key}>
        <div className="ov-kpi-main"><span className="ov-kpi-icon">{icon}</span><div><h2>{name}</h2><strong title={report.totals[key] === null ? "数据暂不可用" : String(report.totals[key])}>{report.totals[key] === null ? "--" : key === "roi" ? report.totals[key].toFixed(2) : money(report.totals[key])}</strong></div></div>
        <div className="ov-kpi-change">较前一日 <b className={change === null || key === "spend" ? "" : change >= 0 ? "ov-positive" : "ov-negative"}>{change === null ? "--" : `${change > 0 ? "+" : ""}${(change * 100).toFixed(1)}%`}</b><span title={note}>{note}</span></div>
        <Sparkline points={points} metric={key} />
      </article>;
    })}
    {[
      { name: "成交订单", value: report.totals.orders?.toLocaleString("zh-CN") ?? "--", icon: <ShoppingCart size={20} weight="fill" />, tone: "mint", note: "订单数据待接入" },
      { name: "订单转化率", value: report.totals.conversion === null ? "--" : `${(report.totals.conversion * 100).toFixed(2)}%`, icon: <UsersThree size={20} weight="fill" />, tone: "blue", note: "转化口径待接入" },
      { name: "千次曝光成本", value: report.totals.cpm === null ? "--" : money(report.totals.cpm), icon: <Eye size={20} weight="fill" />, tone: "sky", note: "曝光数据待接入" },
    ].map((metric) => <article className={`ov-kpi ov-kpi-aux ov-kpi-${metric.tone}`} key={metric.name}><div className="ov-kpi-main"><span className="ov-kpi-icon">{metric.icon}</span><div><h2>{metric.name}</h2><strong>{metric.value}</strong></div></div><p>{metric.value === "--" ? metric.note : "同一统计范围"}</p></article>)}
  </section>;
}
function OverviewSkeleton() {
  return <div className="ov-loading" role="status" aria-label="正在更新经营数据"><div className="ov-loading-kpis">{Array.from({ length: 6 }, (_, i) => <div key={i} />)}</div><div className="ov-loading-chart"><span className="loading-ring" /><p>正在更新所选范围的经营数据…</p></div></div>;
}
export function OverviewPage({ snapshot, loader = loadSnapshotOverview }: { snapshot: FrontendSnapshot; loader?: OverviewLoader }) {
  const { service, reload, identity } = useWorkspace();
  const router = useRouter();
  const [range, setRange] = useState({ startDate: snapshot.scope.date, endDate: snapshot.scope.date });
  const [draft, setDraft] = useState(range);
  const [pendingScope, setPendingScope] = useState<ReportingScope | null>(null);
  const [filterError, setFilterError] = useState("");
  const [retry, setRetry] = useState(0);
  const [distribution, setDistribution] = useState<"status" | "channel">("status");
  const changing = useRef(false);
  const mounted = useRef(true);
  const insightHeading = useRef<HTMLDivElement>(null);
  const dateDisclosure = useRef<HTMLDetailsElement>(null);
  const scope = pendingScope ?? snapshot.scope;
  const filter = useMemo<OverviewFilter>(() => ({ scope, ...range }), [scope, range]);
  const key = overviewKey(filter);
  const [resource, setResource] = useState<{ key: string; snapshot: FrontendSnapshot; report?: OverviewReport; error?: string } | null>(null);
  const platforms = overviewPlatforms(snapshot, scope.shop_id);
  const report = resource?.key === key && resource.snapshot === snapshot && !pendingScope ? resource.report : undefined;
  const error = resource?.key === key && resource.snapshot === snapshot ? resource.error : undefined;
  const dateLabel = range.startDate === range.endDate ? range.startDate.replaceAll("-", "/") : `${range.startDate.replaceAll("-", "/")} — ${range.startDate.slice(0, 4) === range.endDate.slice(0, 4) ? range.endDate.slice(5).replace("-", "/") : range.endDate.replaceAll("-", "/")}`;
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void loader({ snapshot, filter, signal: controller.signal }).then((data) => {
      if (overviewKey(data.filter) !== key || data.source !== snapshot.source)
        throw new Error("返回的数据与当前筛选范围或数据来源不一致，请刷新重试。");
      if (!controller.signal.aborted) setResource({ key, snapshot, report: data });
    }).catch((e: unknown) => {
      if (!controller.signal.aborted) setResource({ key, snapshot, error: e instanceof Error ? e.message : "经营数据读取失败，请重试。" });
    });
    return () => controller.abort();
  }, [snapshot, filter, key, loader, retry]);

  async function changeScope(patch: Partial<ReportingScope>, navigateToLive = false) {
    if (!service || changing.current) return;
    const next = { ...snapshot.scope, ...patch };
    if (patch.shop_id) {
      next.live_session_id = "all";
      const available = overviewPlatforms(snapshot, next.shop_id);
      if (!available.some((p) => p.id === next.connector_id)) next.connector_id = "all";
    }
    changing.current = true;
    setPendingScope(next); setFilterError("");
    try {
      window.dispatchEvent(new Event("yuliang:scope-changing"));
      invalidateSnapshotRead(service);
      await service.setScope(next);
      await reload();
      if (navigateToLive && mounted.current) router.push("/app/monitor");
    } catch (e) {
      if (mounted.current) setFilterError(e instanceof Error ? e.message : "筛选条件未保存，请重试。");
    } finally {
      changing.current = false;
      window.dispatchEvent(new Event("yuliang:scope-settled"));
      if (mounted.current) setPendingScope(null);
    }
  }
  function changeRange(startDate: string, endDate: string) {
    if (!validDateRange(startDate, endDate)) { setFilterError("请选择有效日期，开始日期不能晚于结束日期。"); return; }
    setRange({ startDate, endDate }); setDraft({ startDate, endDate }); setFilterError("");
    if (dateDisclosure.current) dateDisclosure.current.open = false;
    // Existing Mock scope deliberately accepts only its seeded reporting day.
    // Date ranges stay in this report workspace until a range API is available.
    if (service?.mode === "api" && startDate === endDate && endDate !== snapshot.scope.date)
      void changeScope({ date: endDate });
  }
  const quickRanges = [
    { name: "今日", start: shanghaiDate(), end: shanghaiDate() },
    { name: "昨日", start: shiftDate(shanghaiDate(), -1), end: shiftDate(shanghaiDate(), -1) },
    { name: "近7天", start: shiftDate(shanghaiDate(), -6), end: shanghaiDate() },
    { name: "近30天", start: shiftDate(shanghaiDate(), -29), end: shanghaiDate() },
  ];
  return <div className="ov-workspace">
    <header className="ov-toolbar">
      <div className="ov-title"><h1>经营概况</h1><p>实时掌握经营表现、投放效率与增长趋势。</p></div>
      <div className="ov-filters" aria-label="经营概况统一筛选">
        <details className="ov-date" ref={dateDisclosure}><summary><CalendarBlank size={16} /><span>{dateLabel}</span><CaretDown size={12} /></summary>
          <form className="ov-date-popover" onSubmit={(event) => { event.preventDefault(); changeRange(draft.startDate, draft.endDate); }}>
            <b>选择统计日期</b><div className="ov-date-presets">{quickRanges.map((q) => <button key={q.name} type="button" onClick={() => changeRange(q.start, q.end)}>{q.name}</button>)}</div>
            <label>开始日期<input type="date" aria-label="开始日期" required value={draft.startDate} onChange={(e) => setDraft({ ...draft, startDate: e.target.value })} /></label>
            <label>结束日期<input type="date" aria-label="结束日期" required min={draft.startDate} value={draft.endDate} onChange={(e) => setDraft({ ...draft, endDate: e.target.value })} /></label>
            <button className="ov-primary-button" type="submit">应用日期范围</button><small>Asia/Shanghai · 历史报表尚待接入</small>
          </form>
        </details>
        <label className="ov-select"><span className="sr-only">当前店铺</span><select aria-label="当前店铺" disabled={!!pendingScope} value={scope.shop_id} onChange={(e) => void changeScope({ shop_id: e.target.value })}><option value="all">全部授权店铺</option>{snapshot.shops.filter((s) => identity?.shopIds.includes(s.id)).map((shop) => <option key={shop.id} value={shop.id}>{shop.name}</option>)}</select></label>
        <label className="ov-select"><span className="sr-only">当前直播间</span><select aria-label="当前直播间" disabled={!!pendingScope} value={scope.live_session_id} onChange={(e) => void changeScope({ live_session_id: e.target.value })}><option value="all">全部关联场次</option>{snapshot.liveSessions.filter((live) => scope.shop_id === "all" || live.shopId === scope.shop_id).map((live) => <option value={live.id} key={live.id}>{live.name}</option>)}</select></label>
        <label className="ov-select"><span className="sr-only">广告平台</span><select aria-label="广告平台" disabled={!!pendingScope} value={scope.connector_id} onChange={(e) => void changeScope({ connector_id: e.target.value })}><option value="all">全部已接入平台</option>{scope.connector_id !== "all" && !platforms.some((p) => p.id === scope.connector_id) && <option disabled value={scope.connector_id}>当前平台待核验</option>}{platforms.map((platform) => <option key={platform.id} value={platform.id}>{connectorNames[platform.id]}</option>)}</select></label>
        <button className="ov-primary-button" type="button" disabled={!report} onClick={() => { insightHeading.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); insightHeading.current?.focus({ preventScroll: true }); }}><Sparkle size={17} weight="fill" />数据洞察</button>
      </div>
    </header>
    {filterError && <p className="ov-filter-error" role="alert"><WarningCircle size={16} />{filterError}</p>}
    {error ? <div className="ov-report-error" role="alert"><WarningCircle size={28} /><h2>暂时无法读取经营数据</h2><p>{error}</p><button className="ov-primary-button" type="button" onClick={() => { setResource(null); setRetry((n) => n + 1); }}><ArrowClockwise size={16} />重新加载</button></div> : !report ? <OverviewSkeleton /> : <div className="ov-dashboard" aria-busy="false">
      <OverviewMetrics report={report} />
      <div className="ov-report-context"><span><i className={report.source === "MOCK" ? "ov-source-mock" : "ov-source-api"} />{report.source === "MOCK" ? "MOCK 演示数据 · 非真实经营成果" : "API 数据"} · {dateLabel}</span><span>{report.updatedAt ? `更新于 ${new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(report.updatedAt))}` : "数据待同步"} · Asia/Shanghai</span><span title={report.coverageNote}>{report.coverageNote}</span></div>
      <div className="ov-main-row">
        <OverviewPanel title="整体趋势" className="ov-trend-panel" action={<div className="ov-segmented" aria-label="趋势日期范围">{quickRanges.map((q) => <button type="button" key={q.name} aria-pressed={range.startDate === q.start && range.endDate === q.end} onClick={() => changeRange(q.start, q.end)}>{q.name}</button>)}</div>}><OverviewTrend trend={report.trend} filter={filter} onRetry={() => { setResource(null); setRetry((n) => n + 1); }} /></OverviewPanel>
        <OverviewPanel title="经营洞察" className="ov-insights-panel" action={<span className="ov-data-hint"><Sparkle size={12} />数据提示</span>}>
          <div ref={insightHeading} className="ov-insights" tabIndex={-1} aria-label="经营洞察数据提示">
            {report.insights.slice(0, 4).map((insight) => <Link href={insight.href} key={insight.id} className={`ov-insight ov-insight-${insight.tone}`}><span className="ov-insight-icon">{insight.tone === "warning" ? <WarningCircle size={19} weight="fill" /> : insight.tone === "success" ? <CheckCircle size={19} weight="fill" /> : <ChartBar size={19} weight="fill" />}</span><div><b>{insight.title}</b><p>{insight.detail}</p></div><CaretRight size={14} /></Link>)}
          </div><p className="ov-insight-note"><Info size={12} />基于数据规则生成，非AI因果分析。</p>
        </OverviewPanel>
      </div>
      <div className="ov-analysis-row">
        <OverviewPanel title="渠道表现" action={<DetailLink href="/app/plans" />}><Performance rows={report.channels} kind="channel" onLive={() => {}} /></OverviewPanel>
        <OverviewPanel title="直播间表现" action={<DetailLink href="/app/monitor" />}><Performance rows={report.lives} kind="live" onLive={(id) => void changeScope({ live_session_id: id }, true)} /></OverviewPanel>
        <OverviewPanel title="转化漏斗" action={<span className="ov-panel-caption">整体转化率 <b>{report.totals.conversion === null ? "--" : `${(report.totals.conversion * 100).toFixed(2)}%`}</b></span>}><OverviewFunnel funnel={report.funnel} /></OverviewPanel>
      </div>
      <div className="ov-analysis-row ov-bottom-row">
        <OverviewPanel title="投放计划分布" action={<div className="ov-segmented ov-segmented-small" aria-label="投放计划分布维度"><button type="button" aria-pressed={distribution === "status"} onClick={() => setDistribution("status")}>按计划状态</button><button type="button" aria-pressed={distribution === "channel"} onClick={() => setDistribution("channel")}>按投放渠道</button></div>}><OverviewDonut slices={report.distributions[distribution]} label="广告总消耗" /><p className="ov-module-note">含暂停计划的历史消耗 · 点击分类查看投放计划</p></OverviewPanel>
        <OverviewPanel title="流量来源" action={<DetailLink href="/app/analytics" />}>
          {report.traffic.status === "ready" ? <OverviewDonut slices={report.traffic.data} label="总流量" monetary={false} href="/app/analytics" /> : <div className="ov-traffic-empty"><div className="ov-empty-ring"><span>--<small>总流量</small></span></div><MissingData compact detail={report.traffic.reason} /></div>}
        </OverviewPanel>
        <OverviewPanel title="AI运营状态" action={<DetailLink href="/app/tasks">查看任务</DetailLink>}>
          <div className="ov-ai-layout"><div className="ov-ai-visual"><div className="ov-ai-orbit"><Robot size={32} weight="fill" /><b>{report.ai.running ? "运行中" : "待命中"}</b></div><small>{report.source === "MOCK" ? "Mock 任务状态" : "任务执行状态"}</small></div>
            <div className="ov-ai-counters"><div><b>{report.ai.running}<small>个</small></b><span>运行中任务</span></div><div><b>{report.ai.completed}<small>个</small></b><span>范围内已核验</span></div><div><b>{report.ai.exceptions}<small>个</small></b><span>待处理异常</span></div><div><b>{report.ai.successRate === null ? "--" : `${(report.ai.successRate * 100).toFixed(1)}%`}</b><span>执行成功率</span></div></div>
          </div><div className="ov-ai-footer"><Link href="/app/command">进入 AI驭量中枢<ArrowRight size={13} /></Link><Link href="/app/tasks">待审批 {report.ai.pending}</Link></div>
          <p className="ov-module-note">按范围内更新的任务统计 · 成功率仅含成功与失败的执行任务</p>
        </OverviewPanel>
      </div>
    </div>}
  </div>;
}
