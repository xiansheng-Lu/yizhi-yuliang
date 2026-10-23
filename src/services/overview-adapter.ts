import { businessTotals, connectorNames, type AdPlan, type FrontendSnapshot } from "@/lib/domain";
import { scopedPlans } from "@/lib/reporting";
import { shanghaiDate, validDateRange, type OverviewFilter, type OverviewReport, type PerformanceRow } from "@/lib/overview";
import { ServiceError } from "./contracts";

/** Frontend-only boundary. A future verified report reader can implement this
 * without changing the dashboard. No speculative HTTP endpoint is called. */
export type OverviewLoader = (input: {
  snapshot: FrontendSnapshot;
  filter: OverviewFilter;
  signal: AbortSignal;
}) => Promise<OverviewReport>;

export function overviewPlatforms(snapshot: FrontendSnapshot, shopId: string) {
  const used = new Set(snapshot.adAccounts
    .filter((a) => shopId === "all" || a.shopId === shopId)
    .map((a) => a.connectorId));
  return snapshot.connectors.filter((c) => used.has(c.id) &&
    (snapshot.source === "MOCK" || ["SUPPORTED", "READ_ONLY", "DEGRADED"].includes(c.status)));
}

function totals(plans: AdPlan[]) {
  const values = businessTotals(plans);
  // Different attribution windows must not be summed into a single GMV/ROI.
  return new Set(plans.map((p) => p.attributionWindow)).size > 1
    ? { ...values, gmv: null, roi: null } : values;
}
function performance(id: string, name: string, detail: string, plans: AdPlan[], totalSpend: number | null): PerformanceRow {
  const values = totals(plans);
  return { id, name, detail, ...values,
    share: values.spend !== null && totalSpend !== null && totalSpend > 0
      ? values.spend / totalSpend : null };
}
export function aggregateOverview(snapshot: FrontendSnapshot, filter: OverviewFilter): OverviewReport {
  if (!validDateRange(filter.startDate, filter.endDate))
    throw new ServiceError("INVALID_INPUT", "请选择有效的日期范围，开始日期不能晚于结束日期。");
  const supported = overviewPlatforms(snapshot, filter.scope.shop_id);
  const platformIds = new Set(supported.map((c) => c.id));
  const validAmount = (value: number | null) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
  const allPlans = scopedPlans(snapshot, filter.scope).filter((p) => {
    const account = snapshot.adAccounts.find((a) => a.id === p.adAccountId);
    return account && platformIds.has(account.connectorId);
  }).map((p) => ({ ...p, spendMinor: validAmount(p.spendMinor) ? p.spendMinor : null,
    paidGmvMinor: validAmount(p.paidGmvMinor) ? p.paidGmvMinor : null }));
  // The existing snapshot contract provides ONE reporting day, not history.
  // A timestamp is an update time and must never be substituted for that day.
  const covered = filter.startDate === snapshot.scope.date && filter.endDate === snapshot.scope.date;
  const plans = covered ? allPlans : [];
  const values = totals(plans);
  const channels = supported.map((channel) => {
    const channelPlans = plans.filter((p) => snapshot.adAccounts.some((a) => a.id === p.adAccountId && a.connectorId === channel.id));
    return performance(channel.id, connectorNames[channel.id], `${channelPlans.length} 个关联计划`, channelPlans, values.spend);
  }).filter((c) => plans.some((p) => snapshot.adAccounts.some((a) => a.id === p.adAccountId && a.connectorId === c.id)))
    .sort((a, b) => (b.spend ?? -1) - (a.spend ?? -1));
  const lives = snapshot.liveSessions
    .filter((live) => plans.some((p) => p.liveSessionId === live.id))
    .map((live) => performance(live.id, live.name,
      ({ MOCK_LIVE: "演示场次", UNKNOWN: "状态待同步", NOT_STARTED: "未开播", LIVE: "直播中", ENDED: "已结束" })[live.status],
      plans.filter((p) => p.liveSessionId === live.id), values.spend))
    .sort((a, b) => (b.gmv ?? -1) - (a.gmv ?? -1));
  const jobs = snapshot.tasks.filter((job) => {
    const date = shanghaiDate(job.updated_at);
    const account = snapshot.adAccounts.find((a) => a.id === job.account_id);
    return date >= filter.startDate && date <= filter.endDate &&
      (filter.scope.shop_id === "all" || job.shop_id === filter.scope.shop_id) &&
      (filter.scope.live_session_id === "all" || job.live_session_id === filter.scope.live_session_id) &&
      (filter.scope.connector_id === "all" || account?.connectorId === filter.scope.connector_id);
  });
  const completed = jobs.filter((j) => j.status === "SUCCEEDED" &&
    j.execution_receipt?.verification === "MATCHED" &&
    (snapshot.source === "MOCK" ? j.execution_receipt.source === "MOCK" : j.execution_receipt.source === "PLATFORM")).length;
  const failed = jobs.filter((j) => j.status === "FAILED").length;
  const pending = jobs.filter((j) => j.status === "PENDING_APPROVAL").length;
  const exceptions = jobs.filter((j) => ["FAILED", "BLOCKED", "MANUAL_REQUIRED"].includes(j.status)).length;
  const updated = plans.map((p) => p.sourceUpdatedAt).filter((at) => Number.isFinite(Date.parse(at))).sort((a, b) => Date.parse(a) - Date.parse(b));
  const completeSpend = plans.length > 0 && plans.every((p) => p.spendMinor !== null);
  const distributions = completeSpend ? [true, false].map((enabled) => ({
    id: enabled ? "enabled" : "paused", name: enabled ? "投放开启" : "投放暂停",
    value: plans.filter((p) => p.enabled === enabled).reduce((n, p) => n + p.spendMinor!, 0),
  })).filter((s) => s.value > 0) : [];
  const report: OverviewReport = {
    source: snapshot.source, filter, updatedAt: updated.at(-1) ?? null,
    coverageNote: !covered ? `当前仅提供 ${snapshot.scope.date} 单日快照，所选日期报表待接入。`
      : !plans.length ? "当前筛选范围暂无投放数据。"
      : new Set(plans.map((p) => p.attributionWindow)).size > 1 ? "归因窗口不一致，支付GMV与ROI暂不合计。"
      : "支付归因 GMV / 同范围广告消耗；含暂停计划的历史消耗。",
    totals: { ...values, orders: null, conversion: null, cpm: null }, previous: null,
    trend: { status: "unavailable", reason: "历史趋势数据源待接入，当前快照不能还原小时或每日曲线。" },
    channels, lives,
    distributions: { status: distributions, channel: completeSpend ? channels.map((c) => ({ id: c.id, name: c.name, value: c.spend! })).filter((s) => s.value > 0) : [] },
    funnel: { status: "unavailable", reason: "曝光、点击、进房、下单与支付尚无同范围统计，暂不能计算完整漏斗。" },
    traffic: { status: "unavailable", reason: "流量来源数据源待接入，不根据GMV推算付费与自然流量。" },
    ai: { running: jobs.filter((j) => ["EXECUTING", "VERIFYING"].includes(j.status)).length,
      completed, exceptions, pending, successRate: completed + failed > 0 ? completed / (completed + failed) : null },
    insights: [],
  };
  if (!covered || !plans.length) report.insights.push({ id: "coverage", tone: "info", title: "当前日期经营数据待同步", detail: report.coverageNote, href: "/app/analytics" });
  const target = filter.scope.shop_id !== "all"
    ? snapshot.riskPolicies.find((policy) => policy.tenantId === snapshot.shops.find((s) => s.id === filter.scope.shop_id)?.tenantId)?.targetRoi : null;
  if (values.roi !== null && target && target > 0) report.insights.push({
    id: "roi", tone: values.roi >= target ? "success" : "warning",
    title: `投放ROI ${values.roi.toFixed(2)}，${values.roi >= target ? "达到" : "低于"}目标 ${target.toFixed(2)}`,
    detail: "按当前范围支付归因GMV与广告消耗计算。", href: "/app/goals",
  });
  if (channels[0]?.share !== null && channels[0]?.share !== undefined) report.insights.push({
    id: "channel", tone: "info", title: `${channels[0].name} 消耗占比 ${(channels[0].share * 100).toFixed(1)}%`,
    detail: "当前范围内广告消耗最高的渠道，可查看相关计划。", href: "/app/plans",
  });
  if (pending || exceptions) report.insights.push({ id: "tasks", tone: "warning", title: `${pending} 项待审批 · ${exceptions} 项异常`, detail: "按所选范围内更新的任务统计，进入详情核对。", href: "/app/tasks" });
  report.insights.push({ id: "history", tone: "info", title: "较前一日变化暂不可用", detail: "缺少同口径对比数据，暂不判断增长与下降。", href: "/app/analytics" });
  return report;
}
export const loadSnapshotOverview: OverviewLoader = async ({ snapshot, filter, signal }) => {
  signal.throwIfAborted();
  const report = aggregateOverview(snapshot, filter);
  signal.throwIfAborted();
  return report;
};
