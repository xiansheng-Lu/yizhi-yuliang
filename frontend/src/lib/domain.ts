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
  status: "MOCK_LIVE" | "UNKNOWN" | "NOT_STARTED" | "LIVE" | "ENDED";
  /** Optional read-only fields; absent until supplied by the backend. */
  connectorId?: ConnectorId;
  coverUrl?: string | null;
  videoUrl?: string | null;
  startedAt?: string | null;
  endedAt?: string | null;
  totalViewers?: number | null;
  paidOrders?: number | null;
}
export type NativePlanLevel = "CAMPAIGN" | "ADGROUP" | "AD";

export interface AdPlan {
  id: string;
  nativePlanId: string;
  /** 平台原生层级标识，区分 CAMPAIGN / ADGROUP / AD；一个 Campaign 可能挂多个 AdGroup，不能混成一个"计划"。 */
  nativeLevel: NativePlanLevel | null;
  /** 父级原生 ID；ADGROUP 指向所属 Campaign，AD 指向所属 AdGroup；Campaign 本身为 null。 */
  parentNativeId?: string | null;
  tenantId: string;
  shopId: string;
  adAccountId: string;
  liveSessionId?: string;
  name: string;
  aliases?: string[];
  /** 没有明确来源的字段允许 null 并附字段级原因；真实 0 仍为 0，不跨层级借值。 */
  budgetMinor: number | null;
  bidMinor: number | null;
  targetRoi: number | null;
  /** 无独立状态映射时保持 null 并显示"待同步"，null 不自动当"已暂停"。 */
  enabled: boolean | null;
  spendMinor: number | null;
  paidGmvMinor: number | null;
  /** 未提供的真实源字段允许 null 并显示缺失，不使用抓取时间或统计日期冒充。 */
  sourceUpdatedAt: string | null;
  /** 未提供时允许 null，不混用不同归因口径。 */
  attributionWindow: string | null;
  /** 字段级原因说明：为什么该字段为 null（例如"CAMPAIGN SDK 无出价字段"）。 */
  fieldReasons?: Partial<Record<
    | "budgetMinor"
    | "bidMinor"
    | "targetRoi"
    | "enabled"
    | "sourceUpdatedAt"
    | "attributionWindow",
    string
  >>;
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
