import { businessTotals, type AdPlan, type FrontendSnapshot, type LiveSession } from "./domain";
import type { CommandJob, ReportingScope } from "./command-job";
import { scopedJobs, scopedPlans } from "./reporting";

export type LiveStatus = "unselected" | "not_started" | "live" | "ended" | "unknown";
export type LiveModule = "live" | "commerce" | "trends" | "tasks" | "plans";
export const liveCadence: Record<LiveModule, number> = {
  live: 15_000, commerce: 30_000, trends: 60_000, tasks: 10_000, plans: 30_000,
};
export const moduleNames: Record<LiveModule, string> = {
  live: "直播状态与在线", commerce: "经营指标", trends: "直播趋势", tasks: "AI任务", plans: "关联投放计划",
};
export interface LiveFreshnessPolicy {
  /** Maximum age of the platform timestamp, independently configurable per feed. */
  maxAgeMs: Record<LiveModule, number>;
}
const positive = (value: unknown, fallback: number) => Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : fallback;
export function liveRefreshConfiguration() {
  return {
    // Zero means the API owner has not confirmed a safe polling interval.
    snapshotMinimumMs: positive(process.env.NEXT_PUBLIC_SNAPSHOT_MIN_INTERVAL_MS, 0),
    maxAgeMs: {
      live: positive(process.env.NEXT_PUBLIC_LIVE_MAX_AGE_MS, 45_000),
      commerce: positive(process.env.NEXT_PUBLIC_COMMERCE_MAX_AGE_MS, 120_000),
      trends: positive(process.env.NEXT_PUBLIC_TRENDS_MAX_AGE_MS, 180_000),
      tasks: positive(process.env.NEXT_PUBLIC_TASKS_MAX_AGE_MS, 30_000),
      plans: positive(process.env.NEXT_PUBLIC_PLANS_MAX_AGE_MS, 120_000),
    },
  };
}
export interface LiveData {
  room: LiveSession | null;
  status: LiveStatus;
  online: number | null;
  viewers: number | null;
  platformCovered: boolean;
}
export interface CommerceData {
  spend: number | null;
  gmv: number | null;
  roi: number | null;
  orders: number | null;
  note: string;
}
export interface ModuleData {
  live: LiveData;
  commerce: CommerceData;
  trends: null;
  tasks: CommandJob[];
  plans: AdPlan[];
}
export type ModuleState<T> = {
  data: T | null;
  status: "idle" | "loading" | "ready" | "empty" | "error" | "unavailable";
  refreshing: boolean;
  error: string | null;
  updatedAt: string | null;
  lastSuccessAt: number | null;
  failures: number;
  nextDue: number;
};
export type LiveModules = { [K in LiveModule]: ModuleState<ModuleData[K]> };
export function scopeKey(scope: ReportingScope) {
  return JSON.stringify([scope.shop_id, scope.live_session_id, scope.connector_id, scope.date, scope.timezone]);
}
export function finiteValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}
export function validTime(value: string | null | undefined): string | null {
  return value && Number.isFinite(Date.parse(value)) ? value : null;
}
export function oldestTime(values: (string | null | undefined)[]) {
  // A missing timestamp makes freshness unknown; never hide it behind a newer source.
  if (!values.length || values.some((value) => !validTime(value))) return null;
  return values.map((at) => at!).sort((a, b) => Date.parse(a) - Date.parse(b))[0];
}
export function selectedRoom(snapshot: FrontendSnapshot, scope = snapshot.scope) {
  return snapshot.liveSessions.find((room) => room.id === scope.live_session_id &&
    (scope.shop_id === "all" || scope.shop_id === room.shopId)) ?? null;
}
export function liveStatus(room: LiveSession | null, source: FrontendSnapshot["source"]): LiveStatus {
  if (!room) return "unselected";
  if (room.status === "NOT_STARTED") return "not_started";
  if (room.status === "ENDED") return "ended";
  if (room.status === "LIVE" || (room.status === "MOCK_LIVE" && source === "MOCK")) return "live";
  return "unknown";
}
export const statusNames: Record<LiveStatus, string> = {
  unselected: "未选择直播间", not_started: "未开播", live: "直播中", ended: "已结束", unknown: "直播状态待确认",
};
export function selectLiveModules(snapshot: FrontendSnapshot, scope: ReportingScope) {
  const room = selectedRoom(snapshot, scope);
  const status = liveStatus(room, snapshot.source);
  const scoped = { ...snapshot, scope: { ...scope, shop_id: room?.shopId ?? scope.shop_id } };
  const plans = room ? scopedPlans(scoped).filter((plan) => plan.tenantId === room.tenantId &&
    snapshot.adAccounts.some((account) => account.id === plan.adAccountId && account.tenantId === room.tenantId && account.shopId === room.shopId)) : [];
  const platformCovered = scope.connector_id === "all" || room?.connectorId === scope.connector_id;
  const available = !!room && status !== "not_started" && snapshot.scope.date === scope.date;
  const commonWindow = new Set(plans.map((plan) => plan.attributionWindow)).size <= 1;
  const totals = businessTotals(plans);
  const validAmounts = plans.every((plan) => finiteValue(plan.spendMinor) !== null && finiteValue(plan.paidGmvMinor) !== null);
  const commerce: CommerceData = available && commonWindow && validAmounts ? {
    ...totals, orders: platformCovered ? finiteValue(room?.paidOrders) : null,
    note: "支付归因GMV ÷ 同范围广告消耗，含暂停计划历史消耗",
  } : { spend: null, gmv: null, roi: null, orders: null, note: status === "not_started" ? "未开播，暂无经营数据"
    : !commonWindow ? "归因窗口不一致，暂不合计" : "当前范围暂无完整经营数据" };
  const tasks = room ? scopedJobs(scoped).filter((job) =>
    (snapshot.source === "MOCK" ? job.mode === "MOCK" : job.mode === "API") &&
    job.tenant_id === room.tenantId) : [];
  return {
    data: {
      live: { room, status, platformCovered, online: available && platformCovered ? finiteValue(room?.online) : null,
        viewers: available && platformCovered ? finiteValue(room?.totalViewers) : null },
      commerce, trends: null, tasks, plans,
    } satisfies ModuleData,
    timestamps: { live: validTime(room?.sourceUpdatedAt), commerce: oldestTime([...plans.map((p) => p.sourceUpdatedAt), ...(commerce.orders === null ? [] : [room?.sourceUpdatedAt])]),
      trends: null, tasks: oldestTime(tasks.map((job) => job.updated_at)), plans: oldestTime(plans.map((p) => p.sourceUpdatedAt)) },
  };
}
export function isDelayed(module: ModuleState<unknown>, key: LiveModule, status: LiveStatus, now: number,
  minimumMs = 0, policy: LiveFreshnessPolicy = liveRefreshConfiguration()) {
  if (module.status === "unavailable" || module.status === "idle" || module.status === "loading") return false;
  if (module.failures > 0) return true;
  // Historical sessions do not become stale merely because wall time advances.
  if (status !== "live") return false;
  // Tasks' updated_at is a state-change timestamp, not a heartbeat timestamp.
  const at = key === "tasks" ? module.lastSuccessAt : module.updatedAt ? Date.parse(module.updatedAt) : null;
  return at === null || now - at > Math.max(policy.maxAgeMs[key], minimumMs * 2) || at > now + 60_000;
}
export function formatLiveTime(value: string | number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(new Date(value).getTime())) return "--";
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(value));
}
export function liveDuration(room: LiveSession | null, status: LiveStatus, now: number) {
  const start = validTime(room?.startedAt);
  const end = status === "ended" ? validTime(room?.endedAt) : null;
  if (!start || (status !== "live" && status !== "ended") || (status === "ended" && !end)) return "--";
  const seconds = Math.floor(((end ? Date.parse(end) : now) - Date.parse(start)) / 1000);
  if (seconds < 0) return "--";
  return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map((n) => String(n).padStart(2, "0")).join(":");
}
