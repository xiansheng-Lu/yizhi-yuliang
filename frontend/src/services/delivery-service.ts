import type { FrontendSnapshot, Identity } from "../lib/domain";
import {
  actionNames,
  formatJobValue,
  type CommandJob,
  type CreateJobRequest,
} from "../lib/command-job";
import {
  actionAvailability,
  executionStates,
  isPlatform,
  normalizeScope,
  planStatusNames,
  scopeKey,
  validDate,
  type Action,
  type AdAccount,
  type BusinessGoal,
  type DeliveryData,
  type DeliveryScope,
  type ExecutionTask,
  type Plan,
  type PlatformCapabilities,
} from "../lib/delivery";
import { ApiClient } from "./api-client";
import { ServiceError, type FrontendService } from "./contracts";

export interface DeliveryContext {
  shops: DeliveryData["shops"];
  accounts: AdAccount[];
  capabilities: PlatformCapabilities[];
}
export interface CreatePlanRequest {
  accountId: string;
  platform: "adq" | "beans";
  shopId: string;
  name: string;
  objective: string;
  target: { type: "PRODUCT" | "VIDEO" | "LIVE"; id: string };
  amount: number;
  bidMinor?: number;
  start: string;
  end: string;
  idempotencyKey: string;
  capabilityVersion: number;
}
export interface GoalRequest
  extends Pick<
    BusinessGoal,
    | "name"
    | "period"
    | "start"
    | "end"
    | "gmvTargetMinor"
    | "budget"
    | "currency"
    | "roiFloor"
    | "breakdown"
  > {
  scope: DeliveryScope;
  goalId?: string;
  expectedVersion?: number;
  idempotencyKey: string;
}
export interface BudgetRequest {
  scope: DeliveryScope;
  allocationId: string;
  planId: string | null;
  amount: number;
  expectedVersion: number;
  reason: string;
  decisionId?: string;
  idempotencyKey: string;
}
export interface NewBudgetRequest {
  scope: DeliveryScope;
  accountId: string;
  planId: string | null;
  amount: number;
  currency: "CNY" | "BEAN";
  parentAllocationId: string | null;
  expectedParentVersion: number | null;
  reason: string;
  idempotencyKey: string;
}
export interface DecisionReviewRequest {
  decision: "APPROVE" | "REJECT" | "DEFER" | "MODIFY";
  expectedVersion: number;
  comment: string;
  proposedValue?: number | boolean;
  idempotencyKey: string;
}
export interface ApprovalReviewRequest {
  expectedVersion: number;
  decision: "APPROVE" | "REJECT";
  comment: string;
  idempotencyKey: string;
}
export interface AnalysisRequest {
  scope: DeliveryScope;
  kind: "GOAL_GAP" | "PLAN" | "DECISION" | "BUDGET" | "EXECUTION";
  objectIds: string[];
  idempotencyKey: string;
}
export interface MutationResult {
  resourceId: string;
  state: string;
  executionId: string | null;
  approvalId: string | null;
}
export interface RiskPreview {
  token: string;
  expiresAt: string;
  checks: { passed: boolean; message: string }[];
  requiresApproval: boolean;
  expectedVersion: number;
}
export interface PlanChangeRequest {
  planId: string;
  accountId: string;
  action:
    | "CHANGE_DAILY_BUDGET"
    | "CHANGE_BID"
    | "PAUSE_AD_PLAN"
    | "RESUME_AD_PLAN";
  newValue: number | boolean;
  expectedVersion: number | null;
  decisionId?: string;
  idempotencyKey: string;
}
export interface PolicyRequest {
  accountId: string;
  expectedVersion: number;
  mode: "OBSERVE" | "APPROVAL" | "CONTROLLED";
  roiFloor: number;
  pacingThreshold: number;
  budgetLimit: number;
  minObservationMinutes: number;
  singleChangeLimit: number;
  maxChangesPerDay: number;
  idempotencyKey: string;
}

