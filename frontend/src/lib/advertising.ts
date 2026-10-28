/** Configuration contracts are frontend/backend agreements, not Tencent API payloads.
 * Only the backend resolves and verifies platform IDs and account capabilities. */
export const advertisingProducts = {
  adq: {
    name: "腾讯广告 ADQ",
    short: "ADQ",
    description: "账户、版位、人群、预算与创意的完整广告配置",
    scenarios: ["ADQ"] as const,
    writeEnabled: true,
    provider: "tencent",
    flow: "ADVERTISING",
  },
  beans: {
    name: "视频号微信豆加热",
    short: "微信豆",
    description: "短视频与直播分别配置，以实际账户能力为准",
    scenarios: ["VIDEO", "LIVE"] as const,
    writeEnabled: false,
    provider: "beans",
    flow: "HEATING",
  },
  shop: {
    name: "腾讯营销（小店版）",
    short: "小店版",
    description: "独立产品适配 · 商品托管 / 直播推广 · 待接入",
    scenarios: ["PRODUCT_HOSTING", "SHOP_LIVE"] as const,
    writeEnabled: false,
    provider: "tencent",
    flow: "SMART_COMMERCE",
  },
  ocean_marketing: {
    name: "巨量营销",
    short: "巨量营销",
    description: "项目 / 单元 · 依据专属能力配置",
    scenarios: ["PROJECT_PROMOTION"] as const,
    writeEnabled: true,
    provider: "ocean",
    flow: "ADVERTISING",
  },
  qianchuan: {
    name: "巨量千川",
    short: "千川",
    description: "电商产品线 · 账户与专属接口待核验",
    scenarios: ["QIANCHUAN"] as const,
    writeEnabled: false,
    provider: "ocean",
    flow: "ADVERTISING",
  },
  dou_plus: {
    name: "DOU+",
    short: "DOU+",
    description: "视频推广订单 · 不复用项目 / 单元",
    scenarios: ["DOU_PLUS"] as const,
    writeEnabled: true,
    provider: "ocean",
    flow: "ORDER",
  },
  local_push: {
    name: "本地推",
    short: "本地推",
    description: "门店 / 商品 / 项目 / 单元独立配置",
    scenarios: ["LOCAL_PUSH"] as const,
    writeEnabled: true,
    provider: "ocean",
    flow: "ADVERTISING",
  },
  juguang: {
    name: "小红书聚光",
    short: "聚光",
    description: "标准投 / 搜索 / 简单投 · 创建路径待核验",
    scenarios: ["XHS_STANDARD", "XHS_SEARCH", "XHS_SIMPLE"] as const,
    writeEnabled: false,
    provider: "xhs",
    flow: "ADVERTISING",
  },
} as const;
export const advertisingProviderNames = {
  tencent: "腾讯营销",
  ocean: "巨量引擎",
  xhs: "小红书聚光",
  beans: "视频号微信豆加热",
} as const;
export type AdvertisingProduct = keyof typeof advertisingProducts;
export type AdvertisingScenario =
  | "ADQ"
  | "VIDEO"
  | "LIVE"
  | "PRODUCT_HOSTING"
  | "SHOP_LIVE"
  | "PROJECT_PROMOTION"
  | "QIANCHUAN"
  | "DOU_PLUS"
  | "LOCAL_PUSH"
  | "XHS_STANDARD"
  | "XHS_SEARCH"
  | "XHS_SIMPLE";
