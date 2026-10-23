"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowClockwise, ArrowLeft, ArrowRight, Broadcast, ChartBar, Check, CheckCircle, FileText, FilmStrip, Info, Storefront } from "@phosphor-icons/react";
import { useWorkspace } from "@/components/provider";
import { EmptyState, LoadingState, Panel } from "@/components/ui";
import { connectorNames, type FrontendSnapshot } from "@/lib/domain";
import { analysisKey, analysisShops, dimensionNames, dimensions, selectDimension, validateAnalysisQuery, type AnalysisQuery, type AnalysisResult, type AnalysisTask } from "@/lib/business-analysis";
import type { PublishedReport } from "@/lib/analysis-draft";
import { shiftDate } from "@/lib/reporting-date";
import { abortablePause, authorizedAnalysisSnapshot, createAnalysisAdapter, type AnalysisAdapter } from "@/services/analysis-service";
import { scenarioNames, type AnalysisScenario } from "@/services/analysis-mock";
import { AnalysisReport } from "./report";
import { ReviewHistory } from "./history";
import { ReviewFlowContext, useReviewFlow, type ReviewFlow } from "./flow";
import { ReportEditor, ReportPreview } from "./editor";
import { analysisSelectionHref, ErrorNotice, initialAnalysisQuery } from "./shared";
import "@/styles/business-analysis.css";

