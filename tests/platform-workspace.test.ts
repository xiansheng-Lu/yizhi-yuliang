import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import {
  advertisingNavigationParams,
  configurationSteps,
  newAdvertisingDraft,
  submissionBlock,
} from "../src/lib/advertising";
import {
  catalogEvidence,
  hasCreationEvidence,
  integrationScopeKey,
  platformApiCatalog,
  platformApiSources,
  productCatalog,
  providerOf,
  queryBlock,
  resourcesForPage,
  verificationStageNames,
  type IntegrationContext,
  type IntegrationScope,
  type OperationEvidence,
  type PlatformResourcePage,
  type ProductLine,
} from "../src/lib/platform-catalog";
import {
  PlatformService,
  type PlatformChange,
} from "../src/services/platform-service";
import { ApiClient } from "../src/services/api-client";
import type { Identity } from "../src/lib/domain";
const actor: Identity = {
  mode: "API",
  tenantId: "test-tenant",
  userId: "test-member",
  shopIds: ["test-shop"],
  displayName: "契约测试",
  permissions: ["data.read", "delivery.manage"],
};
const scope = (product: ProductLine = "wxshop"): IntegrationScope => ({
  tenantId: actor.tenantId,
  businessEntityId: "test-entity",
  shopId: "test-shop",
  provider: providerOf(product),
  productLine: product,
  accountId: product === "wxshop" ? "" : "test-account",
  start: "2026-10-23",
  end: "2026-10-23",
});
const proofs = (): OperationEvidence =>
  Object.fromEntries(
    Object.keys(verificationStageNames).map((k) => [
      k,
      {
        verified: true,
        proofId: "unit-test-proof",
        checkedAt: new Date().toISOString(),
        reason: "自动测试专用授权证明",
      },
    ]),
  ) as OperationEvidence;
function context(product: ProductLine = "wxshop"): IntegrationContext {
  const record = productCatalog(product).find(
    (r) =>
      r.normalizedPath ===
      (product === "wxshop"
        ? "/channels/ec/order/get"
        : "/api/open/jg/campaign/list"),
  )!;
  return {
    contractVersion: "platform-workspace-v9.0",
    source: "API",
    tenantId: actor.tenantId,
    productLine: product,
    checkedAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + 300000).toISOString(),
    entities: [{ id: "test-entity", name: "测试主体" }],
    shops: [
      {
        id: "test-shop",
        name: "测试店铺",
        businessEntityId: "test-entity",
        provider: "wxshop",
      },
    ],
    accounts:
      product === "wxshop"
        ? []
        : [
            {
              id: "test-account",
              nativeId: "native-test-account",
              name: "测试账户",
              businessEntityId: "test-entity",
              provider: providerOf(product),
              productLine: product,
              authorization: "AUTHORIZED",
            },
          ],
    links:
      product === "wxshop"
        ? []
        : [
            {
              accountId: "test-account",
              shopId: "test-shop",
              state: "AUTHORIZED",
              checkedAt: new Date().toISOString(),
            },
          ],
    operations: [
      {
        catalogId: record.id,
        resource: product === "wxshop" ? "orders" : "campaigns",
        kind: "READ",
        accountIds: product === "wxshop" ? [] : ["test-account"],
        evidence: proofs(),
        permission: "data.read",
        editableFields: [],
      },
    ],
  };
}
const envelope = (data: unknown) =>
  new Response(
    JSON.stringify({
      schemaVersion: "command-job-v1.0",
      requestId: "test-request",
      data,
    }),
    { headers: { "Content-Type": "application/json" } },
  );
function page(s = scope()): PlatformResourcePage {
  return {
    contractVersion: "platform-workspace-v9.0",
    source: "API",
    scopeKey: integrationScopeKey(s),
    resource: s.productLine === "wxshop" ? "orders" : "campaigns",
    rows: [
      {
        scopeKey: integrationScopeKey(s),
        id: "test-order",
        nativeId: "native-test-order",
        name: "测试订单摘要",
        status: "PAID",
        version: 1,
        updatedAt: new Date().toISOString(),
        metrics: { paidGmvMinor: 12345, refundMinor: 0 },
        attributionWindow: null,
        moneyType: null,
        related: [],
      },
    ],
    nextCursor: null,
    syncedAt: new Date().toISOString(),
    timezone: "Asia/Shanghai",
    granularity: "DAY",
    attributionWindow: null,
    refundHandling: "单列退款，不冲减支付口径",
    deduplication: "未跨广告渠道去重",
    warnings: [],
  };
}