export const scenarioNames: Record<AdvertisingScenario, string> = {
  ADQ: "广告投放",
  VIDEO: "短视频加热",
  LIVE: "直播加热",
  PRODUCT_HOSTING: "商品托管",
  SHOP_LIVE: "直播推广",
  PROJECT_PROMOTION: "项目 / 单元",
  QIANCHUAN: "千川电商推广",
  DOU_PLUS: "DOU+ 视频推广订单",
  LOCAL_PUSH: "本地推项目 / 单元",
  XHS_STANDARD: "标准投",
  XHS_SEARCH: "搜索推广",
  XHS_SIMPLE: "简单投",
};
export function isAdvertisingProduct(v: unknown): v is AdvertisingProduct {
  return typeof v === "string" && Object.hasOwn(advertisingProducts, v);
}
export function advertisingNavigationParams(
  query: URLSearchParams,
): Record<string, string> {
  const product = query.get("product");
  if (product !== "wxshop" && !isAdvertisingProduct(product)) return {};
  return Object.fromEntries(
    ["product", "entityId", "productShopId", "productAccountId"].flatMap(
      (key) => (query.get(key) ? [[key, query.get(key)!]] : []),
    ),
  );
}
export const fieldStateNames = {
  PUBLIC: "公开资料支持",
  AVAILABLE: "当前账户可用",
  API_PENDING: "API支持但未接入",
  AUTH_REQUIRED: "需要授权",
  NOT_APPLICABLE: "当前场景不适用",
  UNCONFIRMED: "需要进一步确认",
} as const;
export type FieldState = keyof typeof fieldStateNames;
export const fieldNames = {
  name: "计划名称",
  account: "投放账户",
  shop: "关联微信小店",
  objective: "推广目标",
  target: "推广对象",
  placement: "广告版位",
  optimization: "优化目标",
  strategy: "投放策略",
  targetingMode: "定向方式",
  region: "地域定向",
  locationType: "地点类型",
  age: "年龄定向",
  gender: "性别定向",
  interest: "兴趣标签",
  behavior: "行为标签",
  intention: "意向标签",
  keyword: "平台关键词",
  behaviorScene: "行为场景",
  behaviorWindow: "行为时间窗",
  behaviorIntensity: "行为强度",
  customAudience: "自定义人群",
  expansion: "智能扩量",
  hardConstraints: "不可突破定向",
  template: "定向模板",
  creators: "作者粉丝",
  groups: "群聊",
  budgetType: "预算类型",
  budget: "预算",
  bidMode: "出价方式",
  bid: "出价 / 目标成本 / ROI",
  schedule: "投放时间",
  timeSlots: "每日投放时段",
  creative: "广告素材",
  title: "创意标题",
  copy: "广告文案",
  portrait: "肖像授权证明",
  duration: "加热时长",
  orderType: "直播订单类型",
  fundType: "资金类型",
  deliveryMode: "成本与放量模式",
  creativeMethod: "加热素材方式",
  productRange: "商品范围",
} as const;
export type FieldId = keyof typeof fieldNames;
export interface ConfigOption {
  id: string;
  name: string;
  available: boolean;
  reason?: string;
}
export interface FieldCapability {
  state: FieldState;
  reason: string;
  required?: boolean;
  options?: ConfigOption[];
  min?: number;
  max?: number;
  step?: number;
  precision?: number;
  unit?: "CNY_MINOR" | "BEAN" | "RATIO";
  unitLabel?: string;
  allowedMin?: number[];
  allowedMax?: number[];
  maxSelected?: number;
  specs?: string[];
}
export interface ConfigurationContext {
  product: AdvertisingProduct;
  scenario: AdvertisingScenario;
  businessEntityId: string;
  accountId: string;
  shopId: string;
  objectiveId: string;
  optimizationId: string;
  strategyId: string;
  targetIds: string[];
  placementIds: string[];
  bidModeId: string;
  orderTypeId: string;
  fundTypeId: string;
  deliveryModeId: string;
  creativeMethodId: string;
}
export interface ConfigurationCapability {
  contractVersion: "advertising-v9.0";
  source: "API";
  tenantId: string;
  contextKey: string;
  version: string;
  checkedAt: string;
  expiresAt: string;
  fields: Partial<Record<FieldId, FieldCapability>>;
  authorization: "AUTHORIZED" | "UNAUTHORIZED" | "FORBIDDEN" | "NOT_INTEGRATED";
  writeChannel: "API" | "MANUAL" | "NONE";
  writePermission: string;
  audienceManagement: "MANUAL" | "PLATFORM_SMART";
  estimateSupported: boolean;
  uploadSupported: boolean;
  currency: "CNY" | "BEAN";
  reason: string;
  apiVersion: string;
  verification: {
    catalogIds: string[];
    pathVerified: boolean;
    applicationScopeGranted: boolean;
    accountAuthorized: boolean;
    backendIntegrated: boolean;
    requestSchemaVerified: boolean;
    executionVerified: boolean;
    proofId: string;
    checkedAt: string;
  };
}
export interface ConfigurationAccount {
  id: string;
  name: string;
  businessEntityId: string;
  shopIds: string[];
  authorization: ConfigurationCapability["authorization"];
  writable: boolean;
}
export interface ConfigurationCatalog {
  contractVersion: "advertising-v9.0";
  source: "API";
  tenantId: string;
  product: AdvertisingProduct;
  accounts: ConfigurationAccount[];
  entities: { id: string; name: string }[];
  shops: { id: string; name: string; businessEntityId: string }[];
  reason: string;
}
export type LookupKind =
  | "regions"
  | "businessDistricts"
  | "customLocations"
  | "interests"
  | "behaviors"
  | "intentions"
  | "keywords"
  | "audiences"
  | "targets"
  | "creatives"
  | "platformTemplates"
  | "creators"
  | "groups"
  | "portraitCertificates";