function empty(scope: DeliveryScope, source: "API" | "MOCK"): DeliveryData {
  return {
    contractVersion: "delivery-v8.0",
    scope,
    source,
    shops: [],
    accounts: [],
    capabilities: [],
    plans: [],
    goals: [],
    budgets: [],
    decisions: [],
    funds: [],
    executions: [],
    approvals: [],
    boundaries: [],
    trends: [],
    budgetTrends: [],
    sync: {
      source,
      state: "NOT_INTEGRATED",
      statisticStart: scope.start,
      statisticEnd: scope.end,
      syncedAt: null,
      message: "投放工作区接口待接入。",
    },
  };
}
export function legacyExecution(
  job: CommandJob,
  account: AdAccount,
): ExecutionTask {
  const statuses: Record<CommandJob["status"], ExecutionTask["status"]> = {
    DRAFT: "PENDING_APPROVAL",
    PENDING_APPROVAL: "PENDING_APPROVAL",
    APPROVED: "PENDING",
    EXECUTING: "EXECUTING",
    VERIFYING: "VERIFYING",
    SUCCEEDED: "EFFECTIVE",
    FAILED:
      job.execution_receipt?.verification === "UNKNOWN" ||
      job.error?.code.includes("TIMEOUT")
        ? "UNKNOWN"
        : "FAILED",
    REJECTED: "CANCELLED",
    CANCELLED: "CANCELLED",
    EXPIRED: "CANCELLED",
    BLOCKED: "MANUAL",
    MANUAL_REQUIRED: "MANUAL",
  };
  // Platform acceptance alone never constitutes an effective change.
  const status =
    job.status === "SUCCEEDED" &&
    (job.execution_receipt?.verification !== "MATCHED" ||
      (job.execution_receipt.source !== "PLATFORM" && job.mode === "API"))
      ? "VERIFYING"
      : statuses[job.status];
  return {
    id: job.job_id,
    nativeId: job.execution_receipt?.platform_receipt_id ?? null,
    platform: account.platform,
    shopId: job.shop_id,
    accountId: account.id,
    planId: job.target_id,
    goalId: null,
    decisionId: null,
    budgetId: null,
    approvalId: job.approval.state === "NOT_REQUESTED" ? null : job.job_id,
    action: actionNames[job.action_type],
    initiator: job.source === "AUTOPILOT" ? "RULE" : "MANUAL",
    actorId: null,
    approverId: job.approval.actor_id,
    status,
    before: formatJobValue(job.old_value),
    after: formatJobValue(job.new_value),
    version: job.version,
    createdAt: job.created_at,
    updatedAt: job.updated_at,
    requestStatus:
      job.execution_receipt?.accepted === true
        ? "平台已受理"
        : "未取得受理证明",
    response: job.execution_receipt,
    verification: job.execution_receipt?.verification ?? "未核验",
    failure: job.error?.message ?? null,
    safeRetry: false,
    idempotencyKey: job.idempotency_key,
    history: job.history,
    legacyJobId: job.job_id,
  };
}
/** Existing snapshot remains the read source when the new contract is absent.
 * No wxpromo, Qianchuan or Juguang records are re-labelled as supported channels. */
