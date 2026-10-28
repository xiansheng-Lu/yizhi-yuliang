import imported from "../data/platform-api-catalog.json";
import { advertisingProducts, type AdvertisingProduct } from "./advertising";
export type ProviderId = "tencent" | "ocean" | "xhs" | "wxshop" | "beans";
export type ProductLine = AdvertisingProduct | "wxshop";
export const providerNames: Record<ProviderId, string> = {
  tencent: "腾讯营销",
  ocean: "巨量引擎",
  xhs: "小红书聚光",
  wxshop: "微信小店经营",
  beans: "视频号微信豆",
};
export interface PlatformApiRecord {
  id: string;
  platform: string;
  domain: string;
  module: string;
  name: string;
  nativePath: string;
  normalizedPath: string | null;
  method: string | null;
  scopes: string[];
  pathEvidence: string;
  docUrl: string | null;
  sourceFile: string;
}
export const platformApiCatalog = imported.records as PlatformApiRecord[];
export const platformApiSources = imported.sources;
export function providerOf(product: ProductLine): ProviderId {
  return product === "wxshop"
    ? "wxshop"
    : advertisingProducts[product].provider;
}
export function productLineName(product: ProductLine) {
  return product === "wxshop"
    ? "微信小店经营数据中心"
    : advertisingProducts[product].name;
}
export function productCatalog(product: ProductLine): PlatformApiRecord[] {
  const provider = providerOf(product),
    records = platformApiCatalog.filter((r) => r.platform === provider);
  if (product === "beans") return [];
  if (product === "shop") return records.filter((r) => r.module === "微信小店");
  if (product === "qianchuan")
    return records.filter(
      (r) => /千川/.test(r.module) || /\/qianchuan\//.test(r.nativePath),
    );
  if (product === "dou_plus")
    return records.filter((r) => r.nativePath.startsWith("/douplus/"));
  if (product === "local_push")
    return records.filter((r) => r.nativePath.startsWith("/local/"));
  if (product === "ocean_marketing")
    return records.filter(
      (r) =>
        !r.nativePath.startsWith("/local/") &&
        !r.nativePath.startsWith("/douplus/") &&
        !/星图|云图|星川/.test(r.domain),
    );
  return records;
}
export function hasCreationEvidence(
  product: AdvertisingProduct,
  ids: string[],
): boolean {
  const paths: Partial<Record<AdvertisingProduct, string[]>> = {
    adq: ["/v3.0/adgroups/add", "/v3.0/search_adgroups/add"],
    ocean_marketing: ["/project/create/", "/promotion/create/"],
    dou_plus: ["/douplus/order/create/"],
    local_push: ["/local/project/create/", "/local/promotion/create/"],
  };
  return productCatalog(product).some(
    (r) =>
      ids.includes(r.id) && paths[product]?.includes(r.normalizedPath ?? ""),
  );
}
export const verificationStageNames = {
  directory: "目录存在",
  path: "路径已核验",
  applicationScope: "应用 scope 已授予",
  account: "账户已授权",
  backend: "后端已接入",
  requestSchema: "参数规则已核验",
  execution: "真实执行已核验",
} as const;
export type VerificationStage = keyof typeof verificationStageNames;
export const verificationPendingNames:Record<VerificationStage,string>={directory:"目录待提供",path:"路径待核验",applicationScope:"scope待授权",account:"账户待授权",backend:"后端待接入",requestSchema:"参数待核验",execution:"执行待核验"};
export interface StageEvidence {
  verified: boolean;
  proofId: string | null;
  checkedAt: string | null;
  reason: string;
}
export type OperationEvidence = Record<VerificationStage, StageEvidence>;
export function catalogEvidence(record: PlatformApiRecord): OperationEvidence {
  const pending = (reason: string): StageEvidence => ({
    verified: false,
    proofId: null,
    checkedAt: null,
    reason,
  });
  return {
    directory: {
      verified: true,
      proofId: record.sourceFile,
      checkedAt: null,
      reason: "目录已收录，不代表可调用。",
    },
    path: {
      verified: record.pathEvidence === "SOURCE_VERIFIED",
      proofId:
        record.pathEvidence === "SOURCE_VERIFIED" ? record.sourceFile : null,
      checkedAt: null,
      reason:
        record.pathEvidence === "SOURCE_VERIFIED"
          ? "原始图谱与规范化目录标记为已核验；仍需补 HTTP 方法和参数。"
          : record.normalizedPath
            ? "目录列出路径；未提供独立联调核验凭证。"
            : "请求路径尚未核验。",
    },
    applicationScope: pending("需要后端返回应用 scope 证明。"),
    account: pending("目录不包含账户授权。"),
    backend: pending("需要后端声明已联调的适配器。"),
    requestSchema: pending("接口参数、版本和约束需要账户场景核验。"),
    execution: pending("尚无此操作的真实执行及回读证明。"),
  };
}
export type PlatformResource =
  | "campaigns"
  | "promotionOrders"
  | "adgroups"
  | "creatives"
  | "targeting"
  | "keywords"
  | "reports"
  | "funds"
  | "orders"
  | "products"
  | "stock"
  | "lives"
  | "compass"
  | "settlements"
  | "aftersales"
  | "distribution"
  | "audit"
  | "decisions"
  | "goals"
  | "allocations"
  | "executions";