export interface SelectedResource {
  id: string;
  platformId: string;
  name: string;
  kind?: string;
  category?: string;
  parentId?: string;
  status?: string;
  eligible?: boolean;
  reason?: string;
  sourceMaterialId?: string;
  previewUrl?: string;
  specs?: string[];
}
export interface LookupPage {
  contractVersion: "advertising-v9.0";
  source: "API";
  tenantId: string;
  contextKey: string;
  kind: LookupKind;
  items: SelectedResource[];
  nextCursor: string | null;
  reason: string;
}
export interface AudienceConfiguration {
  mode: "CUSTOM" | "SMART" | "TEMPLATE";
  locationMode: "ANY" | "REGION" | "BUSINESS" | "CUSTOM";
  locations: SelectedResource[];
  locationTypeId: string;
  age: { min: string; max: string };
  gender: "ALL" | "MALE" | "FEMALE";
  interests: SelectedResource[];
  behaviors: SelectedResource[];
  intentions: SelectedResource[];
  keywords: SelectedResource[];
  creators: SelectedResource[];
  groups: SelectedResource[];
  include: SelectedResource[];
  exclude: SelectedResource[];
  behaviorSceneId: string;
  behaviorWindowId: string;
  behaviorIntensityId: string;
  expansion: boolean;
  hardConstraints: string[];
  platformTemplate: SelectedResource | null;
}
export interface AdvertisingDraft {
  schemaVersion: "advertising-draft-v1";
  id: string;
  updatedAt: string;
  product: AdvertisingProduct;
  scenario: AdvertisingScenario;
  businessEntityId: string;
  accountId: string;
  shopId: string;
  name: string;
  objectiveId: string;
  targets: SelectedResource[];
  placementIds: string[];
  optimizationId: string;
  strategyId: string;
  audience: AudienceConfiguration;
  budget: {
    typeId: string;
    amount: string;
    bidModeId: string;
    bidValue: string;
  };
  schedule: {
    start: string;
    end: string;
    allDay: boolean;
    slots: { days: number[]; start: string; end: string }[];
  };
  creative: {
    assets: SelectedResource[];
    title: string;
    copy: string;
    portrait: SelectedResource | null;
  };
  heating: {
    durationId: string;
    orderTypeId: string;
    fundTypeId: string;
    deliveryModeId: string;
    creativeMethodId: string;
  };
  productRangeId: string;
}
export interface LocalTargetingTemplate {
  id: string;
  name: string;
  product: AdvertisingProduct;
  scenario: AdvertisingScenario;
  accountId: string;
  audience: AudienceConfiguration;
  updatedAt: string;
}
export interface SubmissionTicket {
  idempotencyKey: string;
  draftId: string;
  product: AdvertisingProduct;
  accountId: string;
  contextKey: string;
  createdAt: string;
}
export interface AdvertisingStorage {
  drafts: AdvertisingDraft[];
  templates: LocalTargetingTemplate[];
  tickets: SubmissionTicket[];
}
export const emptyAudience = (): AudienceConfiguration => ({
  mode: "CUSTOM",
  locationMode: "ANY",
  locations: [],
  locationTypeId: "",
  age: { min: "", max: "" },
  gender: "ALL",
  interests: [],
  behaviors: [],
  intentions: [],
  keywords: [],
  creators: [],
  groups: [],
  include: [],
  exclude: [],
  behaviorSceneId: "",
  behaviorWindowId: "",
  behaviorIntensityId: "",
  expansion: false,
  hardConstraints: [],
  platformTemplate: null,
});
export function newAdvertisingDraft(
  product: AdvertisingProduct,
  scenario: AdvertisingScenario = advertisingProducts[product].scenarios[0],
  id = "new",
): AdvertisingDraft {
  return {
    schemaVersion: "advertising-draft-v1",
    id,
    updatedAt: "",
    product,
    scenario,
    businessEntityId: "",
    accountId: "",
    shopId: "",
    name: "",
    objectiveId: "",
    targets: [],
    placementIds: [],
    optimizationId: "",
    strategyId: "",
    audience: emptyAudience(),
    budget: { typeId: "", amount: "", bidModeId: "", bidValue: "" },
    schedule: { start: "", end: "", allDay: true, slots: [] },
    creative: { assets: [], title: "", copy: "", portrait: null },
    heating: {
      durationId: "",
      orderTypeId: "",
      fundTypeId: "",
      deliveryModeId: "",
      creativeMethodId: "",
    },
    productRangeId: "",
  };
}
export function configurationContext(
  d: AdvertisingDraft,
): ConfigurationContext {
  return {
    product: d.product,
    scenario: d.scenario,
    businessEntityId: d.businessEntityId,
    accountId: d.accountId,
    shopId: d.shopId,
    objectiveId: d.objectiveId,
    optimizationId: d.optimizationId,
    strategyId: d.strategyId,
    targetIds: d.targets.map((r) => r.id).sort(),
    placementIds: [...d.placementIds].sort(),
    bidModeId: d.budget.bidModeId,
    ...d.heating,
  };
}
export function configurationContextKey(c: ConfigurationContext): string {
  return JSON.stringify([
    c.product,
    c.scenario,
    c.businessEntityId,
    c.accountId,
    c.shopId,
    c.objectiveId,
    c.optimizationId,
    c.strategyId,
    [...c.targetIds].sort(),
    [...c.placementIds].sort(),
    c.bidModeId,
    c.orderTypeId,
    c.fundTypeId,
    c.deliveryModeId,
    c.creativeMethodId,
  ]);
}
export const configurationSteps = (
  product: AdvertisingProduct,
  scenario: AdvertisingScenario,
) =>
  advertisingProducts[product].flow === "ADVERTISING"
    ? [
        "账户与推广对象",
        "版位与营销目标",
        "人群定向",
        "预算、出价与排期",
        "广告创意",
        "确认与提交",
      ]
    : product === "dou_plus"
      ? [
          "账户与推广视频",
          "推广目的与订单策略",
          "订单投放人群",
          "订单预算与投放时间",
          "订单素材与预览",
          "确认订单与回读",
        ]
      : product === "beans" && scenario === "LIVE"
        ? [
            "账户与直播间",
            "订单与加热策略",
            "直播投放人群",
            "资金、预算与排期",
            "加热素材",
            "配置检查与执行",
          ]
        : product === "beans"
          ? [
              "账户与加热视频",
              "加热目标与方式",
              "加热人群",
              "微信豆预算与时间",
              "视频与肖像授权",
              "预览与执行",
            ]
          : [
              "店铺与推广对象",
              "托管与优化策略",
              "系统智能匹配",
              "预算与排期",
              "素材配置",
              "确认待接入方案",
            ];

