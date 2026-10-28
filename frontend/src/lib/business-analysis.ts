import type { ConnectorId, FrontendSnapshot } from "./domain";
import { money } from "./domain";
import { percentChange, shiftDate, validDateRange } from "./reporting-date";

export const dimensions = ["shop", "live", "ads", "video"] as const;
export type AnalysisDimension = typeof dimensions[number];
export const dimensionNames: Record<AnalysisDimension, string> = {
  shop: "店铺经营分析", live: "直播经营分析", ads: "投流分析", video: "短视频带货分析",
};
export type MetricStatus = "ready" | "empty" | "not_integrated" | "unsupported";
export const metricStatusNames: Record<MetricStatus, string> = {
  ready: "已接入", empty: "暂无数据", not_integrated: "未接入", unsupported: "当前平台不支持",
};
export interface AnalysisQuery {
  shopId: string;
  platformId: ConnectorId | "all";
  startDate: string;
  endDate: string;
  dimensions: AnalysisDimension[];
  timezone: "Asia/Shanghai";
}
export interface AnalysisTask {
  id: string;
  query: AnalysisQuery;
  status: "queued" | "running" | "succeeded" | "failed";
  progress: number;
  resultId?: string;
  error?: string;
  createdAt: string;
}
export interface MetricDefinition {
  id: string; dimension: AnalysisDimension; name: string;
  unit: "minor" | "count" | "rate" | "ratio" | "hours";
  definition: string;
  numerator?: string; denominator?: string;
}
export interface AnalysisMetric extends MetricDefinition {
  value: number | null;
  previous: number | null;
  yearAgo: number | null;
  status: MetricStatus;
  reason?: string;
}
export interface AnalysisPoint { date: string; values: Record<string, number | null> }
export interface AnalysisRow {
  id: string; name: string; date: string; values: Record<string, number | null>;
  relatedProduct?: string; relatedLive?: string;
}
export interface AnalysisSection {
  id: string;
  dimension: AnalysisDimension | "cross";
  title: string;
  topics: string[];
  metricIds: string[];
  rows: AnalysisRow[];
  conclusion: string;
  advice: string;
  limitation?: string;
}
export interface AnalysisInsight {
  id: string;
  kind: "conclusion" | "anomaly" | "opportunity" | "recommendation";
  title: string;
  evidence: string;
  direction: string;
  action: string;
  priority: "P1" | "P2" | "P3";
  verificationMetric: string;
  metricId: string;
  date?: string;
}
export interface AnalysisRules {
  declinePercent: number;
  spendRisePercent: number;
  roiFloor: number;
  minSpendMinor: number;
  minViews: number;
}
export const defaultAnalysisRules: AnalysisRules = {
  declinePercent: 20, spendRisePercent: 30, roiFloor: 3, minSpendMinor: 100000, minViews: 1000,
};
export interface AnalysisResult {
  schemaVersion: "business-analysis-v1";
  id: string;
  taskId: string;
  source: "MOCK" | "API";
  query: AnalysisQuery;
  shopName: string;
  platformName: string;
  createdAt: string;
  updatedAt: string | null;
  completeness: "complete" | "partial" | "empty";
  coverage: string;
  attribution: string;
  comparison: { previous: { startDate: string; endDate: string } | null; yearAgo: { startDate: string; endDate: string } | null };
  metrics: AnalysisMetric[];
  points: AnalysisPoint[];
  previousPoints: AnalysisPoint[];
  yearAgoPoints: AnalysisPoint[];
  sections: AnalysisSection[];
  insights: AnalysisInsight[];
  insightSource: "rules" | "ai";
  rules: AnalysisRules;
}

const def = (dimension: AnalysisDimension, key: string, name: string, unit: MetricDefinition["unit"], definition: string, numerator?: string, denominator?: string): MetricDefinition =>
  ({ id: `${dimension}.${key}`, dimension, name, unit, definition, numerator: numerator && `${dimension}.${numerator}`, denominator: denominator && `${dimension}.${denominator}` });
