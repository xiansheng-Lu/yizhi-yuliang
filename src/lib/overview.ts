import type { ReportingScope } from "./command-job";

export interface OverviewFilter {
  scope: ReportingScope;
  startDate: string;
  endDate: string;
}
export type OverviewMetric = "spend" | "gmv" | "roi";
export type OverviewModule<T> =
  | { status: "ready"; data: T }
  | { status: "empty" | "unavailable" | "error"; reason: string };
export interface TrendPoint {
  at: string;
  spend: number | null; // CNY integer minor units; per bucket, never cumulative.
  gmv: number | null;
  roi: number | null;
}
export interface OverviewTotals {
  spend: number | null;
  gmv: number | null;
  roi: number | null;
  orders: number | null;
  conversion: number | null; // ratio, 0..1
  cpm: number | null; // CNY minor units
}
export interface PerformanceRow {
  id: string;
  name: string;
  detail: string;
  spend: number | null;
  gmv: number | null;
  roi: number | null;
  share: number | null;
}
export interface DistributionSlice {
  id: string;
  name: string;
  value: number;
}
export interface FunnelStage {
  id: string;
  name: string;
  count: number;
}
export interface OverviewInsight {
  id: string;
  tone: "info" | "success" | "warning";
  title: string;
  detail: string;
  href: string;
}
export interface OverviewReport {
  source: "API" | "MOCK";
  filter: OverviewFilter;
  updatedAt: string | null;
  coverageNote: string;
  totals: OverviewTotals;
  previous: OverviewTotals | null;
  trend: OverviewModule<TrendPoint[]>;
  channels: PerformanceRow[];
  lives: PerformanceRow[];
  distributions: Record<"status" | "channel", DistributionSlice[]>;
  funnel: OverviewModule<FunnelStage[]>;
  traffic: OverviewModule<DistributionSlice[]>;
  ai: {
    running: number;
    completed: number;
    exceptions: number;
    pending: number;
    successRate: number | null;
  };
  insights: OverviewInsight[];
}
export const overviewMetricNames: Record<OverviewMetric, string> = {
  spend: "广告消耗", gmv: "支付GMV", roi: "投放ROI",
};
export function overviewKey(filter: OverviewFilter) {
  return JSON.stringify([
    filter.scope.shop_id, filter.scope.live_session_id,
    filter.scope.connector_id, filter.scope.date,
    filter.startDate, filter.endDate,
  ]);
}
export function shanghaiDate(at: string | Date = new Date()) {
  const date = new Date(at);
  if (!Number.isFinite(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(date);
  const part = (type: string) => parts.find((p) => p.type === type)?.value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}
export function shiftDate(date: string, days: number) {
  const at = new Date(`${date}T00:00:00+08:00`);
  at.setUTCDate(at.getUTCDate() + days);
  return shanghaiDate(at);
}
export function validDateRange(start: string, end: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(start) &&
    /^\d{4}-\d{2}-\d{2}$/.test(end) && start <= end &&
    shanghaiDate(`${start}T00:00:00+08:00`) === start &&
    shanghaiDate(`${end}T00:00:00+08:00`) === end;
}
export function percentChange(current: number | null, previous: number | null) {
  return current === null || previous === null || previous <= 0
    ? null : (current - previous) / previous;
}
export function funnelRate(stages: FunnelStage[], index: number) {
  const previous = stages[index - 1]?.count;
  return previous > 0 && stages[index]?.count <= previous
    ? stages[index].count / previous : null;
}