/** Public references describe draft controls only. No native ID catalogue or grant is fabricated. */
export function designField(d: AdvertisingDraft, id: FieldId): FieldCapability {
  const publicScalar: FieldId[] = [
    "name",
    "age",
    "gender",
    "region",
    "budget",
    "schedule",
    "timeSlots",
    "title",
    "copy",
    "template",
  ];
  if (!["adq", "beans", "shop"].includes(d.product))
    return {
      state: [
        "name",
        "budget",
        "schedule",
        "timeSlots",
        "title",
        "copy",
        "template",
      ].includes(id)
        ? "PUBLIC"
        : "UNCONFIRMED",
      reason:
        "本地配置意向；具体字段、枚举和请求参数需该产品线的账户能力与接口核验。",
    };
  if (
    d.product === "shop" &&
    [
      "age",
      "gender",
      "region",
      "interest",
      "behavior",
      "intention",
      "keyword",
      "customAudience",
      "targetingMode",
    ].includes(id)
  )
    return {
      state: "NOT_APPLICABLE",
      reason: "小店版适配设计由系统智能匹配人群；实际策略等待账户能力确认。",
    };
  if (
    d.product !== "adq" &&
    [
      "placement",
      "optimization",
      "strategy",
      "behavior",
      "intention",
      "keyword",
      "customAudience",
    ].includes(id)
  )
    return {
      state: "UNCONFIRMED",
      reason: "此产品不继承 ADQ 能力，需要独立确认。",
    };
  return {
    state: publicScalar.includes(id)
      ? "PUBLIC"
      : d.product === "adq" &&
          ["interest", "behavior", "intention", "customAudience"].includes(id)
        ? "API_PENDING"
        : "UNCONFIRMED",
    reason: publicScalar.includes(id)
      ? "可编辑本地意向，账户规则尚未核验，不能据此提交。"
      : "尚无此账户与场景的有效能力响应，等待接口与授权。",
  };
}
export function fieldFor(
  d: AdvertisingDraft,
  cap: ConfigurationCapability | null,
  id: FieldId,
): FieldCapability {
  const resolved =
    cap?.fields[id] ??
    (cap
      ? { state: "NOT_APPLICABLE", reason: "当前账户能力未开放此项。" }
      : designField(d, id));
  return cap &&
    cap.authorization !== "AUTHORIZED" &&
    resolved.state === "AVAILABLE"
    ? {
        ...resolved,
        state: "AUTH_REQUIRED",
        reason: "当前账户尚未授权此配置。",
      }
    : resolved;
}
export function canEditField(
  field: FieldCapability,
  localScalar = false,
): boolean {
  return (
    field.state === "AVAILABLE" || (localScalar && field.state === "PUBLIC")
  );
}
export const optionAllowed = (field: FieldCapability, id: string) =>
  field.state === "AVAILABLE" &&
  !!field.options?.some((o) => o.id === id && o.available);

