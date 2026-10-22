import { businessTotals, type AdPlan } from "./domain";
import { finiteValue, type LiveModule } from "./live-room";

export const liveMetricIds = ["online", "viewers", "gmv", "orders", "spend", "roi"] as const;
export type LiveMetricId = typeof liveMetricIds[number];
export const liveMetricDefinitions: Record<LiveMetricId, { name: string; module: LiveModule; definition: string; breakdown: "audience" | "plans" | "orders" }> = {
  online: { name: "在线人数", module: "live", definition: "当前直播间在线人数，使用平台返回的场次快照。", breakdown: "audience" },
  viewers: { name: "累计观看", module: "live", definition: "平台返回的本场累计观看值；人数去重规则与跨日统计口径待平台明确。", breakdown: "audience" },
  gmv: { name: "支付GMV", module: "commerce", definition: "所选店铺、场次、平台和统计日的关联计划支付归因GMV，含暂停计划的历史数据。", breakdown: "plans" },
  orders: { name: "成交订单", module: "commerce", definition: "当前范围内平台返回的成交订单数；没有订单字段时显示 --。", breakdown: "orders" },
  spend: { name: "广告消耗", module: "commerce", definition: "所选范围关联计划的广告消耗，包含已暂停计划的历史消耗。", breakdown: "plans" },
  roi: { name: "投放ROI", module: "commerce", definition: "沿用现有口径：同范围支付归因GMV ÷ 广告消耗。消耗为0或归因窗口不一致时不计算。", breakdown: "plans" },
};
export function parseLiveMetric(value: string | null): LiveMetricId | null {
  return liveMetricIds.find((id) => id === value) ?? null;
}
/** Keep drill-down within the existing monitor permission and route. */
export function liveMetricHref(metric: LiveMetricId | null, search = "") {
  const query = new URLSearchParams(search);
  query.delete("metric");
  if (metric) query.set("metric", metric);
  return `/app/monitor${query.size ? `?${query.toString()}` : ""}`;
}
export function liveMetricMoney(value: number | null | undefined) {
  return finiteValue(value) === null ? "--" : new Intl.NumberFormat("zh-CN", {
    style: "currency", currency: "CNY", maximumFractionDigits: 2,
  }).format(value! / 100);
}
export function livePlanAmounts(plan: AdPlan, notStarted = false) {
  if (notStarted || finiteValue(plan.spendMinor) === null || finiteValue(plan.paidGmvMinor) === null)
    return { spend: null, gmv: null, roi: null };
  return businessTotals([plan]);
}