const dimensionIcons = { shop: Storefront, live: Broadcast, ads: ChartBar, video: FilmStrip };
const dimensionDescriptions = { shop: "成交、商品与退款，掌握店铺经营全貌。", live: "对比场次与转化，发现直播增长空间。", ads: "核对消耗与归因，识别预算优化机会。", video: "连接内容、点击与成交，找到高效视频。" };
function useResource<T>(load: (signal: AbortSignal) => Promise<T>) {
  const [resource, setResource] = useState<{ data?: T; error?: string }>({});
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal).then(data => { if (!controller.signal.aborted) setResource({ data }); }).catch((e: unknown) => { if (!controller.signal.aborted) setResource({ error: e instanceof Error ? e.message : "读取失败，请稍后重试。" }); });
    return () => controller.abort();
  }, [load, attempt]);
  return { ...resource, retry: () => { setResource({}); setAttempt(attempt + 1); } };
}
function AnalysisSelection({ snapshot, onRun, initial, last }: { snapshot: FrontendSnapshot; adapter: AnalysisAdapter; onRun: (query: AnalysisQuery, scenario?: AnalysisScenario) => Promise<void>; initial: AnalysisQuery; last?: AnalysisResult }) {
  const { reload, service } = useWorkspace();
  const { remember } = useReviewFlow();
  const [query, setQuery] = useState(initial);
  const [multiple, setMultiple] = useState(initial.dimensions.length > 1);
  const [preset, setPreset] = useState("custom");
  const [scenario, setScenario] = useState<AnalysisScenario>("normal");
  const [busy, setBusy] = useState(false), submitting = useRef(false);
  const [refreshing, setRefreshing] = useState(false), [refreshed, setRefreshed] = useState(false);
  const [error, setError] = useState("");
  const shops = analysisShops(snapshot, query.platformId);
  const validQuery = (() => { try { validateAnalysisQuery(query, snapshot); return ""; } catch (e) { return e instanceof Error ? e.message : "请选择完整条件。"; } })();
  const changed = last && analysisKey(last.query) !== analysisKey(query);
  function patch(patch: Partial<AnalysisQuery>) { const next = { ...query, ...patch }; setQuery(next); remember({ query: next }); setError(""); }
  async function run() {
    if (submitting.current || validQuery) return;
    submitting.current = true; setBusy(true); setError("");
    try { await onRun(query, scenario); } catch (e) { setError(e instanceof Error ? e.message : "创建分析任务失败，请重新分析。"); } finally { submitting.current = false; setBusy(false); }
  }
  return <div className="ba-workspace ba-selection"><header className="ba-report-heading"><div><span className="ba-eyebrow">数据复盘 / 经营洞察</span><h2>分析条件</h2><p>选定经营范围，连接数据与业务，找到下一步增长方向。</p></div><div className="ba-actions"><button className="button secondary" disabled={refreshing || busy} onClick={async () => { setRefreshing(true); setError(""); try { const actor = await reload(); if (!actor) throw new Error("来源刷新未成功，请检查工作区连接状态。"); setRefreshed(true); } catch (e) { setError(e instanceof Error ? e.message : "刷新失败"); } finally { setRefreshing(false); } }}><ArrowClockwise size={15} />{refreshing ? "刷新中…" : "刷新数据"}</button></div></header>
    {service?.mode === "mock" && <div className="ba-source-banner demo"><Info size={18} /><div><b>演示数据 · 固定测试样本</b><span>样本覆盖 2026-09-01 至 2026-10-21，可验证成功、部分数据、无数据与失败流程。</span></div><label>演示场景<select aria-label="演示场景" value={scenario} onChange={event => setScenario(event.target.value as AnalysisScenario)} disabled={busy}>{Object.entries(scenarioNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label></div>}
    {(changed || refreshed) && <div className="ba-change-notice" role="status"><Info size={18} /><span>{changed ? "条件已变化，需要重新执行分析。" : "数据已刷新，需要点击开始经营分析生成新结果。"}已生成报告保留原始条件与数据快照。</span>{last && <Link href={`/app/reports?result=${encodeURIComponent(last.id)}`}>查看原报告</Link>}</div>}
    <Panel title="选择经营范围" detail="平台与店铺按已授权关联关系联动过滤"><fieldset className="ba-filter-fields" disabled={busy}><label>广告平台<select aria-label="分析广告平台" value={query.platformId} onChange={event => { const platformId = event.target.value as AnalysisQuery["platformId"], options = analysisShops(snapshot, platformId); patch({ platformId, shopId: options.some(shop => shop.id === query.shopId) ? query.shopId : options[0]?.id ?? "" }); }}><option value="all">全部关联平台</option>{snapshot.connectors.filter(connector => analysisShops(snapshot, connector.id).length > 0).map(connector => <option key={connector.id} value={connector.id}>{connectorNames[connector.id]}</option>)}</select></label><label>经营店铺<select aria-label="分析经营店铺" value={query.shopId} onChange={event => patch({ shopId: event.target.value })}>{!shops.length && <option value="">无关联店铺</option>}{shops.map(shop => <option key={shop.id} value={shop.id}>{shop.name}</option>)}</select></label><label>统计周期<select aria-label="分析统计周期" value={preset} onChange={event => { const value = event.target.value; setPreset(value); if (value !== "custom") patch({ startDate: shiftDate(snapshot.scope.date, -(Number(value) - 1)), endDate: snapshot.scope.date }); }}><option value="1">统计日</option><option value="7">近7天</option><option value="14">近14天</option><option value="21">近21天</option><option value="custom">自定义周期</option></select></label><label>开始日期<input aria-label="分析开始日期" type="date" value={query.startDate} onChange={event => { setPreset("custom"); patch({ startDate: event.target.value }); }} /></label><label>结束日期<input aria-label="分析结束日期" type="date" value={query.endDate} onChange={event => { setPreset("custom"); patch({ endDate: event.target.value }); }} /></label></fieldset><p className="ba-caption">Asia/Shanghai · 刷新数据只更新来源，执行分析才生成独立报告。</p></Panel>
    <section className="ba-dimension-section"><div className="ba-title-line"><div><h2>选择分析维度</h2><p>{multiple ? "选择 1–4 个维度，自动增加跨业务关联分析。" : "聚焦一个维度，深入分析业务表现。"}</p></div><div className="ba-segment" aria-label="分析维度选择模式"><button aria-pressed={!multiple} disabled={busy} onClick={() => { setMultiple(false); patch({ dimensions: query.dimensions.slice(0, 1) }); }}>单选</button><button aria-pressed={multiple} disabled={busy} onClick={() => setMultiple(true)}>多选</button></div></div><div className="ba-dimensions">{dimensions.map(dimension => { const Icon = dimensionIcons[dimension], selected = query.dimensions.includes(dimension); return <button className={`ba-dimension ${selected ? "selected" : ""}`} key={dimension} disabled={busy} aria-pressed={selected} onClick={() => patch({ dimensions: selectDimension(query.dimensions, dimension, multiple) })}><span className="ba-dimension-top"><span className={`ba-dimension-icon ${dimension}`}><Icon size={26} weight="duotone" /></span><span className="ba-selection-check">{selected && <Check size={13} weight="bold" />}</span></span><h3>{dimensionNames[dimension]}</h3><p>{dimensionDescriptions[dimension]}</p><span className="ba-dimension-meta">{dimension === "shop" ? "经营结果 · 商品贡献" : dimension === "live" ? "场次表现 · 转化效率" : dimension === "ads" ? "归因口径 · 计划表现" : "内容引流 · 成交贡献"}<ArrowRight size={14} /></span></button>; })}</div></section>
    <div className="ba-execution-bar"><div><b>已选择 {query.dimensions.length} 个分析维度</b><p>{query.dimensions.length ? query.dimensions.map(dimension => dimensionNames[dimension]).join("、") : "请至少选择一个分析维度"}</p><small>{snapshot.shops.find(shop => shop.id === query.shopId)?.name ?? "未选择店铺"} · {query.platformId === "all" ? "全部关联平台" : connectorNames[query.platformId]} · {query.startDate} — {query.endDate}</small></div><div><button className="button primary ba-start-button" disabled={!!validQuery || busy} onClick={() => void run()}>{busy ? <><span className="loading-ring" />正在创建分析任务…</> : <>开始经营分析<ArrowRight size={18} /></>}</button>{validQuery && <small className="ba-validation" role="status">{validQuery}</small>}</div></div>
    {error && <ErrorNotice message={error} retry={() => void run()} />}
    <div className="ba-process-guide"><span><CheckCircle size={17} />选择经营范围</span><ArrowRight size={14} /><span>执行分析</span><ArrowRight size={14} /><span>查看完整报告</span><ArrowRight size={14} /><span>编辑复盘与交付</span></div>
  </div>;
}
function TaskProgress({ id, adapter, onRun }: { id: string; adapter: AnalysisAdapter; onRun: (query: AnalysisQuery) => Promise<void> }) {
  const router = useRouter();
  const [task, setTask] = useState<AnalysisTask | null>(null), [error, setError] = useState(""), [retry, setRetry] = useState(0), [running, setRunning] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    async function poll() {
      const start = Date.now();
      while (!controller.signal.aborted) {
        const next = await adapter.getTask(id, controller.signal);
        if (controller.signal.aborted) return;
        setTask(next);
        if (next.status === "failed") { setError(next.error ?? "分析执行失败，请重新分析。"); return; }
        if (next.status === "succeeded" && next.resultId) { router.replace(`/app/reports?result=${encodeURIComponent(next.resultId)}`); return; }
        if (Date.now() - start > 120000) throw new Error("分析仍在进行，已暂停前端轮询。可继续查询同一任务，不会重复执行。");
        await abortablePause(1000, controller.signal);
      }
    }
    void poll().catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "读取任务失败"); });
    return () => controller.abort();
  }, [id, adapter, router, retry]);
  return <div className="ba-workspace"><header className="ba-report-heading"><div><span className="ba-eyebrow">数据复盘 / 执行任务</span><h1>{error ? "分析需要处理" : "正在执行经营分析"}</h1><p>当前任务独立保存，刷新页面可继续查询。</p></div></header><Panel title="分析执行进度"><div className="ba-task-progress">{!error && <span className="loading-ring" />}<b>{task?.status === "failed" ? "分析失败" : task?.status === "succeeded" ? "分析完成，正在打开报告" : "汇总指标 · 对齐统计口径 · 生成经营诊断"}</b><progress value={task?.progress ?? 0} max={100} /><small>任务 {id} · {task?.progress ?? 0}%</small>{task && <p>{task.query.dimensions.map(dimension => dimensionNames[dimension]).join(" / ")}<br />{task.query.startDate} — {task.query.endDate}</p>}</div>{error && <ErrorNotice message={error} />}<div className="ba-actions">{task?.status === "failed" ? <button className="button primary" disabled={running} onClick={async () => { setRunning(true); try { await onRun(task.query); } catch (e) { setError(e instanceof Error ? e.message : "重试失败"); } finally { setRunning(false); } }}>重新分析<ArrowRight size={16} /></button> : error && <button className="button primary" onClick={() => { setError(""); setRetry(retry + 1); }}>继续查询任务</button>}<Link className="button secondary" href={task ? analysisSelectionHref(task.query) : "/app/reports"}><ArrowLeft size={15} />返回调整条件</Link></div></Panel></div>;
}
function ResultResource({ id, adapter, onRun }: { id: string; adapter: AnalysisAdapter; onRun: (query: AnalysisQuery) => Promise<void> }) {
  const load = useCallback((signal: AbortSignal) => adapter.getResult(id, signal), [id, adapter]), resource = useResource(load);
  const { remember } = useReviewFlow();
  useEffect(() => { if (resource.data) remember({ resultId: resource.data.id, query: resource.data.query }); }, [resource.data, remember]);
  return resource.data ? <AnalysisReport result={resource.data} adapter={adapter} onRun={onRun} /> : resource.error ? <><ErrorNotice message={resource.error} retry={resource.retry} /><Link className="button secondary" href="/app/reports">返回经营分析</Link></> : <LoadingState />;
}
function DraftResource({ id, adapter, preview, delivery = false }: { id: string; adapter: AnalysisAdapter; preview: boolean; delivery?: boolean }) {
  const load = useCallback((signal: AbortSignal) => adapter.getDraft(id, signal), [id, adapter]), resource = useResource(load);
  const { remember } = useReviewFlow();
  useEffect(() => { if (resource.data) remember({ draftId: resource.data.id, resultId: resource.data.analysisId, query: resource.data.query }); }, [resource.data, remember]);
  return resource.data ? preview ? <><Link className="button secondary" href={`/app/reports?draft=${encodeURIComponent(id)}`}>返回编辑器</Link><ReportPreview draft={resource.data} /></> : <ReportEditor key={id} initial={resource.data} adapter={adapter} delivery={delivery} /> : resource.error ? <ErrorNotice message={resource.error} retry={resource.retry} /> : <LoadingState />;
}
function PublicationResource({ id, adapter }: { id: string; adapter: AnalysisAdapter }) {
  const load = useCallback((signal: AbortSignal) => adapter.getPublished(id, signal), [id, adapter]), resource = useResource<PublishedReport>(load);
  return resource.data ? <><header className="ba-report-heading"><div><h1>已发布的客户阅读版本</h1><p>{resource.data.source === "MOCK" ? "演示数据 · 当前浏览器内的固定快照。公网发布待后端接入。" : "已发布版本由后端保存。"}</p></div><Link className="button secondary" href="/app/reports">返回复盘列表</Link></header>{resource.data.snapshot ? <ReportPreview draft={resource.data.snapshot} /> : resource.data.url ? <a className="button primary" href={resource.data.url} target="_blank" rel="noreferrer">打开客户网页</a> : <EmptyState title="未返回客户网页" />}</> : resource.error ? <ErrorNotice message={resource.error} retry={resource.retry} /> : <LoadingState />;
}
export function AnalysisWorkspace({ snapshot: rawSnapshot }: { snapshot: FrontendSnapshot }) {
  const { service, identity } = useWorkspace();
  const params = useSearchParams(), router = useRouter();
  const snapshot = useMemo(() => identity ? authorizedAnalysisSnapshot(rawSnapshot, identity) : rawSnapshot, [rawSnapshot, identity]);
  const adapter = useMemo(() => service && identity ? createAnalysisAdapter(service, snapshot, identity) : null, [service, snapshot, identity]);
  const actorKey = `${identity?.tenantId}:${identity?.userId}`;
  const flowKey = `yuliang:review-flow:v1:${actorKey}`;
  const [flow, setFlow] = useState<ReviewFlow>({});
  const flowRef = useRef<ReviewFlow>({});
  const [restored, setRestored] = useState(false);
  const [history, setHistory] = useState(params.get("view") === "history");
  const submitting = useRef(false);
  const routeKey = params.toString();
  useEffect(() => { submitting.current = false; }, [routeKey]);
  const remember = useCallback((patch: Partial<ReviewFlow>) => {
    const next = { ...flowRef.current, ...patch }; flowRef.current = next; setFlow(next);
    try { window.sessionStorage.setItem(flowKey, JSON.stringify(next)); } catch { /* Navigation still works when browser session storage is unavailable. */ }
  }, [flowKey]);
  useEffect(() => {
    let active = true;
    void Promise.resolve().then(() => {
      if (!active) return;
      try { const raw = window.sessionStorage.getItem(flowKey); if (raw) remember(JSON.parse(raw) as ReviewFlow); } catch { /* Keep current URL as the authoritative restore target. */ }
      setRestored(true);
    });
    return () => { active = false; };
  }, [flowKey, remember]);
  const onRun = useCallback(async (query: AnalysisQuery, scenario?: AnalysisScenario) => {
    if (!adapter || submitting.current) return;
    submitting.current = true;
    try {
      const pendingKey = `yuliang:analysis-pending:${actorKey}`, serialized = JSON.stringify([analysisKey(query), scenario ?? "normal"]);
      const raw = window.sessionStorage.getItem(pendingKey), previous = raw ? JSON.parse(raw) as { query: string; key: string } : null;
      const key = previous?.query === serialized ? previous.key : crypto.randomUUID();
      window.sessionStorage.setItem(pendingKey, JSON.stringify({ query: serialized, key }));
      const task = await adapter.createTask(query, key, new AbortController().signal, scenario);
      window.sessionStorage.removeItem(pendingKey); remember({ query });
      router.push(`${analysisSelectionHref(query)}&task=${encodeURIComponent(task.id)}`);
    } catch (error) { submitting.current = false; throw error; }
  }, [adapter, actorKey, router, remember]);
  if (!adapter || !identity || !restored) return <LoadingState />;
  const draft = params.get("draft"), preview = params.get("preview"), published = params.get("published"), result = params.get("result"), task = params.get("task");
  const currentStep = draft || preview ? 2 : published ? 3 : result || task ? 1 : 0;
  if (published) return <PublicationResource key={published} id={published} adapter={adapter} />;
  if (preview) return <DraftResource key={preview} id={preview} adapter={adapter} preview />;
  const steps = [
    { name: "分析条件", href: flow.query ? analysisSelectionHref(flow.query) : "/app/reports" },
    { name: "分析报告", href: flow.resultId ? `/app/reports?result=${encodeURIComponent(flow.resultId)}` : null },
    { name: "报告编辑", href: flow.draftId ? `/app/reports?draft=${encodeURIComponent(flow.draftId)}` : null },
    { name: "客户交付", href: flow.draftId ? `/app/reports?draft=${encodeURIComponent(flow.draftId)}&delivery=1` : null },
  ];
  return <ReviewFlowContext.Provider value={{ flow, remember, openHistory: () => setHistory(true) }}><div className="ba-workspace ba-unified">
    <header className="ba-workbench-heading"><div><h1>数据复盘</h1><p>从经营分析到客户交付，让每一轮经营都有据可依。</p></div><button className="button secondary" onClick={() => setHistory(true)}><FileText size={16} />历史报告</button></header>
    <nav className="ba-workflow-steps" aria-label="数据复盘工作流">{steps.map((step, index) => step.href ? <Link key={step.name} href={step.href} aria-current={!history && currentStep === index ? "step" : undefined} onClick={() => setHistory(false)}><span>{index + 1}</span>{step.name}</Link> : <span key={step.name} aria-disabled="true"><i>{index + 1}</i>{step.name}</span>)}</nav>
    <div hidden={history} className="ba-workflow-body">{draft ? <DraftResource key={`${draft}-${params.get("delivery") ?? ""}`} id={draft} adapter={adapter} preview={false} delivery={params.get("delivery") === "1"} /> : result ? <ResultResource key={result} id={result} adapter={adapter} onRun={onRun} /> : task ? <TaskProgress key={task} id={task} adapter={adapter} onRun={onRun} /> : <SelectionResource key={params.toString()} snapshot={snapshot} adapter={adapter} onRun={onRun} params={new URLSearchParams(params.toString())} />}</div>
    {history && <ReviewHistory adapter={adapter} close={() => setHistory(false)} />}
  </div></ReviewFlowContext.Provider>;
}
function SelectionResource({ snapshot, adapter, onRun, params }: { snapshot: FrontendSnapshot; adapter: AnalysisAdapter; onRun: (query: AnalysisQuery, scenario?: AnalysisScenario) => Promise<void>; params: URLSearchParams }) {
  const { flow } = useReviewFlow();
  const [last, setLast] = useState<AnalysisResult | undefined>();
  useEffect(() => { const controller = new AbortController(); void adapter.listResults(controller.signal).then(results => { if (!controller.signal.aborted) setLast(results[0]); }).catch(() => { /* The explicit history view exposes read errors; selection remains usable. */ }); return () => controller.abort(); }, [adapter]);
  return <AnalysisSelection snapshot={snapshot} adapter={adapter} onRun={onRun} initial={params.has("shop") ? initialAnalysisQuery(params, snapshot) : flow.query ?? initialAnalysisQuery(params, snapshot)} last={last} />;
}