/** Context changes invalidate dependents immediately; fresh effective capabilities further prune them. */
export function changeConfiguration(
  d: AdvertisingDraft,
  patch: Partial<AdvertisingDraft>,
): { draft: AdvertisingDraft; cleared: boolean } {
  let next = { ...d, ...patch };
  let cleared = false;
  if (
    (patch.businessEntityId !== undefined &&
      patch.businessEntityId !== d.businessEntityId) ||
    (patch.accountId !== undefined && patch.accountId !== d.accountId) ||
    (patch.shopId !== undefined && patch.shopId !== d.shopId)
  ) {
    const blank = newAdvertisingDraft(d.product, d.scenario, d.id);
    next = {
      ...blank,
      name: d.name,
      businessEntityId: next.businessEntityId,
      accountId: patch.businessEntityId !== undefined ? "" : next.accountId,
      shopId:
        patch.accountId !== undefined || patch.businessEntityId !== undefined
          ? ""
          : next.shopId,
      updatedAt: d.updatedAt,
    };
    cleared = true;
  } else if (
    (patch.objectiveId !== undefined && patch.objectiveId !== d.objectiveId) ||
    (patch.placementIds &&
      JSON.stringify(patch.placementIds) !== JSON.stringify(d.placementIds))
  ) {
    next = {
      ...next,
      targets: patch.objectiveId !== undefined ? [] : next.targets,
      optimizationId: "",
      strategyId: "",
      audience: emptyAudience(),
      budget: { ...d.budget, bidModeId: "", bidValue: "" },
      creative: { ...d.creative, assets: [] },
    };
    cleared = true;
  } else if (
    (patch.budget && patch.budget.bidModeId !== d.budget.bidModeId) ||
    (patch.heating &&
      ["orderTypeId", "fundTypeId", "deliveryModeId", "creativeMethodId"].some(
        (key) =>
          patch.heating![key as keyof typeof d.heating] !==
          d.heating[key as keyof typeof d.heating],
      ))
  ) {
    next = {
      ...next,
      audience: emptyAudience(),
      budget: { ...next.budget, bidValue: "" },
      creative: { ...next.creative, assets: [] },
    };
    if (patch.heating && patch.heating.fundTypeId !== d.heating.fundTypeId)
      next.budget.amount = "";
    cleared = true;
  }
  return { draft: next, cleared };
}
export function reconcileConfiguration(
  d: AdvertisingDraft,
  cap: ConfigurationCapability,
): AdvertisingDraft {
  const next = structuredClone(d);
  const allowed = (id: FieldId) => cap.fields[id]?.state === "AVAILABLE";
  const select = (id: FieldId, value: string) =>
    optionAllowed(
      cap.fields[id] ?? { state: "NOT_APPLICABLE", reason: "" },
      value,
    )
      ? value
      : "";
  next.optimizationId = select("optimization", next.optimizationId);
  next.strategyId = select("strategy", next.strategyId);
  next.budget.typeId = select("budgetType", next.budget.typeId);
  if (!allowed("bid")) next.budget.bidValue = "";
  if (!allowed("creative")) next.creative.assets = [];
  if (!allowed("title")) next.creative.title = "";
  if (!allowed("copy")) next.creative.copy = "";
  if (!allowed("portrait")) next.creative.portrait = null;
  if (!allowed("duration")) next.heating.durationId = "";
  next.productRangeId = select("productRange", next.productRangeId);
  if (cap.audienceManagement === "PLATFORM_SMART")
    next.audience = { ...emptyAudience(), mode: "SMART" };
  else {
    const a = next.audience;
    if (!allowed("age")) a.age = { min: "", max: "" };
    if (!allowed("gender")) a.gender = "ALL";
    if (!allowed("region")) {
      a.locations = [];
      a.locationMode = "ANY";
    }
    if (!allowed("locationType")) a.locationTypeId = "";
    const mappings = {
      interest: "interests",
      behavior: "behaviors",
      intention: "intentions",
      keyword: "keywords",
      customAudience: "include",
      creators: "creators",
      groups: "groups",
    } as const;
    for (const [field, property] of Object.entries(mappings))
      if (!allowed(field as FieldId)) a[property] = [];
    if (!allowed("customAudience")) a.exclude = [];
    if (!allowed("expansion")) a.expansion = false;
    if (!allowed("hardConstraints")) a.hardConstraints = [];
    if (!allowed("template")) a.platformTemplate = null;
  }
  return next;
}

