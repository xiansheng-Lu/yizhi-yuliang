import { analysisMetricDefinitions, dimensionNames, formatAnalysisValue, pointValue, type AnalysisDimension, type AnalysisInsight, type AnalysisMetric, type AnalysisPoint, type AnalysisQuery, type AnalysisResult, type AnalysisSection } from "./business-analysis";
import { shiftDate, validDateRange } from "./reporting-date";

export type ReviewPeriod = "daily" | "weekly" | "monthly" | "custom";
export const reviewPeriodNames: Record<ReviewPeriod, string> = { daily: "日报", weekly: "周报", monthly: "月报", custom: "自定义周期" };
export const reviewTemplates = ["daily", "weekly", "monthly"] as const;
export function reportPeriodIssue(query: AnalysisQuery, period: ReviewPeriod) {
  if (!validDateRange(query.startDate, query.endDate)) return "统计日期无效，请重新选择。";
  const days = Math.round((Date.parse(query.endDate) - Date.parse(query.startDate)) / 86400000) + 1;
  if (period === "daily" && days !== 1) return "日报必须使用1天数据，请重新选择统计周期并执行分析。";
  if (period === "weekly" && days !== 7) return "周报必须使用连续7天数据，请重新选择统计周期并执行分析。";
  if (period === "monthly" && (query.startDate.slice(0, 7) !== query.endDate.slice(0, 7) || !query.startDate.endsWith("-01") || shiftDate(query.endDate, 1).slice(0, 7) === query.endDate.slice(0, 7))) return "月报必须覆盖同一自然月的完整日期，不能用7天数据生成月报。";
  return "";
}
export function queryForReviewPeriod(query: AnalysisQuery, period: ReviewPeriod): AnalysisQuery {
  if (period === "daily") return { ...query, startDate: query.endDate };
  if (period === "weekly") return { ...query, startDate: shiftDate(query.endDate, -6) };
  if (period === "monthly") {
    if (!reportPeriodIssue(query, period)) return query;
    const endDate = shiftDate(`${query.endDate.slice(0, 7)}-01`, -1);
    return { ...query, startDate: `${endDate.slice(0, 7)}-01`, endDate };
  }
  return query;
}
export interface ReportModule {
  id: string;
  title: string;
  text: string;
  visible: boolean;
  showChart: boolean;
  showMetrics?: boolean;
  metricIds: string[];
  sectionId?: string;
}
export interface ReportDraft {
  schemaVersion: "business-report-v1";
  id: string;
  analysisId: string;
  title: string;
  period: ReviewPeriod;
  query: AnalysisQuery;
  shopName: string;
  platformName: string;
  source: "MOCK" | "API";
  updatedAt: string;
  version: number;
  metrics: AnalysisMetric[];
  points: AnalysisPoint[];
  sections: AnalysisSection[];
  insights: AnalysisInsight[];
  coverage: string;
  attribution: string;
  modules: ReportModule[];
}
export interface PublishedReport { id: string; draftId: string; url: string | null; source: "MOCK" | "API"; publishedAt: string; snapshot?: ReportDraft; title?: string; period?: ReviewPeriod; query?: AnalysisQuery; analysisId?: string }
export function createReportDraft(result: AnalysisResult, period: ReviewPeriod, id: string): ReportDraft {
  const issue = reportPeriodIssue(result.query, period);
  if (issue) throw new Error(issue);
  return structuredClone({
    schemaVersion: "business-report-v1", id, analysisId: result.id,
    title: `${result.shopName} · 经营复盘${reviewPeriodNames[period]}`,
    period, query: result.query, shopName: result.shopName, platformName: result.platformName,
    source: result.source, updatedAt: new Date().toISOString(), version: 0,
    metrics: result.metrics, points: result.points, sections: result.sections, insights: result.insights,
    coverage: result.coverage, attribution: result.attribution,
    modules: [
      { id: "summary", title: "经营摘要", text: result.insights.filter(i => i.kind === "conclusion").map(i => `${i.title}。${i.evidence}`).join("\n") || "当前数据不足以形成经营结论，请补充数据。", visible: true, showChart: true, metricIds: result.metrics.slice(0, 3).map(m => m.id) },
      ...result.sections.map(section => ({ id: section.id, title: section.title, text: `${section.conclusion}\n${section.advice}`, visible: true, showChart: true, metricIds: section.metricIds, sectionId: section.id })),
      { id: "diagnosis", title: "异常与下一步行动", text: result.insights.filter(i => i.kind === "anomaly" || i.kind === "recommendation").map(i => `[${i.priority}] ${i.title}\n依据：${i.evidence}\n操作：${i.action}\n验证指标：${i.verificationMetric}`).join("\n\n") || "样本或比较基准不足，暂无有据可依的异常与建议。", visible: true, showChart: false, metricIds: [] },
      { id: "manual-advice", title: "人工经营建议", text: "", visible: true, showChart: false, metricIds: [] },
      { id: "action-plan", title: "下阶段行动计划", text: "", visible: true, showChart: false, metricIds: [] },
    ],
  } satisfies ReportDraft);
}
export function moveReportModule(modules: ReportModule[], id: string, offset: number) {
  const next = [...modules], index = next.findIndex(module => module.id === id), target = index + offset;
  if (index < 0 || target < 0 || target >= next.length) return next;
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}
export function escapeReportHtml(value: string) { return value.replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]!); }
export function reportChartSvg(points: AnalysisPoint[], metric: AnalysisMetric) {
  const values = points.map(point => pointValue(point.values, metric));
  if (!values.some(value => value !== null)) return "<p>暂无可用趋势数据</p>";
  const max = Math.max(1, ...values.filter((v): v is number => v !== null));
  const path = values.map((value, index) => value === null ? "" : `${index === 0 || values[index - 1] === null ? "M" : "L"}${(40 + index / Math.max(1, values.length - 1) * 720).toFixed(1)},${(180 - value / max * 140).toFixed(1)}`).join(" ");
  const dots = points.map((point, index) => values[index] === null ? "" : `<circle cx="${40 + index / Math.max(1, values.length - 1) * 720}" cy="${180 - values[index]! / max * 140}" r="3" fill="#335fe1"><title>${escapeReportHtml(point.date)}：${escapeReportHtml(formatAnalysisValue(values[index], metric.unit))}</title></circle>`).join("");
  return `<svg viewBox="0 0 800 215" role="img" aria-label="${escapeReportHtml(metric.name)}趋势"><line x1="40" y1="180" x2="760" y2="180" stroke="#dce3ef"/><path d="${path}" fill="none" stroke="#335fe1" stroke-width="2.5"/>${dots}<text x="40" y="207" fill="#63738c" font-size="12">${escapeReportHtml(points[0]?.date ?? "")}</text><text x="760" y="207" text-anchor="end" fill="#63738c" font-size="12">${escapeReportHtml(points.at(-1)?.date ?? "")}</text></svg>`;
}
/** Standalone, script-free client deliverable. Human edits are escaped as text. */
export function exportReportHtml(draft: ReportDraft) {
  const esc = escapeReportHtml;
  const modules = draft.modules.filter(module => module.visible).map(module => {
    const metrics = module.metricIds.map(id => draft.metrics.find(metric => metric.id === id)).filter((m): m is AnalysisMetric => !!m);
    const section = draft.sections.find(section => section.id === module.sectionId);
    return `<section><h2>${esc(module.title)}</h2><div class="metrics">${(module.showMetrics === false ? [] : metrics).map(metric => `<div><span>${esc(metric.name)}</span><strong>${esc(formatAnalysisValue(metric.value, metric.unit))}</strong><small>${esc(metric.definition)}</small></div>`).join("")}</div><p class="body">${esc(module.text)}</p>${module.showChart && metrics[0] ? reportChartSvg(draft.points, metrics[0]) : ""}${section?.rows.length ? `<details><summary>查看数据明细（${section.rows.length}条）</summary><div class="scroll"><table><thead><tr><th>业务对象</th>${metrics.map(m => `<th>${esc(m.name)}</th>`).join("")}</tr></thead><tbody>${section.rows.map(row => `<tr><td>${esc(row.name)}</td>${metrics.map(m => `<td>${esc(formatAnalysisValue(pointValue(row.values, m), m.unit))}</td>`).join("")}</tr>`).join("")}</tbody></table></div></details>` : ""}</section>`;
  }).join("");
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"><title>${esc(draft.title)}</title><style>body{margin:0;background:#f5f8fd;color:#17213b;font:15px/1.8 system-ui,'Microsoft YaHei',sans-serif}main{max-width:1080px;margin:auto;padding:42px 24px}header{padding:20px 0 32px;border-bottom:3px solid #335fe1}h1{font-size:30px}h2{font-size:21px}small,header p{color:#63738c}.tag{color:#335fe1}.demo{background:#fff4d8;padding:12px 18px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:20px}.metrics strong,.metrics small{display:block}.metrics strong{font-size:25px}.metrics small{font-size:11px}section{padding:28px;background:white;border:1px solid #e6edf8;border-radius:14px;margin-top:24px}.body{white-space:pre-wrap;overflow-wrap:anywhere}svg{width:100%;max-height:300px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{text-align:left;padding:10px;border-bottom:1px solid #e6edf8;white-space:nowrap}.scroll{overflow:auto}footer{color:#63738c;margin-top:28px;font-size:12px}summary{cursor:pointer;color:#335fe1}@media(max-width:600px){main{padding:20px 12px}section{padding:18px}h1{font-size:24px}}@media print{body{background:white}section{break-inside:avoid}details{display:block}}</style></head><body><main>${draft.source === "MOCK" ? '<p class="demo">演示数据 · 固定测试样本，不代表真实经营结果</p>' : ""}<header><span class="tag">易致·驭量 / 客户经营复盘</span><h1>${esc(draft.title)}</h1><p>${esc(draft.shopName)} · ${esc(draft.platformName)}<br>${esc(draft.query.startDate)} — ${esc(draft.query.endDate)} · Asia/Shanghai<br>${draft.query.dimensions.map((dimension: AnalysisDimension) => dimensionNames[dimension]).join(" / ")}</p><small>${esc(draft.coverage)}<br>${esc(draft.attribution)}</small></header>${modules}<footer>数据引用：${esc(draft.analysisId)} · ${esc(draft.updatedAt)}<br>复盘含人工编辑，原始经营分析结果独立保存。跨业务GMV可能重叠，不相加；趋势相关性不证明因果。</footer></main></body></html>`;
}

export function validateDraft(draft: ReportDraft) {
  if (draft?.query && draft.period !== "custom") { const issue = reportPeriodIssue(draft.query, draft.period); if (issue) throw new Error(issue); }
  if (!draft || draft.schemaVersion !== "business-report-v1" || !draft.id || !draft.analysisId || !draft.title?.trim() || draft.title.length > 120 || !Array.isArray(draft.modules) || !draft.modules.some(m => m.visible) || draft.modules.length > 40 || draft.modules.some(m => m.title.length > 120 || m.text.length > 20000)) throw new Error("请填写报告名称并保留至少一个可见模块，内容长度需符合限制。");
  if (draft.metrics.some(metric => !analysisMetricDefinitions.some(def => def.id === metric.id))) throw new Error("复盘指标口径不正确。");
}
