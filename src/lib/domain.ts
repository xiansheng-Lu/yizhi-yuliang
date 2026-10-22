import type { CommandJob, ReportingScope } from "./command-job";
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
  aliases?: string[];
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
export interface RiskPolicy {
  id: string;
  tenantId: string;
  dailyBudgetMinor: number;
  targetRoi: number;
  maxChangePercent: number;
  cooldownMinutes: number;
  mode: "MANUAL" | "COPILOT" | "AUTOPILOT";
  version: number;
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
  schemaVersion: "command-job-v1.0";
  revision: number;
  source: "MOCK" | "API";
  tenants: Tenant[];
  shops: Shop[];
  adAccounts: AdAccount[];
  liveSessions: LiveSession[];
  plans: AdPlan[];
  products: Product[];
  materials: Material[];
  tasks: CommandJob[];
  scope: ReportingScope;
  riskPolicies: RiskPolicy[];
  replaySegments: ReplaySegment[];
  replayReports: ReplayReport[];
  connectors: Connector[];
}
export interface Identity {
  userId: string;
  displayName: string;
  email?: string;
  workspacePages?: string[];
  tenantId: string;
  shopIds: string[];
  permissions: string[];
  mode: "MOCK" | "API";
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