export const analysisMetricDefinitions: MetricDefinition[] = [
  def("shop", "gmv", "支付GMV", "minor", "店铺订单支付金额，订单去重；包含后续退款，退款另列"),
  def("shop", "orders", "有效订单量", "count", "支付成功且未关闭的订单数，按订单ID去重"),
  def("shop", "aov", "成交客单价", "minor", "支付GMV ÷ 有效订单量", "gmv", "orders"),
  def("shop", "refund", "退款金额", "minor", "所选周期实际成功退款金额，按退款单去重"),
  def("shop", "refundRate", "退款率", "rate", "周期退款金额 ÷ 周期支付GMV（金额口径）", "refund", "gmv"),
  def("live", "sessions", "开播场次", "count", "按开播日归属并按场次ID去重"),
  def("live", "hours", "直播时长", "hours", "周期内实际开播时长，单位小时"),
  def("live", "viewers", "直播观看人数", "count", "各日观看UV之和；跨日未去重人次"),
  def("live", "gmv", "直播成交GMV", "minor", "直播渠道归因支付金额，与店铺GMV可能重叠"),
  def("live", "conversion", "直播成交转化率", "rate", "直播支付人数 ÷ 同范围观看人次", "buyers", "viewers"),
  def("ads", "spend", "广告消耗", "minor", "同平台、同账户、同周期实际广告消耗，含暂停计划"),
  def("ads", "gmv", "广告归因GMV", "minor", "平台支付归因金额；归因窗口见数据来源说明"),
  def("ads", "roi", "投放ROI", "ratio", "平台支付归因GMV ÷ 同范围消耗；以平台口径为准", "gmv", "spend"),
  def("ads", "ctr", "点击率", "rate", "广告点击量 ÷ 广告曝光量", "clicks", "impressions"),
  def("ads", "cpc", "点击成本", "minor", "广告消耗 ÷ 广告点击量", "spend", "clicks"),
  def("ads", "cpa", "成交成本", "minor", "广告消耗 ÷ 平台归因支付订单数", "spend", "orders"),
  def("video", "published", "视频发布数量", "count", "周期内发布且关联商品的视频数，按内容ID去重"),
  def("video", "views", "视频播放量", "count", "周期内视频有效播放次数，非去重观看人数"),
  def("video", "clicks", "商品点击量", "count", "视频商品卡点击次数"),
  def("video", "gmv", "视频成交GMV", "minor", "视频渠道归因支付金额，与店铺、广告归因可能重叠"),
  def("video", "conversion", "点击转化率", "rate", "视频归因支付订单数 ÷ 商品点击次数", "orders", "clicks"),
];
export function analysisKey(query: AnalysisQuery) {
  return JSON.stringify([query.shopId, query.platformId, query.startDate, query.endDate, [...query.dimensions].sort(), query.timezone]);
}
export function analysisShops(snapshot: FrontendSnapshot, platformId: AnalysisQuery["platformId"]) {
  return snapshot.shops.filter(shop => platformId === "all" || shop.connectorId === platformId || snapshot.adAccounts.some(account => account.shopId === shop.id && account.connectorId === platformId));
}
export function validateAnalysisQuery(query: AnalysisQuery, snapshot: FrontendSnapshot) {
  if (!query.dimensions.length || query.dimensions.length > 4 || new Set(query.dimensions).size !== query.dimensions.length || query.dimensions.some(d => !dimensions.includes(d))) throw new Error("请至少选择一个有效分析维度。");
  if (!validDateRange(query.startDate, query.endDate) || rangeDays(query) > 366) throw new Error("请选择有效日期范围，最长支持366天。");
  if (query.timezone !== "Asia/Shanghai" || !analysisShops(snapshot, query.platformId).some(shop => shop.id === query.shopId)) throw new Error("店铺不属于当前平台或未获授权，请重新选择。");
}
export function rangeDays(query: Pick<AnalysisQuery, "startDate" | "endDate">) { return Math.round((Date.parse(query.endDate) - Date.parse(query.startDate)) / 86400000) + 1; }
export function previousRange(query: AnalysisQuery) { return { startDate: shiftDate(query.startDate, -rangeDays(query)), endDate: shiftDate(query.startDate, -1) }; }
export function selectDimension(current: AnalysisDimension[], next: AnalysisDimension, multiple: boolean) {
  return current.includes(next) ? current.filter(d => d !== next) : multiple ? [...current, next] : [next];
}
export function formatAnalysisValue(value: number | null | undefined, unit: MetricDefinition["unit"] = "count") {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return unit === "minor" ? money(value) : unit === "rate" ? `${(value * 100).toFixed(2)}%` : unit === "ratio" ? value.toFixed(2) : unit === "hours" ? `${value.toFixed(1)}小时` : value.toLocaleString("zh-CN");
}
export function pointValue(values: AnalysisPoint["values"], metric: MetricDefinition) {
  if (metric.numerator && metric.denominator) {
    const a = values[metric.numerator], b = values[metric.denominator];
    return typeof a === "number" && typeof b === "number" && b > 0 ? a / b : null;
  }
  return values[metric.id] ?? null;
}
export function sumPoints(points: AnalysisPoint[]) {
  const keys = new Set(points.flatMap(point => Object.keys(point.values)));
  return Object.fromEntries([...keys].map(key => [key, points.every(point => typeof point.values[key] === "number") ? points.reduce((total, point) => total + point.values[key]!, 0) : null]));
}
/** Ratio metrics are recomputed from numerators/denominators, never averaged. */
export function groupAnalysisPoints(points: AnalysisPoint[], weekly: boolean): AnalysisPoint[] {
  if (!weekly) return points;
  const groups = new Map<string, AnalysisPoint[]>();
  points.forEach(point => {
    const weekday = new Date(`${point.date}T00:00:00+08:00`).getUTCDay();
    // 00:00 Shanghai is the previous UTC day; convert to local weekday first.
    const monday = shiftDate(point.date, -((weekday + 1 + 6) % 7));
    groups.set(monday, [...(groups.get(monday) ?? []), point]);
  });
  return [...groups].map(([date, rows]) => ({ date, values: sumPoints(rows) }));
}
function insight(kind: AnalysisInsight["kind"], id: string, title: string, evidence: string, metricId: string, action = "核对业务明细并在下一同口径周期验证", priority: AnalysisInsight["priority"] = "P2", date?: string): AnalysisInsight {
  return { kind, id, title, evidence, metricId, direction: "先核对口径和样本，再调整运营动作", action, priority, verificationMetric: analysisMetricDefinitions.find(m => m.id === metricId)?.name ?? metricId, date };
}
export function diagnoseAnalysis(result: Pick<AnalysisResult, "metrics" | "sections" | "points" | "comparison">, rules: AnalysisRules): AnalysisInsight[] {
  const insights: AnalysisInsight[] = [];
  for (const metric of result.metrics) {
    if (metric.status !== "ready" || metric.value === null) continue;
    const change = result.comparison.previous ? percentChange(metric.value, metric.previous) : null;
    if (["shop.gmv", "live.gmv", "ads.roi", "video.gmv"].includes(metric.id)) insights.push(insight("conclusion", `summary-${metric.id}`, `${metric.name} ${formatAnalysisValue(metric.value, metric.unit)}`, change === null ? "没有完整同口径对比周期，仅描述本期数值。" : `较上一等长周期${change >= 0 ? "上升" : "下降"}${Math.abs(change * 100).toFixed(1)}%。`, metric.id));
    const down = ["ads.roi", "shop.gmv", "live.conversion"].includes(metric.id) && change !== null && change <= -rules.declinePercent / 100;
    const up = metric.id === "ads.spend" && change !== null && change >= rules.spendRisePercent / 100;
    if (down || up) {
      const evidence = `本期${formatAnalysisValue(metric.value, metric.unit)}，上期${formatAnalysisValue(metric.previous, metric.unit)}；变化${(change! * 100).toFixed(1)}%，阈值${up ? "+" + rules.spendRisePercent : "-" + rules.declinePercent}%。`;
      const action = metric.id.startsWith("ads") ? "检查高消耗计划的归因窗口、受众与素材；小额调整后对照同周期ROI" : "定位下降商品或场次，核对库存、商品卡和成交链路后进行单项验证";
      insights.push(insight("anomaly", `anomaly-${metric.id}`, `${metric.name}${up ? "异常上升" : "明显下降"}`, evidence, metric.id, action, "P1"));
      insights.push(insight("recommendation", `action-${metric.id}`, `优先核对${metric.name}`, evidence, metric.id, action, "P1"));
    }
  }
  const metric = (id: string) => result.metrics.find(m => m.id === id);
  const views = metric("video.views"), videoGmv = metric("video.gmv");
  if (result.comparison.previous && views?.value !== null && views?.value !== undefined && views.value >= rules.minViews && videoGmv?.status === "ready") {
    const trafficChange = percentChange(views.value, views.previous), saleChange = percentChange(videoGmv.value, videoGmv.previous);
    if (trafficChange !== null && saleChange !== null && trafficChange >= rules.spendRisePercent / 100 && saleChange <= -rules.declinePercent / 100) {
      const evidence = `播放量变化${(trafficChange * 100).toFixed(1)}%，视频GMV变化${(saleChange * 100).toFixed(1)}%；基准为上一等长周期。`;
      insights.push(insight("anomaly", "video-disconnect", "短视频流量与成交脱节", evidence, "video.gmv", "分内容核对商品点击与支付转化，检查挂载商品和落地承接", "P1"));
      insights.push(insight("recommendation", "video-action", "核对高播放内容的成交承接", evidence, "video.gmv", "选择高播放低转化视频，验证商品卡和内容卖点，观察下一周期点击转化率", "P1"));
    }
  }
  result.sections.filter(section => section.dimension !== "cross").forEach(section => {
    const dimension = section.dimension as AnalysisDimension;
    const id = dimension === "ads" ? "ads.roi" : `${dimension}.gmv`;
    if (metric(id)?.status !== "ready") return;
    const definition = analysisMetricDefinitions.find(m => m.id === id)!;
    const candidates = section.rows.map(row => ({ row, value: pointValue(row.values, definition) })).filter(item => item.value !== null && item.value > 0 && (dimension !== "ads" || ((item.row.values["ads.spend"] ?? 0) >= rules.minSpendMinor && item.value >= rules.roiFloor))).sort((a, b) => b.value! - a.value!);
    const top = candidates[0];
    if (top) {
      const evidence = `${top.row.name}：${definition.name} ${formatAnalysisValue(top.value, definition.unit)}；本期同口径明细内排名第一。${dimension === "ads" ? `消耗不少于${money(rules.minSpendMinor)}，ROI不低于${rules.roiFloor}。` : "仅代表本期成交贡献，潜力需要后续验证。"}`;
      insights.push(insight("opportunity", `top-${dimension}`, `${top.row.name}值得优先验证`, evidence, id, "复核库存与样本，进行有限增量测试，观察下一周期效率及成交", "P2"));
      insights.push(insight("recommendation", `verify-${dimension}`, `验证${top.row.name}的增长空间`, evidence, id, dimension === "ads" ? "保留对照组，小幅增加测试预算；同归因窗口观察ROI和成交成本" : "保持其余条件稳定，测试一次内容或资源调整；对照成交贡献与转化", "P2"));
    }
    if (dimension === "ads") section.rows.forEach(row => {
      const spend = row.values["ads.spend"], roi = pointValue(row.values, definition);
      if (typeof spend === "number" && spend >= rules.minSpendMinor && roi !== null && roi < rules.roiFloor) {
        const evidence = `消耗${money(spend)}，ROI ${roi.toFixed(2)}；基准：消耗≥${money(rules.minSpendMinor)}且ROI<${rules.roiFloor}。`;
        insights.push(insight("anomaly", `low-${row.id}`, `${row.name}高消耗低产出`, evidence, id, "核对归因延迟后，提交降预算或素材替换方案进行审批", "P1", row.date));
        insights.push(insight("recommendation", `low-action-${row.id}`, `检查${row.name}预算效率`, evidence, id, "核对归因延迟与转化链路，小额调整预算后观察同窗口ROI", "P1"));
      }
    });
  });
  // Annotate actual daily anomalies against the immediately preceding valid day.
  const spendDef = analysisMetricDefinitions.find(m => m.id === "ads.spend")!;
  result.points.forEach((point, index) => {
    const prior = result.points[index - 1];
    if (!prior || shiftDate(prior.date, 1) !== point.date) return;
    const current = pointValue(point.values, spendDef), before = pointValue(prior.values, spendDef);
    const change = percentChange(current, before);
    if (change !== null && change >= rules.spendRisePercent / 100) insights.push(insight("anomaly", `day-${point.date}`, `${point.date}广告消耗上升`, `较前一日增加${(change * 100).toFixed(1)}%，触发日阈值${rules.spendRisePercent}%。`, "ads.spend", "查看当日计划消耗与成交明细", "P2", point.date));
  });
  return insights;
}