export const resourceNames: Record<PlatformResource, string> = {
  campaigns: "计划 / 项目",
  promotionOrders: "推广订单",
  adgroups: "单元",
  creatives: "创意与素材",
  targeting: "定向与人群",
  keywords: "关键词与否定词",
  reports: "平台广告报表",
  funds: "广告账户预算与余额",
  orders: "经营订单对账",
  products: "商品资产",
  stock: "库存",
  lives: "直播经营数据",
  compass: "罗盘经营报表",
  settlements: "店铺结算与流水",
  aftersales: "售后与退款",
  distribution: "优选联盟分销",
  audit: "审核",
  decisions: "诊断与决策",
  goals: "经营目标",
  allocations: "预算分配",
  executions: "执行与回读",
};
export const resourcesForPage = (
  page: string,
  product: ProductLine,
): PlatformResource[] =>
  product === "wxshop"
    ? [
        "products",
        "stock",
        "orders",
        "compass",
        "lives",
        "aftersales",
        "settlements",
        "distribution",
      ]
    : page === "goals"
      ? ["goals", "reports"]
      : page === "decisions"
        ? ["decisions", "reports"]
        : page === "budgets"
          ? ["funds", "allocations"]
          : page === "executions"
            ? ["executions", "audit"]
            : page === "monitor"
              ? ["lives", "reports"]
              : page === "products"
                ? ["products", "stock", "orders"]
                : product === "dou_plus" || product === "beans"
                  ? ["promotionOrders", "creatives", "targeting", "reports"]
                  : [
                      "campaigns",
                      "adgroups",
                      "creatives",
                      "targeting",
                      "keywords",
                      "audit",
                      "reports",
                    ];