export function adaptDeliverySnapshot(
  snapshot: FrontendSnapshot | null,
  identity: Identity,
  scope: DeliveryScope,
): DeliveryData {
  const source = snapshot?.source ?? identity.mode;
  const data = empty(scope, source);
  if (!snapshot || snapshot.source !== identity.mode) return data;
  const shops = snapshot.shops.filter(
    (s) => s.tenantId === identity.tenantId && identity.shopIds.includes(s.id),
  );
  data.shops = shops.map((s) => ({
    id: s.id,
    nativeId: null,
    tenantId: s.tenantId,
    name: s.name,
  }));
  data.accounts = snapshot.adAccounts
    .filter(
      (a) =>
        a.tenantId === identity.tenantId &&
        shops.some((s) => s.id === a.shopId) &&
        isPlatform(a.connectorId),
    )
    .map((a) => ({
      id: a.id,
      nativeId: null,
      platform: a.connectorId as "adq" | "beans",
      shopId: a.shopId,
      name: a.name,
      authorization: "NOT_INTEGRATED",
      currency: a.connectorId === "beans" ? "BEAN" : "CNY",
    }));
  const ids = new Set(
    data.accounts
      .filter(
        (a) =>
          (scope.shopId === "all" || a.shopId === scope.shopId) &&
          (scope.platform === "all" || a.platform === scope.platform) &&
          (scope.accountId === "all" || a.id === scope.accountId),
      )
      .map((a) => a.id),
  );
  const dateMatches =
    scope.start === snapshot.scope.date && scope.end === snapshot.scope.date;
  data.plans = snapshot.plans
    .filter(
      (p) =>
        p.tenantId === identity.tenantId &&
        ids.has(p.adAccountId) &&
        data.accounts.find((a) => a.id === p.adAccountId)?.platform === "adq" &&
        (!scope.liveSessionId || p.liveSessionId === scope.liveSessionId),
    )
    .map(
      (p): Plan => ({
        id: p.id,
        nativeId: p.nativePlanId,
        shopId: p.shopId,
        accountId: p.adAccountId,
        name: p.name,
        version: null,
        platform: "adq",
        kind: "CAMPAIGN",
        level: "CAMPAIGN",
        budgetMinor: p.budgetMinor,
        bidMinor: p.bidMinor,
        targetRoi: p.targetRoi,
        status:
          p.enabled === null
            ? "UNKNOWN"
            : p.enabled
              ? "RUNNING"
              : "PAUSED",
        updatedAt: p.sourceUpdatedAt,
        metrics: {
          spend: dateMatches ? p.spendMinor : null,
          attributedGmvMinor: dateMatches ? p.paidGmvMinor : null,
          conversions: null,
          roi:
            dateMatches && p.spendMinor && p.paidGmvMinor !== null
              ? p.paidGmvMinor / p.spendMinor
              : null,
          attributionWindow: p.attributionWindow,
          statisticDate: snapshot.scope.date,
          comparableKey: null,
        },
        linkedObjects:
          p.liveSessionId &&
          snapshot.liveSessions.some(
            (s) => s.id === p.liveSessionId && s.shopId === p.shopId,
          )
            ? [
                {
                  id: p.liveSessionId,
                  nativeId: null,
                  type: "LIVE",
                  name: snapshot.liveSessions.find(
                    (s) => s.id === p.liveSessionId,
                  )!.name,
                },
              ]
            : [],
        extensions: {},
      }),
    );
  data.executions = snapshot.tasks
    .filter((j) => {
      const day = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Shanghai",
      }).format(new Date(j.created_at));
      return (
        j.mode === source &&
        j.tenant_id === identity.tenantId &&
        j.account_id &&
        ids.has(j.account_id) &&
        j.target_type === "AD_PLAN" &&
        data.plans.some((p) => p.id === j.target_id) &&
        day <= scope.end &&
        day >= scope.start
      );
    })
    .map((j) =>
      legacyExecution(j, data.accounts.find((a) => a.id === j.account_id)!),
    );
  // The legacy snapshot has per-object publication times, but no workspace
  // successful-fetch timestamp. Never mislabel publication as synchronization.
  data.sync.syncedAt = null;
  data.sync.message =
    source === "MOCK"
      ? "演示模式：仅展示现有 ADQ 样例，所有正式写入已隔离。微信豆订单不由广告计划推造。"
      : "沿用 /v1/frontend/snapshot 只读数据；账户权限、能力声明及新增工作区接口待联调。";
  return data;
}
function scoped(
  data: DeliveryData,
  identity: Identity,
  requested: DeliveryScope,
) {
  if (
    data.source !== "API" ||
    data.sync.source !== "API" ||
    scopeKey(data.scope) !== scopeKey(requested) ||
    data.sync.statisticStart !== requested.start ||
    data.sync.statisticEnd !== requested.end
  )
    throw new ServiceError(
      "INVALID_RESPONSE",
      "投放响应来源或统计范围不匹配，已丢弃。",
    );
  const shops = data.shops.filter(
    (s) => s.tenantId === identity.tenantId && identity.shopIds.includes(s.id),
  );
  const accounts = data.accounts.filter(
    (a) =>
      shops.some((s) => s.id === a.shopId) &&
      isPlatform(a.platform) &&
      (a.platform === "adq" ? a.currency === "CNY" : a.currency === "BEAN"),
  );
  const visible = accounts.filter(
    (a) =>
      (requested.shopId === "all" || a.shopId === requested.shopId) &&
      (requested.platform === "all" || a.platform === requested.platform) &&
      (requested.accountId === "all" || a.id === requested.accountId),
  );
  const has = (accountId: string, shopId?: string, platform?: string) =>
    visible.some(
      (a) =>
        a.id === accountId &&
        (!shopId || a.shopId === shopId) &&
        (!platform || a.platform === platform),
    );
  let plans = data.plans.filter(
    (p) =>
      has(p.accountId, p.shopId, p.platform) &&
      ((p.kind === "CAMPAIGN" && p.platform === "adq") ||
        (p.kind === "PROMOTION_ORDER" && p.platform === "beans")) &&
      (!requested.liveSessionId ||
        p.linkedObjects.some(
          (o) => o.type === "LIVE" && o.id === requested.liveSessionId,
        )),
  );
  plans = plans.map((plan) => {
    const cap = data.capabilities.find(
      (c) => c.accountId === plan.accountId && c.platform === plan.platform,
    );
    if (!cap) return plan;
    const metrics = { ...plan.metrics };
    for (const field of [
      "spend",
      "attributedGmvMinor",
      "roi",
      "conversions",
    ] as const)
      if (!cap.supportedMetrics.includes(field)) metrics[field] = null;
    return plan.kind === "PROMOTION_ORDER"
      ? {
          ...plan,
          metrics,
          progress: cap.supportedMetrics.includes("progress")
            ? plan.progress
            : null,
          extensions: {
            views: cap.supportedMetrics.includes("views")
              ? plan.extensions.views
              : null,
            interactions: cap.supportedMetrics.includes("interactions")
              ? plan.extensions.interactions
              : null,
          },
        }
      : { ...plan, metrics };
  });
  const planIds = new Set(plans.map((p) => p.id));
  const decisions = data.decisions.filter(
    (d) => has(d.accountId, d.shopId, d.platform) && planIds.has(d.planId),
  );
  const budgets = data.budgets.filter(
    (b) =>
      has(b.accountId, b.shopId, b.platform) &&
      (!b.planId || planIds.has(b.planId)) &&
      visible.find((a) => a.id === b.accountId)?.currency === b.currency,
  );
  const executions = data.executions
    .filter(
      (t) => has(t.accountId, t.shopId, t.platform) && planIds.has(t.planId),
    )
    .map((t) =>
      t.status === "EFFECTIVE" && (t.verification !== "MATCHED" || !t.nativeId)
        ? { ...t, status: "VERIFYING" as const }
        : t,
    );
  return {
    ...data,
    shops,
    accounts,
    plans,
    decisions,
    budgets,
    executions,
    capabilities: data.capabilities.filter((c) =>
      has(c.accountId, undefined, c.platform),
    ),
    goals: data.goals.filter(
      (g) =>
        shops.some((s) => s.id === g.shopId) &&
        (requested.shopId === "all" || g.shopId === requested.shopId) &&
        (requested.platform === "all" || g.platform === requested.platform) &&
        (requested.accountId === "all" ||
          g.accountId === requested.accountId) &&
        g.start <= requested.end &&
        g.end >= requested.start,
    ),
    funds: data.funds.filter(
      (f) =>
        has(f.accountId) &&
        visible.find((a) => a.id === f.accountId)?.currency === f.currency,
    ),
    approvals: data.approvals.filter(
      (a) =>
        has(a.accountId) &&
        ((a.decisionId && decisions.some((d) => d.id === a.decisionId)) ||
          (a.budgetId && budgets.some((b) => b.id === a.budgetId)) ||
          (a.executionId && executions.some((t) => t.id === a.executionId))),
    ),
    boundaries: data.boundaries.filter(
      (b) =>
        has(b.accountId) &&
        visible.find((a) => a.id === b.accountId)?.currency === b.currency,
    ),
    trends: data.trends.filter((t) => planIds.has(t.planId)),
    budgetTrends: data.budgetTrends.filter(
      (t) =>
        requested.platform === "all" ||
        t.currency === (requested.platform === "beans" ? "BEAN" : "CNY"),
    ),
  };
}
export function validateDeliveryData(value: DeliveryData) {
  const arrays = [
    "shops",
    "accounts",
    "capabilities",
    "plans",
    "goals",
    "budgets",
    "funds",
    "decisions",
    "executions",
    "approvals",
    "boundaries",
    "trends",
    "budgetTrends",
  ] as const;
  if (
    !value ||
    value.contractVersion !== "delivery-v8.0" ||
    arrays.some((key) => !Array.isArray(value[key])) ||
    !value.scope ||
    !value.sync
  )
    throw new ServiceError("INVALID_RESPONSE", "投放工作区数据结构不完整。");
  const fail = (message: string): never => {
    throw new ServiceError("INVALID_RESPONSE", message);
  };
  const nonnegative = (v: unknown) =>
    typeof v === "number" && Number.isFinite(v) && v >= 0;
  const metric = (v: unknown) => v === null || nonnegative(v);
  if (
    !validDate(value.scope.start) ||
    !validDate(value.scope.end) ||
    value.scope.end < value.scope.start
  )
    fail("统计周期无效。");
  for (const c of value.capabilities)
    if (
      !c ||
      !isPlatform(c.platform) ||
      !c.accountId ||
      !c.actions ||
      !Array.isArray(c.supportedObjects) ||
      !Array.isArray(c.supportedMetrics) ||
      !Array.isArray(c.supportedFilters) ||
      !Array.isArray(c.supportedColumns) ||
      !c.schema ||
      !Array.isArray(c.schema.objectives) ||
      !Array.isArray(c.schema.targets) ||
      !Number.isFinite(Date.parse(c.checkedAt)) ||
      !Number.isInteger(c.version) ||
      c.version < 1 ||
      !Number.isFinite(c.maxAgeSeconds) ||
      c.maxAgeSeconds <= 0 ||
      !nonnegative(c.schema.minAmount) ||
      !nonnegative(c.schema.maxAmount) ||
      c.schema.maxAmount < c.schema.minAmount
    )
      fail("账户能力声明无效，操作已关闭。");
  const ids = new Set<string>();
  for (const p of value.plans) {
    if (
      !p ||
      !p.id ||
      ids.has(p.id) ||
      !p.nativeId ||
      !p.metrics ||
      !Array.isArray(p.linkedObjects) ||
      !p.extensions ||
      (p.updatedAt !== null && !Number.isFinite(Date.parse(p.updatedAt))) ||
      !(p.status in planStatusNames) ||
      ![
        p.metrics.spend,
        p.metrics.attributedGmvMinor,
        p.metrics.conversions,
        p.metrics.roi,
      ].every(metric) ||
      !Number.isInteger(p.version) ||
      p.version! < 1
    )
      fail("投放对象结构无效。");
    ids.add(p.id);
    if (
      p.kind === "CAMPAIGN"
        ? p.platform !== "adq" ||
          (p.budgetMinor !== null && !nonnegative(p.budgetMinor)) ||
          (p.bidMinor !== null && !nonnegative(p.bidMinor))
        : p.kind !== "PROMOTION_ORDER" ||
          p.platform !== "beans" ||
          !nonnegative(p.amountBeans) ||
          !metric(p.progress) ||
          (p.progress !== null && p.progress > 100) ||
          !p.contentId
    )
      fail("投放对象与平台模型不匹配。");
  }
  for (const g of value.goals)
    if (
      !g ||
      !g.id ||
      !Array.isArray(g.breakdown) ||
      !Array.isArray(g.trends) ||
      !Array.isArray(g.history) ||
      !nonnegative(g.gmvTargetMinor) ||
      !nonnegative(g.budget) ||
      !metric(g.actualBusinessGmvMinor) ||
      !metric(g.spent) ||
      !metric(g.actualRoi)
    )
      fail("经营目标结构无效。");
  for (const x of value.decisions)
    if (
      !x ||
      !x.id ||
      !x.observation ||
      !Array.isArray(x.observation.limitations) ||
      !Array.isArray(x.evidence) ||
      ![
        "OPEN",
        "DEFERRED",
        "PENDING_APPROVAL",
        "ACCEPTED",
        "REJECTED",
        "EXECUTED",
      ].includes(x.status)
    )
      fail("AI证据结构无效。");
  for (const t of value.executions)
    if (
      !t ||
      !t.id ||
      !(t.status in executionStates) ||
      !Array.isArray(t.history) ||
      !Number.isFinite(Date.parse(t.createdAt)) ||
      !Number.isFinite(Date.parse(t.updatedAt)) ||
      !Number.isInteger(t.version)
    )
      fail("执行任务结构无效。");
  for (const a of value.approvals)
    if (
      !a ||
      !a.id ||
      !a.accountId ||
      !Number.isInteger(a.version) ||
      a.version < 1 ||
      !["PENDING", "APPROVED", "REJECTED"].includes(a.status) ||
      a.before === undefined ||
      a.after === undefined
    )
      fail("审批资源缺少账户、版本或变更内容。");
  for (const b of value.budgets)
    if (
      !b ||
      !b.id ||
      !nonnegative(b.limit) ||
      !nonnegative(b.allocated) ||
      !metric(b.spent) ||
      !["CNY", "BEAN"].includes(b.currency)
    )
      fail("预算结构无效。");
  for (const t of value.trends)
    if (
      !t ||
      !Array.isArray(t.points) ||
      t.points.some(
        (p) =>
          !validDate(p.date) ||
          p.date < value.scope.start ||
          p.date > value.scope.end,
      )
    )
      fail("趋势日期不属于当前统计周期。");
  return value;
}
export function retainDeliveryFailure(
  data: DeliveryData,
  error: unknown,
): DeliveryData {
  if (
    error instanceof ServiceError &&
    ["FORBIDDEN", "UNAUTHENTICATED"].includes(error.code)
  )
    return {
      ...empty(data.scope, data.source),
      shops: data.shops,
      accounts: data.accounts.map((a) => ({
        ...a,
        authorization: "FORBIDDEN",
      })),
      sync: {
        ...data.sync,
        syncedAt: null,
        state: "FORBIDDEN",
        message: "当前范围无数据访问权限，已清除历史业务数据。",
      },
    };
  return {
    ...data,
    capabilities: [],
    sync: {
      ...data.sync,
      state: "STALE",
      message: "同步失败，保留此范围上次可用数据，投放操作已关闭。",
    },
  };
}
export class DeliveryService {
  private retryUntil = 0;
  constructor(readonly service: FrontendService) {}
  private async transport<T>(
    path: string,
    method = "GET",
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<T> {
    if (!(this.service instanceof ApiClient))
      throw new ServiceError(
        "API_NOT_CONFIGURED",
        "演示模式不调用正式投放接口。",
      );
    if (Date.now() < this.retryUntil)
      throw new ServiceError(
        "RATE_LIMITED",
        "服务端要求暂缓请求，请等待限流窗口结束后同步。",
        Math.ceil((this.retryUntil - Date.now()) / 1000),
      );
    try {
      return await this.service.deliveryRequest<T>(
        `/v1/delivery/${path}`,
        method,
        body,
        signal,
      );
    } catch (e) {
      if (e instanceof ServiceError && e.code === "RATE_LIMITED")
        this.retryUntil = Date.now() + (e.retryAfterSeconds ?? 30) * 1000;
      throw e;
    }
  }
  async load(
    scope: DeliveryScope,
    snapshot: FrontendSnapshot | null,
    identity: Identity,
    signal: AbortSignal,
  ): Promise<DeliveryData> {
    const fallback = adaptDeliverySnapshot(snapshot, identity, scope);
    if (this.service.mode !== "api") return fallback;
    try {
      const query = new URLSearchParams({
        platform: scope.platform,
        shopId: scope.shopId,
        accountId: scope.accountId,
        start: scope.start,
        end: scope.end,
        ...(scope.liveSessionId ? { liveSessionId: scope.liveSessionId } : {}),
      });
      const result = await this.transport<DeliveryData>(
        `workspace?${query}`,
        "GET",
        undefined,
        signal,
      );
      return scoped(validateDeliveryData(result), identity, scope);
    } catch (error) {
      if (signal.aborted) throw signal.reason;
      if (
        error instanceof ServiceError &&
        ["NO_DATA", "API_NOT_CONFIGURED"].includes(error.code)
      )
        return fallback;
      throw error;
    }
  }
  gate(
    data: DeliveryData,
    identity: Identity,
    accountId: string,
    action: Action,
  ) {
    const rule = actionAvailability(data, identity, accountId, action);
    if (rule.status !== "AUTHORIZED")
      throw new ServiceError("FORBIDDEN", rule.reason);
  }
  async preview(input: PlanChangeRequest) {
    const value = await this.transport<RiskPreview>(
      "plan-changes/preview",
      "POST",
      input,
    );
    if (
      !value ||
      !value.token ||
      !Array.isArray(value.checks) ||
      !value.checks.length ||
      !Number.isFinite(Date.parse(value.expiresAt)) ||
      !Number.isInteger(value.expectedVersion) ||
      value.expectedVersion !== input.expectedVersion
    )
      throw new ServiceError(
        "INVALID_RESPONSE",
        "预检缺少有效检查项、版本或凭据。",
      );
    return value;
  }
  async changePlan(
    input: PlanChangeRequest,
    preview: RiskPreview,
    plan: Plan,
  ): Promise<MutationResult> {
    if (
      preview.expectedVersion !== input.expectedVersion ||
      preview.checks.some((c) => !c.passed) ||
      !preview.token ||
      Date.parse(preview.expiresAt) <= Date.now()
    )
      throw new ServiceError(
        "CONFLICT",
        "风险校验未通过或已过期，请重新预检。",
      );
    return this.transport<MutationResult>("plan-changes", "POST", {
      ...input,
      previewToken: preview.token,
      platform: plan.platform,
    });
  }
  /** Existing create -> confirm -> approval endpoints retain their server checks. */
  async legacyChange(
    input: PlanChangeRequest,
    plan: Plan,
    identity: Identity,
  ): Promise<CommandJob> {
    if (
      this.service.mode !== "api" ||
      !identity.permissions.includes("task.create") ||
      plan.platform !== "adq"
    )
      throw new ServiceError("FORBIDDEN", "此账户不能创建正式操作任务。");
    const request: CreateJobRequest = {
      source: "PC_MANUAL",
      shop_id: plan.shopId,
      target_type: "AD_PLAN",
      target_id: plan.id,
      action_type: input.action,
      new_value:
        typeof input.newValue === "boolean"
          ? { kind: "BOOLEAN", value: input.newValue }
          : { kind: "MONEY", currency: "CNY", amount_minor: input.newValue },
      intent: "计划中心：经用户预览确认的投放变更申请",
      idempotency_key: input.idempotencyKey,
    };
    return this.service.createJob(request);
  }
  create(input: CreatePlanRequest) {
    return this.transport<MutationResult>(
      input.platform === "adq" ? "campaigns" : "promotion-orders",
      "POST",
      input,
    );
  }
  goal(input: GoalRequest) {
    return this.transport<MutationResult>(
      input.goalId ? `goals/${encodeURIComponent(input.goalId)}` : "goals",
      input.goalId ? "PATCH" : "POST",
      input,
    );
  }
  budget(input: BudgetRequest) {
    return this.transport<MutationResult>("budget-requests", "POST", input);
  }
  allocate(input: NewBudgetRequest) {
    return this.transport<MutationResult>("budget-requests", "POST", input);
  }
  analyze(input: AnalysisRequest) {
    return this.transport<MutationResult>("analyses", "POST", input);
  }
  review(id: string, input: DecisionReviewRequest) {
    return this.transport<MutationResult>(
      `decisions/${encodeURIComponent(id)}/reviews`,
      "POST",
      input,
    );
  }
  approval(id: string, input: ApprovalReviewRequest) {
    return this.transport<MutationResult>(
      `approvals/${encodeURIComponent(id)}/reviews`,
      "POST",
      input,
    );
  }
  policy(input: PolicyRequest) {
    return this.transport<MutationResult>("policies", "PUT", input);
  }
  verify(id: string, version: number) {
    return this.transport<MutationResult>(
      `executions/${encodeURIComponent(id)}/verify`,
      "POST",
      { expectedVersion: version },
    );
  }
  retry(id: string, version: number, idempotencyKey: string) {
    return this.transport<MutationResult>(
      `executions/${encodeURIComponent(id)}/safe-retry`,
      "POST",
      { expectedVersion: version, idempotencyKey },
    );
  }
  normalize(scope: DeliveryScope, data: DeliveryData) {
    return normalizeScope(scope, data.shops, data.accounts);
  }
}