export interface ConfigurationIssue {
  step: number;
  field: string;
  message: string;
}
export function decimalUnits(
  value: string,
  unit: FieldCapability["unit"],
): number | null {
  if (!/^(0|[1-9]\d*)(\.\d+)?$/.test(value)) return null;
  const precision = unit === "CNY_MINOR" ? 2 : unit === "BEAN" ? 0 : 4;
  if ((value.split(".")[1]?.length ?? 0) > precision) return null;
  const scaled =
    unit === "CNY_MINOR"
      ? BigInt(value.split(".")[0]) * 100n +
        BigInt((value.split(".")[1] ?? "").padEnd(2, "0"))
      : null;
  const number = scaled === null ? Number(value) : Number(scaled);
  return Number.isFinite(number) &&
    number > 0 &&
    number <= Number.MAX_SAFE_INTEGER
    ? number
    : null;
}
export function validateConfiguration(
  d: AdvertisingDraft,
  cap: ConfigurationCapability | null,
): ConfigurationIssue[] {
  const issues: ConfigurationIssue[] = [];
  const add = (step: number, field: string, message: string) =>
    issues.push({ step, field, message });
  if (!d.name.trim() || d.name.length > 100)
    add(0, "name", "填写 1–100 字的计划名称，平台字节限制由账户规则再次校验。");
  if (!d.accountId) add(0, "account", "请选择已关联的真实账户。");
  if (!d.businessEntityId) add(0, "entity", "请选择真实经营主体。");
  if (!d.shopId && (!cap || cap.fields.shop?.required))
    add(0, "shop", "请选择账户关联的店铺。");
  if (!d.objectiveId) add(0, "objective", "请选择账户支持的推广目标。");
  if (!d.targets.length) add(0, "target", "选择实际授权的推广对象。");
  const a = d.audience;
  if (a.mode === "TEMPLATE" && !a.platformTemplate)
    add(2, "template", "请选择平台定向包，或复用本地模板后使用自定义定向。");
  if (a.locationMode !== "ANY" && !a.locations.length)
    add(
      2,
      "region",
      "选择有效的平台地域、商圈或位置，不能使用中文名称代替 ID。",
    );
  if ((a.age.min === "") !== (a.age.max === ""))
    add(2, "age", "年龄上下限需同时填写，或同时留空表示不限。");
  if (a.age.min || a.age.max) {
    const f = fieldFor(d, cap, "age"),
      min = Number(a.age.min),
      max = Number(a.age.max);
    if (
      !/^\d+$/.test(a.age.min) ||
      !/^\d+$/.test(a.age.max) ||
      min > max ||
      min < (f.min ?? 0) ||
      max > (f.max ?? 120) ||
      (f.allowedMin && !f.allowedMin.includes(min)) ||
      (f.allowedMax && !f.allowedMax.includes(max))
    )
      add(2, "age", "年龄范围不符合当前规则；请检查上下限和允许的年龄值。");
  }
  const included = new Set(a.include.map((r) => r.id));
  if (a.exclude.some((r) => included.has(r.id)))
    add(2, "customAudience", "同一人群包不能同时包含与排除。");
  const lists = [
    a.locations,
    a.interests,
    a.behaviors,
    a.intentions,
    a.keywords,
    a.include,
    a.exclude,
    a.creators,
    a.groups,
    d.targets,
    d.creative.assets,
  ];
  if (
    lists.some((list) =>
      list.some((r) => !r.id || !r.platformId || r.eligible === false),
    )
  )
    add(
      2,
      "resources",
      "存在无合法平台 ID、已失效或未授权的资源，需要重新查询。",
    );
  if (a.behaviors.length)
    for (const [value, label] of [
      [a.behaviorSceneId, "行为场景"],
      [a.behaviorWindowId, "行为时间窗"],
      [a.behaviorIntensityId, "行为强度"],
    ])
      if (!value) add(2, "behavior", `请选择${label}。`);
  const budgetField = fieldFor(d, cap, "budget"),
    budgetUnit =
      budgetField.unit ?? (d.product === "beans" ? "BEAN" : "CNY_MINOR");
  const amount = decimalUnits(d.budget.amount, budgetUnit);
  if (
    amount === null ||
    (budgetField.min !== undefined && amount < budgetField.min) ||
    (budgetField.max !== undefined && amount > budgetField.max) ||
    (amount !== null && budgetField.step && amount % budgetField.step !== 0)
  )
    add(3, "budget", "预算需为正数，并符合资金单位、账户上下限及步长。");
  const bidField = fieldFor(d, cap, "bid");
  if (d.budget.bidValue || bidField.required) {
    const bid = decimalUnits(d.budget.bidValue, bidField.unit);
    if (
      bid === null ||
      (bidField.min !== undefined && bid < bidField.min) ||
      (bidField.max !== undefined && bid > bidField.max)
    )
      add(3, "bid", "出价、目标成本或 ROI 不符合当前方式的单位与范围。");
  }
  const start = Date.parse(d.schedule.start + "+08:00"),
    end = Date.parse(d.schedule.end + "+08:00");
  const validLocal = (text: string, time: number) =>
    Number.isFinite(time) &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(text) &&
    new Date(time + 8 * 60 * 60 * 1000).toISOString().slice(0, 16) === text;
  if (
    !validLocal(d.schedule.start, start) ||
    !validLocal(d.schedule.end, end) ||
    end <= start
  )
    add(
      3,
      "schedule",
      "填写有效的开始、结束时间，结束时间必须晚于开始时间（北京时间）。",
    );
  if (!d.schedule.allDay) {
    if (!d.schedule.slots.length)
      add(3, "timeSlots", "至少设置一个每日投放时段。");
    const occupied = new Map<number, { start: string; end: string }[]>();
    for (const slot of d.schedule.slots) {
      if (
        !slot.days.length ||
        slot.days.some((n) => !Number.isInteger(n) || n < 1 || n > 7) ||
        !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(slot.start) ||
        !(
          slot.end === "24:00" || /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(slot.end)
        ) ||
        slot.start >= slot.end ||
        slot.end > "24:00" ||
        slot.start > "23:59"
      )
        add(
          3,
          "timeSlots",
          "时段需选择星期，且开始早于结束；跨日请拆成两个时段。",
        );
      for (const day of slot.days) {
        const prior = occupied.get(day) ?? [];
        if (prior.some((p) => p.start < slot.end && slot.start < p.end))
          add(3, "timeSlots", "同一星期内的投放时段不能重叠。");
        prior.push(slot);
        occupied.set(day, prior);
      }
    }
  }
  if (cap) {
    const selections: [FieldId, string, number][] = [
      ["objective", d.objectiveId, 0],
      ["optimization", d.optimizationId, 1],
      ["strategy", d.strategyId, 1],
      ["bidMode", d.budget.bidModeId, 3],
      ["budgetType", d.budget.typeId, 3],
      ["duration", d.heating.durationId, 3],
      ["orderType", d.heating.orderTypeId, 1],
      ["fundType", d.heating.fundTypeId, 3],
      ["deliveryMode", d.heating.deliveryModeId, 1],
      ["creativeMethod", d.heating.creativeMethodId, 4],
      ["productRange", d.productRangeId, 0],
      ["locationType", a.locationTypeId, 2],
      ["behaviorScene", a.behaviorSceneId, 2],
      ["behaviorWindow", a.behaviorWindowId, 2],
      ["behaviorIntensity", a.behaviorIntensityId, 2],
    ];
    for (const [id, value, step] of selections)
      if (
        (cap.fields[id]?.required && !value) ||
        (value && !optionAllowed(fieldFor(d, cap, id), value))
      )
        add(step, id, `${fieldNames[id]}不在当前账户的可用选项中。`);
    if (
      d.placementIds.some(
        (id) => !optionAllowed(fieldFor(d, cap, "placement"), id),
      ) ||
      (cap.fields.placement?.required && !d.placementIds.length)
    )
      add(1, "placement", "请选择兼容的版位组合。");
    if (cap.fields.creative?.required && !d.creative.assets.length)
      add(4, "creative", "选择已同步且审核可用的创意素材。");
    if (cap.fields.portrait?.required && !d.creative.portrait)
      add(4, "portrait", "当前内容需要有效肖像授权证明。");
    if (cap.fields.title?.required && !d.creative.title.trim())
      add(4, "title", "填写创意标题。");
    if (cap.fields.copy?.required && !d.creative.copy.trim())
      add(4, "copy", "填写广告文案。");
    for (const [id, refs] of [
      ["interest", a.interests],
      ["behavior", a.behaviors],
      ["intention", a.intentions],
      ["keyword", a.keywords],
      ["customAudience", [...a.include, ...a.exclude]],
      ["region", a.locations],
      ["creative", d.creative.assets],
    ] as [FieldId, SelectedResource[]][]) {
      const f = fieldFor(d, cap, id);
      if (refs.length && f.state !== "AVAILABLE")
        add(
          id === "creative" ? 4 : 2,
          id,
          `${fieldNames[id]}当前不可用：${f.reason}`,
        );
      if (f.maxSelected !== undefined && refs.length > f.maxSelected)
        add(2, id, `${fieldNames[id]}超过账户允许的选择数量。`);
    }
    if (Date.now() > Date.parse(cap.expiresAt))
      add(5, "capability", "账户能力已过期，请重新读取后校验。");
    if (Number.isFinite(start) && start < Date.now())
      add(3, "schedule", "开始时间已过去，请重新选择后校验。");
  }
  return issues;
}
export function submissionBlock(
  d: AdvertisingDraft,
  cap: ConfigurationCapability | null,
  mode: string,
  permissions: string[],
  writable: boolean,
): string {
  if (!advertisingProducts[d.product].writeEnabled)
    return `${advertisingProducts[d.product].name}的专属创建接口或参数尚未核验，本版本只保存配置方案。`;
  if (mode !== "API") return "演示模式隔离真实投放，仅保存本地草稿或方案。";
  if (!cap) return "账户能力接口未接入或尚未授权，无法提交真实投放。";
  if (
    !cap.verification.catalogIds.length ||
    ![
      cap.verification.pathVerified,
      cap.verification.applicationScopeGranted,
      cap.verification.accountAuthorized,
      cap.verification.backendIntegrated,
      cap.verification.requestSchemaVerified,
      cap.verification.executionVerified,
    ].every(Boolean)
  )
    return "目录、路径、应用 scope、账户授权、后端接入、参数规则和真实执行核验尚未全部通过。";
  if (cap.contextKey !== configurationContextKey(configurationContext(d)))
    return "配置已变化，等待对应账户能力刷新。";
  if (
    !Number.isFinite(Date.parse(cap.expiresAt)) ||
    Date.now() >= Date.parse(cap.expiresAt)
  )
    return "账户能力已过期，请重新读取。";
  if (cap.authorization !== "AUTHORIZED" || !writable)
    return "账户尚未获得正式写入授权。";
  if (cap.writeChannel !== "API")
    return "当前没有正式执行通道，可保存方案并前往官方入口操作。";
  if (!cap.writePermission || !permissions.includes(cap.writePermission))
    return "当前成员缺少后端声明的写入权限。";
  return "";
}
export function storageKey(owner: {
  mode: string;
  tenantId: string;
  userId: string;
}) {
  return `yuliang:advertising:v1:${encodeURIComponent(owner.mode)}:${encodeURIComponent(owner.tenantId)}:${encodeURIComponent(owner.userId)}`;
}
export const emptyAdvertisingStorage = (): AdvertisingStorage => ({
  drafts: [],
  templates: [],
  tickets: [],
});
function safeResources(value: unknown): SelectedResource[] {
  if (!Array.isArray(value)) return [];
  return value
    .slice(0, 250)
    .filter(
      (r): r is SelectedResource =>
        !!r &&
        typeof r === "object" &&
        [r.id, r.platformId, r.name].every(
          (v) => typeof v === "string" && v.length > 0,
        ),
    )
    .map((r) => ({
      id: r.id.slice(0, 500),
      platformId: r.platformId.slice(0, 500),
      name: r.name.slice(0, 500),
      ...Object.fromEntries(
        [
          "kind",
          "category",
          "parentId",
          "status",
          "reason",
          "sourceMaterialId",
          "previewUrl",
        ]
          .filter((k) => typeof r[k as keyof SelectedResource] === "string")
          .map((k) => [
            k,
            String(r[k as keyof SelectedResource]).slice(0, 2000),
          ]),
      ),
      eligible: r.eligible === true,
      specs: Array.isArray(r.specs)
        ? r.specs.filter((s) => typeof s === "string").slice(0, 20)
        : [],
    }));
}
function safeAudience(value: AudienceConfiguration): AudienceConfiguration {
  const next = { ...value };
  if (!["CUSTOM", "SMART", "TEMPLATE"].includes(next.mode))
    next.mode = "CUSTOM";
  if (!["ANY", "REGION", "BUSINESS", "CUSTOM"].includes(next.locationMode))
    next.locationMode = "ANY";
  if (!["ALL", "MALE", "FEMALE"].includes(next.gender)) next.gender = "ALL";
  for (const key of [
    "locations",
    "interests",
    "behaviors",
    "intentions",
    "keywords",
    "include",
    "exclude",
    "creators",
    "groups",
  ] as const)
    next[key] = safeResources(next[key]);
  next.platformTemplate = safeResources([next.platformTemplate])[0] ?? null;
  next.hardConstraints = Array.isArray(next.hardConstraints)
    ? next.hardConstraints.filter((v): v is string => typeof v === "string")
    : [];
  return next;
}
/** Parse against the current shape, dropping unknown keys (including tokens/grants).
 * Restored resources remain untrusted until backend configuration validation. */