test("all 1818 normalized references are retained with raw-source hashes", () => {
  assert.equal(platformApiCatalog.length, 1818);
  assert.equal(new Set(platformApiCatalog.map((r) => r.id)).size, 1818);
  for (const source of platformApiSources) {
    const bytes = readFileSync(`docs/shared/reference/platform-api-reference/${source.file}`);
    assert.equal(
        createHash("sha256").update(bytes.toString("utf8").replace(/\r\n/g,"\n")).digest("hex"),
      source.sha256,
    );
    assert.equal(
      platformApiCatalog.filter((r) => r.sourceFile === source.file).length,
      source.recordCount,
    );
  }
  assert.equal(
    platformApiCatalog.filter((r) => r.pathEvidence === "SOURCE_VERIFIED")
      .length,
    43,
  );
  assert.equal(platformApiCatalog.filter((r) => !r.normalizedPath).length, 50);
});
test("catalogue does not infer HTTP methods or account grants", () => {
  assert.equal(
    platformApiCatalog.filter((r) => r.method === null).length,
    1530,
  );
  const record = productCatalog("juguang").find(
    (r) => r.pathEvidence === "SOURCE_VERIFIED",
  )!;
  const evidence = catalogEvidence(record);
  assert.equal(evidence.path.verified, true);
  for (const stage of [
    "account",
    "backend",
    "execution",
    "applicationScope",
    "requestSchema",
  ] as const)
    assert.equal(evidence[stage].verified, false);
});
test("Ocean Marketing, DOU+, local push and Qianchuan have separate namespaces", () => {
  assert.ok(
    productCatalog("dou_plus").every((r) =>
      r.nativePath.startsWith("/douplus/"),
    ),
  );
  assert.ok(
    productCatalog("local_push").every((r) =>
      r.nativePath.startsWith("/local/"),
    ),
  );
  assert.ok(
    productCatalog("ocean_marketing").every(
      (r) => !/^\/(local|douplus)\//.test(r.nativePath),
    ),
  );
  assert.equal(productCatalog("qianchuan").length, 0);
  assert.ok(resourcesForPage("plans", "dou_plus").includes("promotionOrders"));
  assert.ok(!resourcesForPage("plans", "dou_plus").includes("campaigns"));
  assert.notDeepEqual(
    configurationSteps("dou_plus", "DOU_PLUS"),
    configurationSteps("ocean_marketing", "PROJECT_PROMOTION"),
  );
});
test("merchant settlement and beans interfaces cannot become ADQ creation evidence", () => {
  const merchant = productCatalog("wxshop").find((r) =>
    r.nativePath.includes("getbalance"),
  )!;
  assert.equal(hasCreationEvidence("adq", [merchant.id]), false);
  assert.equal(productCatalog("beans").length, 0);
  assert.match(
    submissionBlock(
      newAdvertisingDraft("beans"),
      null,
      "API",
      actor.permissions,
      true,
    ),
    /尚未核验/,
  );
  assert.equal(
    hasCreationEvidence(
      "juguang",
      productCatalog("juguang").map((r) => r.id),
    ),
    false,
  );
});
test("five-workbench navigation retains product and real scope, never creation view", () => {
  assert.deepEqual(
    advertisingNavigationParams(
      new URLSearchParams(
        "product=dou_plus&entityId=e&productShopId=s&productAccountId=a&view=create&secret=token",
      ),
    ),
    {
      product: "dou_plus",
      entityId: "e",
      productShopId: "s",
      productAccountId: "a",
    },
  );
  assert.deepEqual(
    advertisingNavigationParams(
      new URLSearchParams("product=unknown&productAccountId=a"),
    ),
    {},
  );
});
test("unbound or unauthorized accounts do not unlock data reads", () => {
  const c = context("juguang"),
    s = scope("juguang");
  assert.equal(queryBlock(c, s, "campaigns", actor.permissions), "");
  c.links[0].state = "BOUND";
  assert.match(queryBlock(c, s, "campaigns", actor.permissions), /授权关联/);
  c.links[0].state = "AUTHORIZED";
  c.accounts[0].authorization = "UNAUTHORIZED";
  assert.match(queryBlock(c, s, "campaigns", actor.permissions), /授权关联/);
});
test("read requires actual scope, permission and backend proofs, but not prior write execution", () => {
  const c = context(),
    s = scope();
  assert.equal(queryBlock(c, s, "orders", actor.permissions), "");
  c.operations[0].evidence.execution.verified = false;
  assert.equal(queryBlock(c, s, "orders", actor.permissions), "");
  c.operations[0].evidence.backend.verified = false;
  assert.match(queryBlock(c, s, "orders", actor.permissions), /核验/);
  assert.match(
    queryBlock(
      context(),
      { ...s, tenantId: "other" },
      "orders",
      actor.permissions,
    ),
    /范围/,
  );
  assert.match(queryBlock(context(), s, "orders", []), /权限/);
});
test("integration endpoint absence never falls back to legacy sample accounts", async () => {
  const api = new PlatformService(
    new ApiClient(
      "https://test.invalid",
      async () => new Response(null, { status: 404 }),
    ),
    actor,
  );
  await assert.rejects(api.context("juguang"), { code: "NO_DATA" });
});
test("context rejects capabilities borrowed from another product line", async () => {
  const c = context("juguang");
  c.operations[0].catalogId = productCatalog("adq")[0].id;
  const api = new PlatformService(
    new ApiClient("https://test.invalid", async () => envelope(c)),
    actor,
  );
  await assert.rejects(api.context("juguang"), { code: "INVALID_RESPONSE" });
});
test("scope keys include business entity, shop, product, account and dates", () => {
  const s = scope("juguang");
  for (const change of [
    { businessEntityId: "different" },
    { shopId: "different" },
    { accountId: "different" },
    { start: "2026-10-22" },
  ])
    assert.notEqual(
      integrationScopeKey({ ...s, ...change }),
      integrationScopeKey(s),
    );
});
test("resource reads discard buyer PII and unknown top-level payloads", async () => {
  const data = page();
  Object.assign(data.rows[0], {
    buyerPhone: "private-test-value",
    shippingAddress: "private-test-address",
  });
  Object.assign(data, { rawOrders: ["private-test-value"] });
  const api = new PlatformService(
    new ApiClient("https://test.invalid", async () => envelope(data)),
    actor,
  );
  const result = await api.query(context(), scope(), "orders");
  assert.equal(result.rows[0].metrics.paidGmvMinor, 12345);
  assert.equal(result.rows[0].metrics.refundMinor, 0);
  assert.equal(JSON.stringify(result).includes("private-test"), false);
  assert.equal(result.attributionWindow, null);
});
test("response rows and envelope must both match the requested business scope", async () => {
  for (const alter of [
    (p: PlatformResourcePage) => {
      p.scopeKey = "foreign";
    },
    (p: PlatformResourcePage) => {
      p.rows[0].scopeKey = "foreign";
    },
  ]) {
    const data = page();
    alter(data);
    const api = new PlatformService(
      new ApiClient("https://test.invalid", async () => envelope(data)),
      actor,
    );
    await assert.rejects(api.query(context(), scope(), "orders"), {
      code: "INVALID_RESPONSE",
    });
  }
});
test("merchant data rejects advertising-wallet balances", async () => {
  const data = page();
  data.rows[0].moneyType = "AD_CASH_BALANCE";
  data.rows[0].metrics.balanceMinor = 123;
  const api = new PlatformService(
    new ApiClient("https://test.invalid", async () => envelope(data)),
    actor,
  );
  await assert.rejects(api.query(context(), scope(), "orders"), {
    code: "INVALID_RESPONSE",
  });
});
test("unverified write cannot even enter remote preflight", async () => {
  let calls = 0;
  const c = context("juguang"),
    record = productCatalog("juguang").find(
      (r) => r.nativePath === "/jg/campaign/update",
    )!;
  c.operations.push({
    ...c.operations[0],
    catalogId: record.id,
    kind: "WRITE",
    permission: "delivery.manage",
    editableFields: ["name"],
    evidence: {
      ...proofs(),
      execution: {
        verified: false,
        proofId: null,
        checkedAt: null,
        reason: "未联调",
      },
    },
  });
  const change: PlatformChange = {
    catalogId: record.id,
    targetId: "test-campaign",
    nativeId: "native-campaign",
    expectedVersion: 1,
    changes: { name: "新名称" },
    reason: "测试预览",
  };
  const api = new PlatformService(
    new ApiClient("https://test.invalid", async () => {
      calls++;
      return envelope({});
    }),
    actor,
  );
  await assert.rejects(api.preview(c, scope("juguang"), change), {
    code: "FORBIDDEN",
  });
  assert.equal(calls, 0);
});
test("platform confirmation token cannot be reused for edited changes", async () => {
  const c = context("juguang"),
    s = scope("juguang"),
    record = productCatalog("juguang").find(
      (r) => r.nativePath === "/jg/campaign/update",
    )!;
  c.operations.push({
    ...c.operations[0],
    catalogId: record.id,
    kind: "WRITE",
    permission: "delivery.manage",
    editableFields: ["name"],
  });
  const change: PlatformChange = {
    catalogId: record.id,
    targetId: "test-campaign",
    nativeId: "native-campaign",
    expectedVersion: 1,
    changes: { name: "新名称" },
    reason: "测试预览",
  };
  const api = new PlatformService(
    new ApiClient("https://test.invalid", async () =>
      envelope({
        contractVersion: "platform-workspace-v9.0",
        source: "API",
        scopeKey: integrationScopeKey(s),
        token: "test-token",
        expiresAt: new Date(Date.now() + 60000).toISOString(),
        before: { name: "原名称" },
        after: { name: "新名称" },
        checks: [{ passed: true, message: "仅用于测试" }],
        requiresApproval: true,
      }),
    ),
    actor,
  );
  const preview = await api.preview(c, s, change);
  await assert.rejects(
    api.command(
      c,
      s,
      { ...change, changes: { name: "另一个名称" } },
      preview,
      "test-key",
    ),
    { code: "FORBIDDEN" },
  );
});
test("accepted task without matched readback can never claim successful execution", async () => {
  const s = scope();
  const api = new PlatformService(
    new ApiClient("https://test.invalid", async () =>
      envelope({
        contractVersion: "platform-workspace-v9.0",
        source: "API",
        scopeKey: integrationScopeKey(s),
        idempotencyKey: "test-key",
        taskId: "test-task",
        businessId: "native-id",
        approvalId: null,
        state: "SUCCEEDED",
        verification: "UNKNOWN",
        version: 1,
        cancellable: false,
        message: "结果待核查",
      }),
    ),
    actor,
  );
  await assert.rejects(api.result(s, "test-key"), { code: "INVALID_RESPONSE" });
});
