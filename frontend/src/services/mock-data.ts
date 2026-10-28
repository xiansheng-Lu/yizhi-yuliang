import { connectorIds, type FrontendSnapshot } from "@/lib/domain";

export function createMockSnapshot(): FrontendSnapshot {
  const at = "2026-10-21T06:35:00.000Z";
  const plans = [
    [
      "plan-qc",
      "千川A计划｜秋茶直播推广",
      "qianchuan",
      600000,
      426000,
      3412000,
    ],
    ["plan-adq", "办公室茶饮｜人群拓展", "adq", 520000, 318000, 1925000],
    ["plan-wx", "秋季礼盒｜小店推广", "wxpromo", 450000, 258000, 1641000],
    ["plan-red", "种草短视频｜聚光测试", "juguang", 400000, 243000, 1654000],
  ] as const;
  const snapshot: FrontendSnapshot = {
    schemaVersion: "command-job-v1.0",
    scope: {
      shop_id: "shop-tea",
      live_session_id: "live-tea",
      connector_id: "all",
      date: "2026-10-21",
      timezone: "Asia/Shanghai",
    },
    revision: 1,
    source: "MOCK",
    tenants: [{ id: "tenant-demo", name: "易致科技 · 演示企业" }],
    shops: [
      {
        id: "shop-tea",
        tenantId: "tenant-demo",
        name: "茶韵旗舰店",
        connectorId: "wxshop",
      },
    ],
    adAccounts: plans.map(([, , connectorId]) => ({
      id: `account-${connectorId}`,
      tenantId: "tenant-demo",
      shopId: "shop-tea",
      connectorId,
      name: "演示广告账户",
    })),
    liveSessions: [
      {
        id: "live-tea",
        tenantId: "tenant-demo",
        shopId: "shop-tea",
        name: "秋茶直播专场",
        sourceUpdatedAt: at,
        fetchedAt: at,
        online: 1284,
        productClicks: 367,
        status: "MOCK_LIVE",
      },
    ],
    plans: plans.map(
      ([id, name, connector, budgetMinor, spendMinor, paidGmvMinor]) => ({
        id,
        nativePlanId: `mock-${id}`,
        tenantId: "tenant-demo",
        shopId: "shop-tea",
        adAccountId: `account-${connector}`,
        liveSessionId: "live-tea",
        name,
        aliases:
          connector === "qianchuan"
            ? ["千川A计划", "A计划", "秋茶新品"]
            : [name.split("｜")[0]],
        budgetMinor,
        spendMinor,
        paidGmvMinor,
        bidMinor: 280,
        targetRoi: 5,
        enabled: true,
        sourceUpdatedAt: at,
        attributionWindow: "Mock：当日支付归因，非真实报表",
      }),
    ),
    products: [
      {
        id: "product-tea",
        tenantId: "tenant-demo",
        shopId: "shop-tea",
        skuId: "sku-tea-01",
        name: "秋茶礼盒",
        priceMinor: 29900,
        stock: 452,
        clicks: 225,
        paidOrders: 31,
        speechAdvice: "先解释克重、冲泡方式与优惠条件，再展示包装。",
      },
      {
        id: "product-gift",
        tenantId: "tenant-demo",
        shopId: "shop-tea",
        skuId: "sku-tea-02",
        name: "节日礼盒套装",
        priceMinor: 49900,
        stock: 96,
        clicks: 34,
        paidOrders: 6,
        speechAdvice: "说明组合规格、送礼场景与售后保障。",
      },
    ],
    materials: [
      {
        id: "material-1",
        tenantId: "tenant-demo",
        productId: "product-tea",
        name: "办公室冲泡场景",
        version: "V3",
        kind: "VIDEO",
        status: "REVIEW",
      },
      {
        id: "material-2",
        tenantId: "tenant-demo",
        productId: "product-tea",
        name: "秋茶礼盒讲解脚本",
        version: "V1",
        kind: "SCRIPT",
        status: "DRAFT",
      },
    ],
    tasks: [],
    riskPolicies: [
      {
        id: "policy-demo",
        tenantId: "tenant-demo",
        dailyBudgetMinor: 2000000,
        targetRoi: 5,
        maxChangePercent: 50,
        cooldownMinutes: 5,
        mode: "COPILOT",
        version: 1,
      },
    ],
    replaySegments: [
      {
        id: "segment-1",
        liveSessionId: "live-tea",
        productId: "product-tea",
        startSeconds: 1090,
        endSeconds: 1640,
        transcript: "秋茶礼盒讲解片段（模拟文本，尚无录像）",
        advice: "补充规格、包装和真实冲泡展示。",
        videoUrl: null,
      },
      {
        id: "segment-2",
        liveSessionId: "live-tea",
        productId: "product-gift",
        startSeconds: 2160,
        endSeconds: 2530,
        transcript: "福利转化片段（模拟文本）",
        advice: "明确优惠门槛与库存，保留观察窗口。",
        videoUrl: null,
      },
    ],
    replayReports: [
      {
        id: "report-demo",
        liveSessionId: "live-tea",
        source: "MOCK",
        sections: [
          "直播概览",
          "回放诊断",
          "货盘诊断",
          "主播话术",
          "投流复盘",
          "下一场方案",
        ].map((title) => ({
          title,
          evidenceIds: ["segment-1"],
          text: "演示报告结构，真实录像、广告回读、订单与退款尚待后端接入。",
        })),
      },
    ],
    connectors: connectorIds.map((id) => ({
      id,
      status: "UNKNOWN",
      scopes: [],
      checkedAt: null,
    })),
  };
  snapshot.shops.push({
    id: "shop-mountain",
    tenantId: "tenant-demo",
    name: "山海好物",
    connectorId: "douyinshop",
  });
  snapshot.adAccounts.push({
    id: "account-qianchuan-mountain",
    tenantId: "tenant-demo",
    shopId: "shop-mountain",
    connectorId: "qianchuan",
    name: "山海好物千川账户",
  });
  snapshot.adAccounts.forEach((account) => {
    account.name =
      account.id === "account-qianchuan-mountain"
        ? "山海好物千川账户"
        : account.id === "account-qianchuan"
          ? "茶韵千川账户"
          : account.id === "account-adq"
            ? "茶韵ADQ账户"
            : account.id === "account-wxpromo"
              ? "茶韵微信小店推广账户"
              : "茶韵聚光账户";
  });
  snapshot.plans.push({
    ...snapshot.plans[0],
    id: "plan-qc-b",
    nativePlanId: "mock-plan-qc-b",
    name: "千川B计划｜素材观察",
    aliases: ["千川B计划", "B计划"],
    budgetMinor: 30000,
    spendMinor: 12000,
    paidGmvMinor: 72000,
  });
  snapshot.liveSessions.push({
    ...snapshot.liveSessions[0],
    id: "live-mountain",
    shopId: "shop-mountain",
    name: "山海好物晚间专场",
    online: 326,
    productClicks: 87,
  });
  snapshot.plans.push({
    ...snapshot.plans[0],
    id: "plan-mountain-a",
    nativePlanId: "mock-plan-mountain-a",
    name: "千川A计划｜山海专场",
    aliases: ["千川A计划", "A计划"],
    shopId: "shop-mountain",
    liveSessionId: "live-mountain",
    adAccountId: "account-qianchuan-mountain",
    budgetMinor: 100000,
    spendMinor: 18000,
    paidGmvMinor: 126000,
  });
  snapshot.products.push({
    ...snapshot.products[0],
    id: "product-mountain",
    shopId: "shop-mountain",
    skuId: "sku-mountain",
    name: "山海礼盒",
    stock: 83,
  });
  snapshot.plans.find((plan) => plan.id === "plan-adq")!.budgetMinor = 420000;
  return snapshot;
}