export const safeMetricKeys = [
  "budgetMinor",
  "balanceMinor",
  "spendMinor",
  "attributedGmvMinor",
  "paidGmvMinor",
  "refundMinor",
  "settlementMinor",
  "stock",
  "paidOrders",
  "refundOrders",
  "impressions",
  "clicks",
  "roi",
  "beans",
  "liveViewers",
  "itemCount",
] as const;
export interface IntegrationScope {
  tenantId: string;
  businessEntityId: string;
  shopId: string;
  provider: ProviderId;
  productLine: ProductLine;
  accountId: string;
  start: string;
  end: string;
}
export function integrationScopeKey(s: IntegrationScope) {
  return JSON.stringify([
    s.tenantId,
    s.businessEntityId,
    s.shopId,
    s.provider,
    s.productLine,
    s.accountId,
    s.start,
    s.end,
  ]);
}
export interface IntegrationContext {
  contractVersion: "platform-workspace-v9.0";
  source: "API";
  tenantId: string;
  productLine: ProductLine;
  checkedAt: string;
  expiresAt: string;
  entities: { id: string; name: string }[];
  shops: {
    id: string;
    name: string;
    businessEntityId: string;
    provider: string;
  }[];
  accounts: {
    id: string;
    nativeId: string;
    name: string;
    businessEntityId: string;
    provider: ProviderId;
    productLine: ProductLine;
    authorization:
      | "AUTHORIZED"
      | "UNAUTHORIZED"
      | "FORBIDDEN"
      | "NOT_INTEGRATED";
  }[];
  links: {
    accountId: string;
    shopId: string;
    state: "BOUND" | "AUTHORIZED";
    checkedAt: string;
  }[];
  operations: {
    catalogId: string;
    resource: PlatformResource;
    kind: "READ" | "WRITE";
    accountIds: string[];
    evidence: OperationEvidence;
    permission: string;
    editableFields: ("name" | "budget" | "bid" | "status")[];
    options?: { field: string; id: string; name: string }[];
  }[];
}
export interface PlatformResourceRow {
  scopeKey: string;
  id: string;
  nativeId: string;
  name: string;
  status: string;
  version: number | null;
  updatedAt: string;
  metrics: Partial<Record<(typeof safeMetricKeys)[number], number | null>>;
  attributionWindow: string | null;
  moneyType:
    | "AD_BUDGET"
    | "AD_CASH_BALANCE"
    | "MERCHANT_SETTLEMENT"
    | "BEAN"
    | null;
  related: { type: "PRODUCT" | "LIVE" | "PLAN"; id: string }[];
}
export interface PlatformResourcePage {
  contractVersion: "platform-workspace-v9.0";
  source: "API";
  scopeKey: string;
  resource: PlatformResource;
  rows: PlatformResourceRow[];
  nextCursor: string | null;
  syncedAt: string;
  timezone: "Asia/Shanghai";
  granularity: string;
  attributionWindow: string | null;
  refundHandling: string;
  deduplication: string;
  warnings: string[];
}
export function queryBlock(
  context: IntegrationContext | null,
  scope: IntegrationScope,
  resource: PlatformResource,
  permissions: string[],
  now = Date.now(),
): string {
  if (!context) return "产品线账户与能力接口待接入。";
  if (
    scope.tenantId !== context.tenantId ||
    scope.productLine !== context.productLine
  )
    return "当前产品线或企业范围不匹配。";
  if (Date.parse(context.expiresAt) <= now)
    return "账户能力已过期，需要重新读取。";
  if (
    !scope.businessEntityId ||
    !scope.shopId ||
    (scope.productLine !== "wxshop" && !scope.accountId)
  )
    return "选择真实经营主体、店铺和匹配产品线的账户。";
  const operation = context.operations.find(
    (o) =>
      o.resource === resource &&
      o.kind === "READ" &&
      (scope.productLine === "wxshop" ||
        o.accountIds.includes(scope.accountId)),
  );
  if (
    scope.productLine !== "wxshop" &&
    (!context.accounts.some(
      (a) => a.id === scope.accountId && a.authorization === "AUTHORIZED",
    ) ||
      !context.links.some(
        (l) =>
          l.accountId === scope.accountId &&
          l.shopId === scope.shopId &&
          l.state === "AUTHORIZED",
      ))
  )
    return "账户与经营店铺尚未建立有效授权关联。";
  if (!operation) return "后端尚未声明此账户可读取该类资源。";
  if (
    ![
      operation.evidence.directory,
      operation.evidence.path,
      operation.evidence.applicationScope,
      operation.evidence.account,
      operation.evidence.backend,
      operation.evidence.requestSchema,
    ].every((e) => e.verified)
  )
    return "读取所需的路径、scope、账户授权、后端或参数核验尚未通过。";
  if (!permissions.includes(operation.permission))
    return "当前成员缺少此资源的读取权限。";
  return "";
}
