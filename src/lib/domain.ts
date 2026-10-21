export const connectorIds = [
  "adq",
  "wxpromo",
  "beans",
  "wxshop",
  "qianchuan",
  "douyinshop",
  "juguang",
  "redshop",
] as const;
export type ConnectorId = (typeof connectorIds)[number];
export const connectorNames: Record<ConnectorId, string> = {
  adq: "腾讯广告 ADQ",
  wxpromo: "微信小店推广",
  beans: "微信豆",
  wxshop: "微信小店经营",
  qianchuan: "巨量千川",
  douyinshop: "抖店经营",
  juguang: "小红书聚光",
  redshop: "小红书电商",
};
export interface Tenant {
  id: string;
  name: string;
}
export interface Shop {
  id: string;
  tenantId: string;
  name: string;
  connectorId: ConnectorId;
}
export interface AdAccount {
  id: string;
  tenantId: string;
  shopId: string;
  connectorId: ConnectorId;
  name: string;
}
export interface LiveSession {
  id: string;
  tenantId: string;
  shopId: string;
  name: string;
  sourceUpdatedAt: string;
  fetchedAt: string;
  online: number | null;
  productClicks: number | null;
  status: "MOCK_LIVE" | "UNKNOWN";
}
export interface AdPlan {
  id: string;
  nativePlanId: string;
  tenantId: string;
  shopId: string;
  adAccountId: string;
  liveSessionId?: string;
  name: string;
  budgetMinor: number;
  bidMinor: number;
  targetRoi: number;
  enabled: boolean;
  spendMinor: number | null;
  paidGmvMinor: number | null;
  sourceUpdatedAt: string;
  attributionWindow: string;
}
export interface Product {
  id: string;
  tenantId: string;
  shopId: string;
  skuId: string;
  name: string;
  priceMinor: number;
  stock: number | null;
  clicks: number | null;
  paidOrders: number | null;
  speechAdvice: string;
}
export interface Material {
  id: string;
  tenantId: string;
  productId: string;
  name: string;
  version: string;
  kind: "VIDEO" | "IMAGE" | "SCRIPT";
  status: "DRAFT" | "REVIEW" | "READY";
}
export type TaskAction =
  | { type: "AD_SET_BUDGET"; budgetMinor: number }
  | { type: "AD_SET_BID"; bidMinor: number }
  | { type: "AD_SET_ROI_TARGET"; targetRoi: number }
  | { type: "AD_PAUSE" | "AD_RESUME" }
  | { type: "REQUEST_LIVE_START" | "REQUEST_LIVE_END" };
