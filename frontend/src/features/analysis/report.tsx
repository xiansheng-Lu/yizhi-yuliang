"use client";
import { useState } from "react";
import Link from "next/link";
import { ArrowClockwise, ArrowLeft, ArrowRight, CheckCircle, FileText, Info, WarningCircle } from "@phosphor-icons/react";
import { Badge, Panel } from "@/components/ui";
import { dimensionNames, dimensions, diagnoseAnalysis, formatAnalysisValue, metricStatusNames, pointValue, type AnalysisInsight, type AnalysisMetric, type AnalysisQuery, type AnalysisResult, type AnalysisRow, type AnalysisRules, type AnalysisSection } from "@/lib/business-analysis";
import { percentChange } from "@/lib/reporting-date";
import { createReportDraft, reportPeriodIssue, queryForReviewPeriod, reviewTemplates, reviewPeriodNames, type ReviewPeriod } from "@/lib/analysis-draft";
import type { AnalysisAdapter } from "@/services/analysis-service";
import { AnalysisTable, AnalysisTrend } from "./charts";
import { AnalysisModal, analysisSelectionHref, ErrorNotice } from "./shared";
import { useRouter } from "next/navigation";

export function MetricGrid({ metrics, compact = false }: { metrics: AnalysisMetric[]; compact?: boolean }) {
  return <div className={`ba-metrics ${compact ? "ba-metrics-compact" : ""}`}>{metrics.map(metric => {
    const change = percentChange(metric.value, metric.previous);
    return <article className="ba-metric" key={metric.id}><div><span>{metric.name}</span><Info size={13} aria-label={metric.definition} /></div><strong>{formatAnalysisValue(metric.value, metric.unit)}</strong>{metric.status !== "ready" ? <small className="ba-unavailable" title={metric.reason}>{metricStatusNames[metric.status]}</small> : <small>较上期 {change === null ? "暂无对比" : `${change >= 0 ? "+" : ""}${(change * 100).toFixed(1)}%`}</small>}<details><summary>统计口径</summary><p>{metric.definition}{metric.reason ? `。${metric.reason}` : ""}</p></details></article>;
  })}</div>;
}
function Specialist({ section, result, openRow }: { section: AnalysisSection; result: AnalysisResult; openRow: (row: AnalysisRow, metrics: AnalysisMetric[]) => void }) {
  const [topic, setTopic] = useState(0);
  const [metricId, setMetricId] = useState(section.metricIds[0]);
  const metrics = result.metrics.filter(metric => section.metricIds.includes(metric.id));
  const selected = metrics.find(metric => metric.id === metricId) ?? metrics[0];
  const related = section.topics[topic];
  const rows = topic === 4 && section.dimension === "ads" ? section.rows.filter(row => {
    const roi = result.metrics.find(metric => metric.id === "ads.roi");
    const value = roi ? pointValue(row.values, roi) : null;
    return value !== null && value < result.rules.roiFloor && (row.values["ads.spend"] ?? 0) >= result.rules.minSpendMinor;
  }) : section.rows;
  return <Panel title={section.title} detail="同一平台、店铺与统计周期 · 明细按业务对象展开">
    <div className="ba-tabs" role="tablist" aria-label={`${section.title}专项主题`}>{section.topics.map((name, index) => <button key={name} role="tab" aria-selected={topic === index} onClick={() => { setTopic(index); const key = name.includes("退款") ? "shop.refund" : name.includes("客单") ? "shop.aov" : name.includes("ROI") ? "ads.roi" : name.includes("转化") || name.includes("低效直播") ? section.dimension === "live" ? "live.conversion" : "video.conversion" : name.includes("播放") ? "video.views" : undefined; if (key && metrics.some(metric => metric.id === key)) setMetricId(key); }}>{name}</button>)}</div>
    <div role="tabpanel" aria-label={related} className="ba-specialist-body">
      <div className="ba-section-context"><h3>{related}</h3><label>观察指标<select aria-label={`${section.title}观察指标`} value={selected?.id ?? ""} onChange={event => setMetricId(event.target.value)}>{metrics.map(metric => <option key={metric.id} value={metric.id}>{metric.name}</option>)}</select></label></div>
      <MetricGrid metrics={metrics.slice(0, 3)} compact />
      {selected && <AnalysisTrend points={result.points} metrics={[selected]} insights={result.insights} />}
      {section.dimension === "live" && topic === 2 && <div className="ba-funnel">{["live.viewers", "live.conversion"].map(id => { const metric = metrics.find(m => m.id === id); return metric && <div key={id}><span>{metric.name}</span><b>{formatAnalysisValue(metric.value, metric.unit)}</b></div>; })}<p>仅展示接口提供的观看与成交比例；曝光、进房、点击环节未接入。</p></div>}
      <AnalysisTable rows={rows} metrics={metrics.slice(0, 6)} onSelect={row => openRow(row, metrics)} />
      <div className="ba-conclusion"><CheckCircle size={17} /><div><b>本项结论</b><p>{section.conclusion}</p><b>建议操作</b><p>{section.advice}</p></div></div>
      {section.limitation && <p className="ba-data-note"><Info size={15} />{section.limitation}</p>}
    </div>
  </Panel>;
}
function Insights({ insights, kind }: { insights: AnalysisInsight[]; kind: AnalysisInsight["kind"] }) {
  const rows = insights.filter(insight => insight.kind === kind);
  return rows.length ? <div className="ba-insights">{rows.map(insight => <article key={insight.id}><div><Badge tone={insight.priority === "P1" ? "warning" : "blue"}>{insight.priority}</Badge><h3>{insight.title}</h3></div><p>{insight.evidence}</p>{kind === "recommendation" && <dl><div><dt>优化方向</dt><dd>{insight.direction}</dd></div><div><dt>建议操作</dt><dd>{insight.action}</dd></div><div><dt>验证指标</dt><dd>{insight.verificationMetric}</dd></div></dl>}</article>)}</div> : <p className="ba-no-insight">{kind === "anomaly" ? "没有满足当前规则且具备有效比较基准的异常；不代表经营没有风险。" : "当前数据不足，暂无有依据的结论或建议。"}</p>;
}
export function AnalysisReport({ result, adapter, onRun }: { result: AnalysisResult; adapter: AnalysisAdapter; onRun: (query: AnalysisQuery) => Promise<void> }) {
  const router = useRouter();
  const [metricId, setMetricId] = useState(result.metrics.find(metric => metric.status === "ready")?.id ?? result.metrics[0]?.id);
  const [weekly, setWeekly] = useState(false);
  const [compare, setCompare] = useState("none");
  const [generating, setGenerating] = useState(false);
  const [settings, setSettings] = useState(false);
  const [period, setPeriod] = useState<ReviewPeriod>("weekly");
  const periodIssue = reportPeriodIssue(result.query, period);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<{ row: AnalysisRow; metrics: AnalysisMetric[] } | null>(null);
  const [detailDate, setDetailDate] = useState("");
  const [ruleDraft, setRuleDraft] = useState<AnalysisRules>({ ...result.rules });
  const [rules, setRules] = useState<AnalysisRules>({ ...result.rules });
  const [ruleError, setRuleError] = useState("");
  const metric = result.metrics.find(metric => metric.id === metricId);
  const insights = result.insightSource === "rules" ? diagnoseAnalysis(result, rules) : result.insights;
  const effectiveResult = { ...result, insights, rules };
  async function rerun() { if (busy) return; setBusy(true); setError(""); try { await onRun(result.query); } catch (e) { setError(e instanceof Error ? e.message : "创建分析失败"); } finally { setBusy(false); } }
  async function generate() {
    if (generating) return;
    if (periodIssue) { setError(periodIssue); return; }
    setGenerating(true); setError("");
    try {
      const draft = createReportDraft(effectiveResult, period, crypto.randomUUID());
      const saved = await adapter.saveDraft(draft, new AbortController().signal);
      router.push(`/app/reports?draft=${encodeURIComponent(saved.id)}`);
    } catch (e) { setError(e instanceof Error ? e.message : "生成草稿失败，请重试"); } finally { setGenerating(false); }
  }
  return <div className="ba-workspace">
    <header className="ba-report-heading"><div><span className="ba-eyebrow">经营决策 / 分析结果</span><h2>经营分析报告</h2><p>基于当前经营数据，识别增长机会、经营异常与优化方向。</p></div><div className="ba-actions"><button className="button secondary" disabled={busy} onClick={() => void rerun()}><ArrowClockwise size={16} />{busy ? "创建任务中…" : "重新分析"}</button><Link className="button secondary" href={analysisSelectionHref(result.query)}><ArrowLeft size={16} />返回调整条件</Link><button className="button primary" onClick={() => setSettings(true)} disabled={result.completeness === "empty"}><FileText size={16} />生成复盘报告</button></div></header>
    {error && <ErrorNotice message={error} />}
    <div className={`ba-source-banner ${result.source === "MOCK" ? "demo" : ""}`}><Info size={17} /><div><b>{result.source === "MOCK" ? "演示数据 · 固定测试样本" : "平台经营数据"}</b><span>{result.coverage}</span></div><Badge tone={result.completeness === "complete" ? "green" : "warning"}>{result.completeness === "complete" ? "数据完整" : result.completeness === "partial" ? "部分数据" : "暂无数据"}</Badge></div>
    <section className="ba-report-meta" aria-label="分析报告信息"><div><small>经营店铺</small><b>{result.shopName}</b></div><div><small>广告平台</small><b>{result.platformName}</b></div><div><small>统计周期</small><b>{result.query.startDate} — {result.query.endDate}</b></div><div><small>本次分析维度</small><b>{result.query.dimensions.map(dimension => dimensionNames[dimension].replace("分析", "")).join(" / ")}</b></div><div><small>数据更新时间</small><b>{result.updatedAt ? new Date(result.updatedAt).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "未返回"}</b></div><div><small>分析方式</small><b>{result.insightSource === "rules" ? "基础规则分析" : "后端AI诊断"} · Asia/Shanghai</b></div></section>
    <nav className="ba-report-nav" aria-label="报告章节"><a href="#ba-core">核心指标</a><a href="#ba-trend">经营趋势</a><a href="#ba-specialist">专项分析</a>{result.query.dimensions.length > 1 && <a href="#ba-cross">跨业务关联</a>}<a href="#ba-diagnosis">经营诊断</a></nav>
    <section id="ba-core" className="ba-anchor"><div className="ba-title-line"><h2>经营核心指标</h2><span>按本次所选维度展示 · 金额单位人民币</span></div>{result.query.dimensions.map(dimension => <div className="ba-metric-group" key={dimension}><h3>{dimensionNames[dimension]}</h3><MetricGrid metrics={result.metrics.filter(metric => metric.dimension === dimension)} /></div>)}<p className="ba-data-note"><Info size={15} />{result.attribution}</p></section>
    <div id="ba-trend" className="ba-anchor"><Panel title="经营趋势分析" detail="按同一统计口径观察变化，点击时间点进入业务明细" action={<Badge tone="blue">可交互图表</Badge>}><div className="ba-chart-controls"><label>趋势指标<select aria-label="经营趋势指标" value={metricId} onChange={event => setMetricId(event.target.value)}>{result.metrics.map(metric => <option key={metric.id} value={metric.id}>{metric.name}{metric.status !== "ready" ? ` · ${metricStatusNames[metric.status]}` : ""}</option>)}</select></label><div className="ba-segment" aria-label="趋势粒度"><button aria-pressed={!weekly} onClick={() => setWeekly(false)}>按日</button><button aria-pressed={weekly} onClick={() => setWeekly(true)}>按周</button></div><label>对比周期<select aria-label="趋势对比周期" value={compare} onChange={event => setCompare(event.target.value)}><option value="none">不对比</option><option value="previous" disabled={!result.comparison.previous}>环比{!result.comparison.previous ? "（无完整对比周期）" : ""}</option><option value="yearAgo" disabled={!result.comparison.yearAgo}>同比{!result.comparison.yearAgo ? "（未接入）" : ""}</option></select></label></div>{metric && <AnalysisTrend points={result.points} metrics={[metric]} insights={insights} weekly={weekly} comparisonPoints={compare === "previous" ? result.previousPoints : compare === "yearAgo" ? result.yearAgoPoints : []} onDetail={setDetailDate} />}{result.comparison.previous && <p className="ba-caption">有效环比基准：{result.comparison.previous.startDate} — {result.comparison.previous.endDate}。同比按后端返回的有效对比周期展示。</p>}</Panel></div>
    <section id="ba-specialist" className="ba-anchor"><div className="ba-title-line"><h2>专项业务分析</h2><span>指标 → 趋势 → 明细 → 结论与建议</span></div><div className="ba-specialists">{dimensions.filter(dimension => result.query.dimensions.includes(dimension)).map(dimension => { const section = result.sections.find(section => section.dimension === dimension); return section ? <Specialist key={section.id} section={section} result={effectiveResult} openRow={(row, metrics) => setDetail({ row, metrics })} /> : <Panel key={dimension} title={dimensionNames[dimension]}><p>该专项模块未返回，待后端接入。</p></Panel>; })}</div></section>
    {result.query.dimensions.length > 1 && <section id="ba-cross" className="ba-anchor"><div className="ba-title-line"><h2>跨业务关联分析</h2><Badge tone="blue">{result.query.dimensions.length} 维度联合观察</Badge></div><div className="ba-cross-note"><Info size={18} /><p>成交指标存在交叉，分别呈现；趋势相关性不证明因果。只有同口径订单级关联和去重证据支持时，才展示可核验的归因贡献。</p></div>{result.sections.filter(section => section.dimension === "cross").map(section => { const metrics = section.metricIds.map(id => result.metrics.find(metric => metric.id === id)).filter((m): m is AnalysisMetric => !!m); return <Panel key={section.id} title={section.title} detail={section.topics.join(" · ")}><AnalysisTrend points={result.points} metrics={metrics} normalized /><p>{section.conclusion}</p><p className="ba-data-note"><WarningCircle size={15} />{section.limitation}</p>{section.rows.length > 0 && <AnalysisTable rows={section.rows} metrics={metrics} onSelect={row => setDetail({ row, metrics })} />}<p><b>验证方向：</b>{section.advice}</p></Panel>; })}{!result.sections.some(section => section.dimension === "cross") && <Panel title="跨业务关联数据未返回"><p>已选择多维度，后端尚未提供同周期关联模块。</p></Panel>}</section>}
    <section id="ba-diagnosis" className="ba-anchor"><div className="ba-title-line"><h2>经营诊断结论</h2><Badge tone="blue">{result.insightSource === "rules" ? "规则分析 · 非AI模型" : "后端AI分析"}</Badge></div>{result.insightSource === "rules" && <details className="ba-rule-settings"><summary>诊断规则与比较基准（可配置）</summary><p>下降与上升使用上一完整等长周期；图中每日消耗异常使用前一日基准。修改仅用于本次前端诊断，不修改原始数据。</p><div className="ba-rule-fields">{([{ key: "declinePercent", label: "下降阈值（%）" }, { key: "spendRisePercent", label: "上升阈值（%）" }, { key: "roiFloor", label: "低效ROI基准" }, { key: "minSpendMinor", label: "最低消耗样本（元）" }, { key: "minViews", label: "最低播放样本" }] as const).map(field => <label key={field.key}>{field.label}<input type="number" min="0.01" step="any" value={field.key === "minSpendMinor" ? ruleDraft[field.key] / 100 : ruleDraft[field.key]} onChange={event => setRuleDraft({ ...ruleDraft, [field.key]: Number(event.target.value) * (field.key === "minSpendMinor" ? 100 : 1) })} /></label>)}</div><button className="button secondary" onClick={() => { if (Object.values(ruleDraft).some(v => !Number.isFinite(v) || v <= 0) || ruleDraft.declinePercent > 100) { setRuleError("请输入大于0的有效阈值；下降比例不得超过100%。"); return; } setRules({ ...ruleDraft }); setRuleError(""); }}>应用诊断规则</button>{ruleError && <ErrorNotice message={ruleError} />}</details>}<div className="ba-diagnosis-grid">{([{ kind: "conclusion", title: "核心经营结论" }, { kind: "anomaly", title: "经营异常" }, { kind: "opportunity", title: "增长机会" }, { kind: "recommendation", title: "优化建议" }] as const).map(group => <Panel key={group.kind} title={group.title}><Insights insights={insights} kind={group.kind} /></Panel>)}</div></section>
    <div className="ba-report-cta"><div><h2>把分析变成下一轮行动</h2><p>复制本次结果为独立复盘草稿，编辑、审核后交付客户。</p></div><button className="button primary" onClick={() => setSettings(true)} disabled={result.completeness === "empty"}>生成复盘报告<ArrowRight size={16} /></button></div>
    {settings && <AnalysisModal title="生成复盘设置" close={() => { if (!generating) setSettings(false); }}><p>复盘将继承店铺、平台、指标、图表、诊断及分析结论。</p><div className="ba-period-options">{reviewTemplates.map(value => <button key={value} aria-pressed={period === value} onClick={() => setPeriod(value)}>{reviewPeriodNames[value]}</button>)}</div><p className="ba-caption">日报为1天，周报为连续7天，月报为完整自然月。当前分析周期：{result.query.startDate} — {result.query.endDate}。</p>{periodIssue && <div className="ba-period-warning"><ErrorNotice message={periodIssue} /><p>建议统计周期：{queryForReviewPeriod(result.query, period).startDate} — {queryForReviewPeriod(result.query, period).endDate}。执行后保留本报告。</p><button className="button secondary" disabled={busy} onClick={() => { setBusy(true); void onRun(queryForReviewPeriod(result.query, period)).catch(e => setError(e instanceof Error ? e.message : "创建任务失败")).finally(() => setBusy(false)); }}>调整周期并重新分析</button></div>}{error && <ErrorNotice message={error} />}<button className="button primary" disabled={generating || !!periodIssue} onClick={() => void generate()}>{generating ? "正在创建草稿…" : "进入复盘编辑器"}<ArrowRight size={16} /></button></AnalysisModal>}
    {detail && <AnalysisModal title={detail.row.name} close={() => setDetail(null)}><p>统计周期：{result.query.startDate} — {result.query.endDate}</p><p>明细ID：{detail.row.id} · 记录日期：{detail.row.date}</p><div className="ba-detail-values">{detail.metrics.map(metric => <div key={metric.id}><span>{metric.name}</span><b>{formatAnalysisValue(pointValue(detail.row.values, metric), metric.unit)}</b><small>{metric.definition}</small></div>)}</div><p className="ba-caption">{detail.row.relatedProduct ? `关联商品：${detail.row.relatedProduct}` : "商品关联未返回"} · {detail.row.relatedLive ? `关联场次：${detail.row.relatedLive}` : "场次关联未返回"}</p><Link className="button secondary" href={`/app/${detail.metrics[0]?.dimension === "ads" ? "plans" : detail.metrics[0]?.dimension === "live" ? "monitor" : detail.metrics[0]?.dimension === "video" ? "assets" : "products"}`}>进入业务工作区<ArrowRight size={14} /></Link></AnalysisModal>}
    {detailDate && <AnalysisModal title={`${detailDate} · 趋势业务明细`} close={() => setDetailDate("")}><p>{weekly ? "按周图选择的周起始日；以下展示本报告该周实际覆盖记录。" : "当前店铺、平台与日期下的实际返回数值。"}</p><AnalysisTable rows={result.points.filter(point => weekly ? point.date >= detailDate && point.date <= new Date(Date.parse(detailDate) + 6 * 86400000).toISOString().slice(0, 10) : point.date === detailDate).map(point => ({ id: point.date, name: point.date, date: point.date, values: point.values }))} metrics={result.metrics.filter(m => m.dimension === (metric?.dimension ?? "shop"))} /><a className="button secondary" href="#ba-specialist" onClick={() => setDetailDate("")}>查看对应专项明细</a></AnalysisModal>}
  </div>;
}