export function readAdvertisingStorage(raw: string | null): AdvertisingStorage {
  if (!raw || raw.length > 2_000_000) return emptyAdvertisingStorage();
  try {
    const value = JSON.parse(raw) as AdvertisingStorage;
    const copyShape = (base: unknown, input: unknown): unknown => {
      if (Array.isArray(base))
        return Array.isArray(input)
          ? input
              .slice(0, 250)
              .map((x) =>
                typeof x === "object" && x !== null
                  ? Object.fromEntries(
                      Object.entries(x).filter(([key]) =>
                        [
                          "id",
                          "platformId",
                          "name",
                          "kind",
                          "category",
                          "parentId",
                          "status",
                          "eligible",
                          "reason",
                          "sourceMaterialId",
                          "previewUrl",
                          "specs",
                          "days",
                          "start",
                          "end",
                        ].includes(key),
                      ),
                    )
                  : x,
              )
          : [];
      if (base === null)
        return input && typeof input === "object"
          ? copyShape({ id: "", platformId: "", name: "" }, input)
          : null;
      if (typeof base === "object")
        return Object.fromEntries(
          Object.entries(base as object).map(([key, v]) => [
            key,
            copyShape(v, (input as Record<string, unknown>)?.[key]),
          ]),
        );
      return typeof base === typeof input
        ? typeof input === "string"
          ? input.slice(0, 5000)
          : input
        : base;
    };
    const drafts = (Array.isArray(value.drafts) ? value.drafts : [])
      .slice(0, 20)
      .filter(
        (d) =>
          d?.schemaVersion === "advertising-draft-v1" &&
          typeof d.id === "string" &&
          !!d.id &&
          isAdvertisingProduct(d.product) &&
          (
            advertisingProducts[d.product].scenarios as readonly string[]
          ).includes(d.scenario),
      )
      .map(
        (d) =>
          copyShape(
            newAdvertisingDraft(d.product, d.scenario),
            d,
          ) as AdvertisingDraft,
      )
      .map((d) => ({
        ...d,
        audience: safeAudience(d.audience),
        targets: safeResources(d.targets),
        placementIds: d.placementIds.filter((v) => typeof v === "string"),
        creative: {
          ...d.creative,
          assets: safeResources(d.creative.assets),
          portrait: safeResources([d.creative.portrait])[0] ?? null,
        },
        schedule: {
          ...d.schedule,
          slots: d.schedule.slots
            .filter(
              (s) =>
                s &&
                typeof s.start === "string" &&
                typeof s.end === "string" &&
                Array.isArray(s.days),
            )
            .map((s) => ({
              start: s.start,
              end: s.end,
              days: s.days.filter(
                (n) =>
                  typeof n === "number" &&
                  Number.isInteger(n) &&
                  n >= 1 &&
                  n <= 7,
              ),
            })),
        },
      }));
    const templates = (Array.isArray(value.templates) ? value.templates : [])
      .slice(0, 30)
      .filter(
        (t) =>
          t?.id &&
          typeof t.name === "string" &&
          isAdvertisingProduct(t.product) &&
          (
            advertisingProducts[t.product].scenarios as readonly string[]
          ).includes(t.scenario),
      )
      .map((t) => ({
        id: t.id,
        name: t.name.slice(0, 100),
        product: t.product,
        scenario: t.scenario,
        accountId: String(t.accountId ?? ""),
        updatedAt: String(t.updatedAt ?? ""),
        audience: safeAudience(
          copyShape(emptyAudience(), t.audience) as AudienceConfiguration,
        ),
      }));
    const tickets = (Array.isArray(value.tickets) ? value.tickets : [])
      .slice(0, 20)
      .filter(
        (t) =>
          t &&
          isAdvertisingProduct(t.product) &&
          [
            t.idempotencyKey,
            t.draftId,
            t.accountId,
            t.contextKey,
            t.createdAt,
          ].every((v) => typeof v === "string"),
      )
      .map((t) => ({
        idempotencyKey: t.idempotencyKey,
        draftId: t.draftId,
        accountId: t.accountId,
        product: t.product,
        contextKey: t.contextKey,
        createdAt: t.createdAt,
      }));
    return { drafts, templates, tickets };
  } catch {
    return emptyAdvertisingStorage();
  }
}