export const taskStatuses = [
  "DRAFT",
  "NEEDS_CLARIFICATION",
  "RISK_PENDING",
  "BLOCKED",
  "AWAITING_APPROVAL",
  "APPROVED",
  "REJECTED",
  "EXPIRED",
  "QUEUED",
  "EXECUTING",
  "RECEIPT_PENDING",
  "VERIFYING",
  "VERIFIED",
  "FAILED",
  "UNKNOWN",
  "MANUAL_REQUIRED",
] as const;
export type TaskStatus = (typeof taskStatuses)[number];
export const taskStatusNames: Record<TaskStatus, string> = {
  DRAFT: "草稿",
  NEEDS_CLARIFICATION: "待明确对象",
  RISK_PENDING: "待服务端风控",
  BLOCKED: "已阻断",
  AWAITING_APPROVAL: "待审批",
  APPROVED: "已批准 · 未执行",
  REJECTED: "已驳回",
  EXPIRED: "审批已失效",
  QUEUED: "待执行",
  EXECUTING: "执行中",
  RECEIPT_PENDING: "待平台回执",
  VERIFYING: "待回读核验",
  VERIFIED: "回读已核验",
  FAILED: "失败",
  UNKNOWN: "结果未知",
  MANUAL_REQUIRED: "待人工交接",
};
export type TaskOrigin = "PC" | "MOBILE" | "AUTOPILOT";
export interface CommandTask {
  id: string;
  tenantId: string;
  shopId: string;
  liveSessionId?: string;
  adAccountId?: string;
  planId?: string;
  origin: TaskOrigin;
  action: TaskAction;
  intent: string;
  status: TaskStatus;
  createdAt: string;
  idempotencyKey: string;
  version: number;
  mode: "MOCK" | "API";
}
export interface RiskPolicy {
  id: string;
  tenantId: string;
  dailyBudgetMinor: number;
  mode: "MANUAL" | "COPILOT" | "AUTOPILOT";
}
export interface RiskCheck {
  id: string;
  taskId: string;
  status: "MOCK_NOT_EVALUATED" | "PASSED" | "BLOCKED";
  reason: string;
  policyId: string;
}
export interface Approval {
  id: string;
  taskId: string;
  actorId: string;
  decision: "APPROVE" | "REJECT";
  createdAt: string;
  taskVersion: number;
  mode: "MOCK" | "API";
}
export interface ExecutionReceipt {
  id: string;
  taskId: string;
  source: "MOCK" | "PLATFORM";
  status: "ACCEPTED" | "REJECTED" | "UNKNOWN";
  verification: "PENDING" | "VERIFIED" | "NOT_APPLIED" | "UNKNOWN";
  platformReceiptId: string | null;
  createdAt: string;
}
export interface ReplaySegment {
  id: string;
  liveSessionId: string;
  productId: string;
  startSeconds: number;
  endSeconds: number;
  transcript: string;
  advice: string;
  videoUrl: string | null;
}
export interface ReplayReport {
  id: string;
  liveSessionId: string;
  source: "MOCK" | "API";
  sections: { title: string; evidenceIds: string[]; text: string }[];
}
export interface Connector {
  id: ConnectorId;
  status: "UNKNOWN" | "READ_ONLY" | "SUPPORTED" | "UNSUPPORTED" | "DEGRADED";
  scopes: string[];
  checkedAt: string | null;
}
export interface FrontendSnapshot {
  schemaVersion: "g1a-draft-1";
  revision: number;
  source: "MOCK" | "API";
  tenants: Tenant[];
  shops: Shop[];
  adAccounts: AdAccount[];
  liveSessions: LiveSession[];
  plans: AdPlan[];
  products: Product[];
  materials: Material[];
  tasks: CommandTask[];
  riskPolicies: RiskPolicy[];
  riskChecks: RiskCheck[];
  approvals: Approval[];
  receipts: ExecutionReceipt[];
  replaySegments: ReplaySegment[];
  replayReports: ReplayReport[];
  connectors: Connector[];
}
export interface Identity {
  userId: string;
  displayName: string;
  tenantId: string;
  shopIds: string[];
  permissions: string[];
  mode: "MOCK" | "API";
}
export interface CreateTaskRequest {
  planId?: string;
  liveSessionId?: string;
  origin: "PC" | "MOBILE";
  intent: string;
  action: TaskAction;
  idempotencyKey: string;
}
export function actionName(action: TaskAction): string {
  return {
    AD_SET_BUDGET: "广告预算调整",
    AD_SET_BID: "广告出价调整",
    AD_SET_ROI_TARGET: "投放ROI目标调整",
    AD_PAUSE: "暂停广告计划",
    AD_RESUME: "恢复广告计划",
    REQUEST_LIVE_START: "直播开播人工交接",
    REQUEST_LIVE_END: "直播关播人工交接",
  }[action.type];
}
export function money(minor: number | null) {
  return minor === null
    ? "数据缺失"
    : new Intl.NumberFormat("zh-CN", {
        style: "currency",
        currency: "CNY",
        maximumFractionDigits: 2,
      }).format(minor / 100);
}
export function businessTotals(plans: AdPlan[]) {
  const complete =
    plans.length > 0 &&
    plans.every((p) => p.spendMinor !== null && p.paidGmvMinor !== null);
  const spend = complete ? plans.reduce((n, p) => n + p.spendMinor!, 0) : null;
  const gmv = complete ? plans.reduce((n, p) => n + p.paidGmvMinor!, 0) : null;
  return {
    spend,
    gmv,
    roi: spend !== null && spend > 0 && gmv !== null ? gmv / spend : null,
  };
}
