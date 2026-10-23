import type { Identity } from "./domain";
import { isAdvertisingProduct } from "./advertising";
import { legacyBudgetTarget } from "./optimization";

export const deliveryPages = [
  "goals",
  "plans",
  "decisions",
  "budgets",
  "executions",
] as const;
export type DeliveryPage = (typeof deliveryPages)[number];
export type Platform = "adq" | "beans";
export type PlatformScope = Platform | "all";
export type Availability =
  | "AUTHORIZED"
  | "UNAUTHORIZED"
  | "NOT_INTEGRATED"
  | "NOT_APPLICABLE"
  | "FORBIDDEN"
  | "ERROR";
export const availabilityNames: Record<Availability, string> = {
  AUTHORIZED: "已支持且已授权",
  UNAUTHORIZED: "已支持但未授权",
  NOT_INTEGRATED: "接口待接入",
  NOT_APPLICABLE: "当前平台不适用",
  FORBIDDEN: "权限不足",
  ERROR: "接口异常",
};
export type Action =
  | "create"
  | "budget"
  | "bid"
  | "pause"
  | "resume"
  | "review"
  | "analyze"
  | "goal"
  | "allocate"
  | "verify"
  | "retry"
  | "rules";
export interface Capability {
  status: Availability;
  reason: string;
  permission: string;
}
export interface PlatformCapabilities {
  platform: Platform;
  accountId: string;
  version: number;
  checkedAt: string;
  actions: Partial<Record<Action, Capability>>;
  execution: "COMMAND_JOB" | "DIRECT" | "MANUAL";
  automationMode: "OBSERVE" | "APPROVAL" | "CONTROLLED";
  refreshSeconds: number | null;
  maxAgeSeconds: number;
  supportedObjects: string[];
  supportedMetrics: string[];
  supportedFilters: string[];
  supportedColumns: string[];
  schema: {
    objectives: string[];
    targets: ("PRODUCT" | "VIDEO" | "LIVE")[];
    bidEditable: boolean;
    minAmount: number;
    maxAmount: number;
  };
}
export interface Shop {
  id: string;
  nativeId: string | null;
  tenantId: string;
  name: string;
}
export interface AdAccount {
  id: string;
  nativeId: string | null;
  shopId: string;
  platform: Platform;
  name: string;
  authorization: Availability;
  currency: "CNY" | "BEAN";
}
export interface DeliveryScope {
  platform: PlatformScope;
  shopId: string;
  accountId: string;
  start: string;
  end: string;
  liveSessionId?: string;
}
export interface PerformanceMetrics {
  spend: number | null;
  attributedGmvMinor: number | null;
  conversions: number | null;
  roi: number | null;
  attributionWindow: string | null;
  statisticDate: string | null;
  /** Only equal nonempty comparable keys may be aggregated. */
  comparableKey: string | null;
}
export interface LinkedObject {
  id: string;
  nativeId: string | null;
  type: "PRODUCT" | "VIDEO" | "LIVE" | "MATERIAL";
  name: string;
}
interface DeliveryObject {
  id: string;
  nativeId: string;
  shopId: string;
  accountId: string;
  name: string;
  version: number | null;
  status: "RUNNING" | "PAUSED" | "REVIEW" | "FAILED" | "COMPLETED" | "UNKNOWN";
  updatedAt: string;
  metrics: PerformanceMetrics;
  linkedObjects: LinkedObject[];
}
export interface Campaign extends DeliveryObject {
  platform: "adq";
  kind: "CAMPAIGN";
  level: "CAMPAIGN" | "ADGROUP" | "AD";
  budgetMinor: number;
  bidMinor: number;
  targetRoi: number | null;
  extensions: { objective?: string; startedAt?: string; endedAt?: string };
}
export interface PromotionOrder extends DeliveryObject {
  platform: "beans";
  kind: "PROMOTION_ORDER";
  contentType: "VIDEO" | "LIVE";
  contentId: string;
  heatingGoal: string;
  amountBeans: number;
  progress: number | null;
  startsAt: string | null;
  endsAt: string | null;
  extensions: { views: number | null; interactions: number | null };
}
export type Plan = Campaign | PromotionOrder;
export interface GoalBreakdown {
  id: string;
  type: "SHOP" | "PLATFORM" | "ACCOUNT" | "PLAN" | "LIVE";
  targetId: string;
  name: string;
  amountMinor: number;
}
export interface TrendPoint {
  date: string;
  actualGmvMinor: number | null;
  targetGmvMinor: number | null;
  spend: number | null;
  budget: number | null;
  roi: number | null;
  targetRoi: number | null;
  forecastGmvMinor?: number | null;
}
export interface BusinessGoal {
  id: string;
  nativeId: string | null;
  shopId: string;
  platform: PlatformScope;
  accountId: string | null;
  name: string;
  period: "DAY" | "WEEK" | "MONTH" | "LIVE" | "SHOP";
  start: string;
  end: string;
  status: "DRAFT" | "ACTIVE" | "CLOSED";
  version: number;
  gmvTargetMinor: number;
  budget: number;
  currency: "CNY" | "BEAN";
  roiFloor: number | null;
  actualBusinessGmvMinor: number | null;
  spent: number | null;
  actualRoi: number | null;
  breakdown: GoalBreakdown[];
  trends: TrendPoint[];
  history: {
    at: string;
    actor: string;
    before: string;
    after: string;
    version: number;
  }[];
}
export interface BudgetAllocation {
  id: string;
  shopId: string;
  platform: Platform;
  accountId: string;
  planId: string | null;
  version: number;
  currency: "CNY" | "BEAN";
  limit: number;
  allocated: number;
  spent: number | null;
  internalStatus: "DRAFT" | "PENDING_APPROVAL" | "APPROVED" | "REJECTED";
  platformStatus: "NOT_SUBMITTED" | "PENDING" | "ACCEPTED" | "FAILED";
  verificationStatus: "PENDING" | "MATCHED" | "UNKNOWN";
  approvalId: string | null;
  executionId: string | null;
}
export interface AccountFunds {
  accountId: string;
  balance: number | null;
  currency: "CNY" | "BEAN";
  syncedAt: string | null;
  warning: string | null;
}
export const decisionTypes = {
  ROI: "ROI异常",
  PACING: "消耗速度",
  CPA: "转化成本",
  EFFICIENCY: "预算效率",
  CREATIVE: "素材表现",
  LIVE: "直播节奏",
  STATUS: "计划状态",
  SYNC: "数据同步",
} as const;
export interface AIDecision {
  id: string;
  platform: Platform;
  shopId: string;
  accountId: string;
  planId: string;
  goalId: string | null;
  type: keyof typeof decisionTypes;
  title: string;
  explanation: string;
  recommendation: string;
  risk: "LOW" | "MEDIUM" | "HIGH";
  status:
    | "OPEN"
    | "DEFERRED"
    | "PENDING_APPROVAL"
    | "ACCEPTED"
    | "REJECTED"
    | "EXECUTED";
  discoveredAt: string;
  version: number;
  evidence: { label: string; value: string; sourceId: string }[];
  observation: {
    start: string;
    end: string;
    sampleSize: number | null;
    delayMinutes: number | null;
    confidence: string;
    limitations: string[];
  };
  proposedAction:
    | "CHANGE_DAILY_BUDGET"
    | "CHANGE_BID"
    | "PAUSE_AD_PLAN"
    | "RESUME_AD_PLAN"
    | null;
  proposedValue: number | boolean | null;
  impact: string | null;
  approvalId: string | null;
  executionId: string | null;
  outcome: string | null;
  history?: { at: string; actorId: string; status: string; comment: string }[];
}
export interface ApprovalRequest {
  id: string;
  accountId: string;
  before: unknown;
  after: unknown;
  reason: string;
  decisionId: string | null;
  budgetId: string | null;
  executionId: string | null;
  status: "PENDING" | "APPROVED" | "REJECTED";
  actorId: string | null;
  version: number;
}
export const executionStates = {
  PENDING_APPROVAL: "待审批",
  PENDING: "待执行",
  EXECUTING: "执行中",
  VERIFYING: "待核验",
  EFFECTIVE: "已生效",
  FAILED: "执行失败",
  UNKNOWN: "结果未知",
  CANCELLED: "已取消",
  PARTIAL: "部分成功",
  MANUAL: "待人工核查",
} as const;
export interface ExecutionTask {
  id: string;
  nativeId: string | null;
  platform: Platform;
  shopId: string;
  accountId: string;
  planId: string;
  goalId: string | null;
  decisionId: string | null;
  budgetId: string | null;
  approvalId: string | null;
  action: string;
  initiator: "MANUAL" | "AI" | "RULE";
  actorId: string | null;
  approverId: string | null;
  status: keyof typeof executionStates;
  before: unknown;
  after: unknown;
  version: number;
  createdAt: string;
  updatedAt: string;
  requestStatus: string;
  response: unknown;
  verification: string;
  failure: string | null;
  safeRetry: boolean;
  idempotencyKey: string | null;
  history: { at: string; status: string; detail: string }[];
  legacyJobId?: string;
}
export interface SyncStatus {
  state:
    | "READY"
    | "LOADING"
    | "EMPTY"
    | "UNAUTHORIZED"
    | "NOT_INTEGRATED"
    | "STALE"
    | "FAILED"
    | "FORBIDDEN";
  source: "API" | "MOCK";
  syncedAt: string | null;
  statisticStart: string;
  statisticEnd: string;
  message: string;
}
export interface RiskBoundaries {
  accountId: string;
  dailyLimit: number;
  singleChangeLimit: number;
  approvalThreshold: number;
  minObservationMinutes: number;
  maxChangesPerDay: number;
  currency: "CNY" | "BEAN";
  version: number;
}
export interface DeliveryData {
  contractVersion: "delivery-v8.0";
  scope: DeliveryScope;
  source: "API" | "MOCK";
  shops: Shop[];
  accounts: AdAccount[];
  capabilities: PlatformCapabilities[];
  plans: Plan[];
  goals: BusinessGoal[];
  decisions: AIDecision[];
  budgets: BudgetAllocation[];
  funds: AccountFunds[];
  executions: ExecutionTask[];
  approvals: ApprovalRequest[];
  boundaries: RiskBoundaries[];
  trends: { planId: string; points: TrendPoint[] }[];
  budgetTrends: { currency: "CNY" | "BEAN"; points: TrendPoint[] }[];
  sync: SyncStatus;
}
export function dailyGoalGaps(points: TrendPoint[], periodStart: string) {
  const sorted = [...points].sort((a, b) => a.date.localeCompare(b.date));
  return sorted.map((point, i) => {
    const previous = sorted[i - 1];
    const first = !previous && point.date === periodStart;
    const consecutive =
      previous &&
      Date.parse(`${point.date}T00:00:00Z`) -
        Date.parse(`${previous.date}T00:00:00Z`) ===
        86_400_000;
    const known =
      point.actualGmvMinor !== null &&
      point.targetGmvMinor !== null &&
      (first ||
        (consecutive &&
          previous.actualGmvMinor !== null &&
          previous.targetGmvMinor !== null));
    return {
      date: point.date,
      gapMinor: known
        ? point.actualGmvMinor! -
          (previous?.actualGmvMinor ?? 0) -
          (point.targetGmvMinor! - (previous?.targetGmvMinor ?? 0))
        : null,
    };
  });
}
export const platforms = {
  adq: {
    name: "腾讯广告 ADQ",
    shortName: "ADQ",
    unit: "元",
    currency: "CNY",
    object: "广告计划",
    createName: "新建广告计划",
    levels: ["推广计划", "广告组", "广告"],
    filters: ["状态", "广告层级"],
    metrics: ["广告消耗", "支付归因GMV", "转化", "投放ROI"],
    operations: ["budget", "bid", "pause", "resume"],
    steps: [
      "账户与推广目标",
      "商品 / 视频 / 直播对象",
      "预算、出价与排期",
      "风险校验与预览",
    ],
  },
  beans: {
    name: "视频号微信豆加热",
    shortName: "微信豆",
    unit: "微信豆",
    currency: "BEAN",
    object: "加热订单",
    createName: "配置加热方案",
    levels: ["短视频", "直播"],
    filters: ["状态", "内容类型"],
    metrics: ["微信豆消耗", "播放", "互动", "加热进度"],
    operations: [],
    steps: [
      "视频号账户",
      "短视频 / 直播内容",
      "加热目标与额度",
      "能力校验与操作方案",
    ],
  },
} as const;
export const planStatusNames = {
  RUNNING: "投放中",
  PAUSED: "已暂停",
  REVIEW: "待审核",
  FAILED: "异常",
  COMPLETED: "已完成",
  UNKNOWN: "待核实",
};
export function isPlatform(value: unknown): value is Platform {
  return value === "adq" || value === "beans";
}
export function isDeliveryPage(value: string): value is DeliveryPage {
  return deliveryPages.some((page) => page === value);
}
export function todayShanghai() {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}
export function validDate(value: string | null): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return (
    Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
  );
}
export function parseScope(
  query: URLSearchParams,
  fallback: Partial<DeliveryScope> = {},
): DeliveryScope {
  const start = validDate(query.get("start"))
    ? query.get("start")!
    : (fallback.start ?? todayShanghai());
  const end =
    validDate(query.get("end")) && query.get("end")! >= start
      ? query.get("end")!
      : start;
  const product=query.get("product");
  const platform = isPlatform(product) ? product : (product==="wxshop"||isAdvertisingProduct(product)) ? "all" : isPlatform(query.get("platform"))
    ? (query.get("platform") as Platform)
    : "all";
  return {
    platform,
    shopId: query.get("shopId") || fallback.shopId || "all",
    accountId: platform === "all" ? "all" : query.get("accountId") || "all",
    start,
    end,
    ...(query.get("liveSessionId")
      ? { liveSessionId: query.get("liveSessionId")! }
      : {}),
  };
}
export function scopeKey(scope: DeliveryScope) {
  return JSON.stringify([
    scope.platform,
    scope.shopId,
    scope.accountId,
    scope.start,
    scope.end,
    scope.liveSessionId ?? "all",
  ]);
}
export function normalizeScope(
  scope: DeliveryScope,
  shops: Shop[],
  accounts: AdAccount[],
): DeliveryScope {
  const shopId = shops.some((s) => s.id === scope.shopId)
    ? scope.shopId
    : "all";
  const valid = accounts.some(
    (a) =>
      a.id === scope.accountId &&
      (scope.platform === "all" || a.platform === scope.platform) &&
      (shopId === "all" || a.shopId === shopId),
  );
  return {
    ...scope,
    shopId,
    accountId: scope.platform !== "all" && valid ? scope.accountId : "all",
  };
}
export function deliveryHref(
  page: string,
  scope: DeliveryScope,
  ids: Record<string, string | null | undefined> = {},
) {
  const q = new URLSearchParams({
    platform: scope.platform,
    shopId: scope.shopId,
    accountId: scope.accountId,
    start: scope.start,
    end: scope.end,
  });
  if (scope.liveSessionId) q.set("liveSessionId", scope.liveSessionId);
  Object.entries(ids).forEach(([key, value]) => {
    if (value) q.set(key, value);
  });
  return page === "budgets" ? legacyBudgetTarget(q) : `/app/${page}?${q}`;
}
export function actionAvailability(
  data: DeliveryData,
  identity: Identity | null,
  accountId: string,
  action: Action,
): Capability {
  const unavailable = (status: Availability, reason: string): Capability => ({
    status,
    reason,
    permission: "",
  });
  const account = data.accounts.find((a) => a.id === accountId);
  if (!account)
    return unavailable("NOT_INTEGRATED", "先选择具体平台与投放账户");
  if (
    !["analyze", "verify"].includes(action) &&
    (data.scope.platform !== account.platform ||
      data.scope.accountId !== accountId)
  )
    return unavailable(
      "NOT_APPLICABLE",
      "修改和审批前请切换到此平台及具体账户工作区",
    );
  if (account.authorization !== "AUTHORIZED")
    return unavailable(
      account.authorization,
      availabilityNames[account.authorization],
    );
  const cap = data.capabilities.find(
    (c) => c.accountId === account.id && c.platform === account.platform,
  );
  const rule = cap?.actions[action];
  if (!rule)
    return unavailable("NOT_INTEGRATED", "后端尚未声明此账户的操作能力");
  if (rule.status !== "AUTHORIZED") return rule;
  if (action === "bid" && !cap?.schema.bidEditable)
    return unavailable("NOT_APPLICABLE", "当前账户不支持修改出价配置");
  if (!identity || !identity.permissions.includes(rule.permission))
    return unavailable("FORBIDDEN", `缺少权限 ${rule.permission}`);
  if (data.source !== "API")
    return unavailable(
      "NOT_INTEGRATED",
      "演示数据仅用于预览，不产生正式投放变更",
    );
  if (
    !["READY", "EMPTY"].includes(data.sync.state) ||
    !data.sync.syncedAt ||
    !Number.isFinite(Date.parse(data.sync.syncedAt)) ||
    !cap ||
    Date.now() - Date.parse(cap.checkedAt) > cap.maxAgeSeconds * 1000 ||
    Date.now() - Date.parse(data.sync.syncedAt) > cap.maxAgeSeconds * 1000
  )
    return unavailable("ERROR", "数据或能力声明已过期，请同步后重新校验");
  return rule;
}
export function amount(
  value: number | null | undefined,
  currency: "CNY" | "BEAN" = "CNY",
) {
  if (value == null || !Number.isFinite(value)) return "--";
  return currency === "CNY"
    ? `¥${(value / 100).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`
    : `${value.toLocaleString("zh-CN")} 豆`;
}
export function time(value: string | null | undefined) {
  return value && Number.isFinite(Date.parse(value))
    ? new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(value))
    : "未提供";
}
export function compatibleRoi(plans: Plan[]) {
  if (
    !plans.length ||
    plans.some(
      (p) =>
        p.platform !== "adq" ||
        !p.metrics.comparableKey ||
        p.metrics.spend === null ||
        p.metrics.attributedGmvMinor === null,
    ) ||
    new Set(plans.map((p) => p.metrics.comparableKey)).size !== 1
  )
    return null;
  const spend = plans.reduce((s, p) => s + p.metrics.spend!, 0);
  return spend > 0
    ? plans.reduce((s, p) => s + p.metrics.attributedGmvMinor!, 0) / spend
    : null;
}
export function redact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        /token|secret|password|cookie|authorization|credential|signature|idempotency/i.test(
          key,
        )
          ? "[已脱敏]"
          : redact(item),
      ]),
    );
  if (typeof value === "string")
    return value
      .replace(/(Bearer\s+)[\w.\-]+/gi, "$1[已脱敏]")
      .replace(
        /((?:access_?token|refresh_?token|secret|password|signature|cookie|authorization|credential|idempotency_?key)["']?\s*[=:]\s*["']?)[^"'\s&,;]+/gi,
        "$1[已脱敏]",
      );
  return value;
}
export interface PlanFilter {
  search: string;
  status: string;
  level: string;
  sort: "name" | "spend" | "updated";
  descending: boolean;
  page: number;
  pageSize: number;
  columns: string[];
}
export const defaultPlanFilter: PlanFilter = {
  search: "",
  status: "all",
  level: "all",
  sort: "updated",
  descending: true,
  page: 1,
  pageSize: 10,
  columns: ["spend", "result", "budget", "bid", "updated"],
};
export function filterPlans(plans: Plan[], filter: PlanFilter) {
  const result = plans.filter(
    (p) =>
      (!filter.search ||
        `${p.id} ${p.nativeId} ${p.name}`
          .toLowerCase()
          .includes(filter.search.toLowerCase())) &&
      (filter.status === "all" || p.status === filter.status) &&
      (filter.level === "all" ||
        (p.kind === "CAMPAIGN" ? p.level : p.contentType) === filter.level),
  );
  return result.sort(
    (a, b) =>
      (filter.sort === "name"
        ? a.name.localeCompare(b.name, "zh-CN")
        : filter.sort === "spend"
          ? (a.metrics.spend ?? -1) - (b.metrics.spend ?? -1)
          : Date.parse(a.updatedAt) - Date.parse(b.updatedAt)) *
      (filter.descending ? -1 : 1),
  );
}
/** A cancelled or superseded read may never publish into another account. */
export class LatestDeliveryRead {
  private sequence = 0;
  private active: AbortController | null = null;
  cancel() {
    this.sequence++;
    this.active?.abort();
  }
  async run<T>(
    read: (signal: AbortSignal) => Promise<T>,
    accept: (value: T) => void,
    reject: (error: unknown) => void,
  ) {
    this.cancel();
    const sequence = this.sequence;
    const controller = new AbortController();
    this.active = controller;
    try {
      const result = await read(controller.signal);
      if (sequence === this.sequence && !controller.signal.aborted)
        accept(result);
    } catch (error) {
      if (sequence === this.sequence && !controller.signal.aborted)
        reject(error);
    }
  }
}
