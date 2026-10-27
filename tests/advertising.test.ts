import test from "node:test";
import assert from "node:assert/strict";
import {
  advertisingProducts,
  canEditField,
  changeConfiguration,
  configurationContext,
  configurationContextKey,
  configurationSteps,
  decimalUnits,
  designField,
  emptyAudience,
  newAdvertisingDraft,
  readAdvertisingStorage,
  reconcileConfiguration,
  storageKey,
  submissionBlock,
  validateConfiguration,
  type AdvertisingDraft,
  type ConfigurationCapability,
  type FieldCapability,
  type SubmissionTicket,
} from "../src/lib/advertising";
import {
  AdvertisingService,
  type ConfigurationTask,
  type ConfigurationValidation,
} from "../src/services/advertising-service";
import { CsrfApiClient as ApiClient } from "./helpers/csrf-fetch";
import { MockAdapter } from "../src/services/mock-adapter";
import type { Identity } from "../src/lib/domain";
import { productCatalog } from "../src/lib/platform-catalog";
const actor: Identity = {
  userId: "test-member",
  tenantId: "test-tenant",
  shopIds: ["test-shop"],
  mode: "API",
  displayName: "契约测试",
  permissions: ["delivery.manage"],
};
const available = (extra: Partial<FieldCapability> = {}): FieldCapability => ({
  state: "AVAILABLE",
  reason: "仅用于自动测试的后端契约数据",
  ...extra,
});
const option = (id: string) => ({
  id,
  name: `测试选项 ${id}`,
  available: true,
});
const resource = (id: string) => ({
  id,
  platformId: `native-test-${id}`,
  name: `测试资源 ${id}`,
  eligible: true,
});
function draft(): AdvertisingDraft {
  const d = newAdvertisingDraft("adq", "ADQ", "test-draft");
  Object.assign(d, {
    accountId: "test-account",
    businessEntityId: "test-entity",
    shopId: "test-shop",
    name: "配置契约测试",
    objectiveId: "test-objective",
    targets: [resource("target")],
    placementIds: ["test-placement"],
  });
  d.budget = {
    typeId: "test-daily",
    amount: "100.25",
    bidModeId: "test-bid",
    bidValue: "2.00",
  };
  d.schedule = {
    start: "2099-01-01T09:00",
    end: "2099-01-02T19:00",
    allDay: true,
    slots: [],
  };
  d.creative.assets = [resource("creative")];
  return d;
}
function capability(d = draft()): ConfigurationCapability {
  return {
    contractVersion: "advertising-v9.0",
    source: "API",
    tenantId: actor.tenantId,
    contextKey: configurationContextKey(configurationContext(d)),
    version: "test-version",
    checkedAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + 300000).toISOString(),
    authorization: "AUTHORIZED",
    writeChannel: "API",
    writePermission: "delivery.manage",
    audienceManagement: "MANUAL",
    estimateSupported: false,
    uploadSupported: false,
    currency: "CNY",
    reason: "自动测试专用",
    apiVersion: "3.0",
    verification: {
      catalogIds: [
        productCatalog("adq").find(
          (r) => r.normalizedPath === "/v3.0/adgroups/add",
        )!.id,
      ],
      pathVerified: true,
      applicationScopeGranted: true,
      accountAuthorized: true,
      backendIntegrated: true,
      requestSchemaVerified: true,
      executionVerified: true,
      proofId: "test-proof",
      checkedAt: new Date().toISOString(),
    },
    fields: {
      name: available(),
      objective: available({
        required: true,
        options: [option("test-objective")],
      }),
      target: available(),
      placement: available({
        required: true,
        options: [option("test-placement")],
      }),
      budget: available({
        required: true,
        unit: "CNY_MINOR",
        min: 100,
        max: 1000000,
        step: 1,
      }),
      budgetType: available({
        required: true,
        options: [option("test-daily")],
      }),
      bidMode: available({ required: true, options: [option("test-bid")] }),
      bid: available({
        required: true,
        unit: "CNY_MINOR",
        min: 10,
        max: 10000,
      }),
      age: available({
        min: 14,
        max: 66,
        allowedMin: [14, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30],
        allowedMax: [18, 30, 45, 66],
      }),
      gender: available(),
      targetingMode: available({
        options: [option("CUSTOM"), option("SMART"), option("TEMPLATE")],
      }),
      region: available({ options: [option("REGION")] }),
      creative: available({ required: true }),
      schedule: available(),
      timeSlots: available(),
      interest: available(),
      customAudience: available({ maxSelected: 200 }),
    },
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
const metadata = (d: AdvertisingDraft) => ({
  contractVersion: "advertising-v9.0",
  source: "API",
  tenantId: actor.tenantId,
  contextKey: configurationContextKey(configurationContext(d)),
});
const validation = (d: AdvertisingDraft): ConfigurationValidation => ({
  ...metadata(d),
  contractVersion: "advertising-v9.0",
  source: "API",
  capabilityVersion: "test-version",
  validationToken: "test-token",
  expiresAt: new Date(Date.now() + 60000).toISOString(),
  requiresApproval: false,
  checks: [
    {
      field: "configuration",
      passed: true,
      severity: "ERROR",
      message: "测试校验通过",
    },
  ],
});
const ticket = (d: AdvertisingDraft): SubmissionTicket => ({
  idempotencyKey: "test-idempotency",
  draftId: d.id,
  accountId: d.accountId,
  product: d.product,
  contextKey: configurationContextKey(configurationContext(d)),
  createdAt: new Date().toISOString(),
});

test("all configured products have independent scenarios and workflows", () => {
  assert.deepEqual(Object.keys(advertisingProducts), [
    "adq",
    "beans",
    "shop",
    "ocean_marketing",
    "qianchuan",
    "dou_plus",
    "local_push",
    "juguang",
  ]);
  assert.equal(configurationSteps("adq", "ADQ")[2], "人群定向");
  assert.notDeepEqual(
    configurationSteps("beans", "VIDEO"),
    configurationSteps("beans", "LIVE"),
  );
  assert.equal(advertisingProducts.shop.writeEnabled, false);
});
test("unbound accounts can edit public draft controls but never get grants", () => {
  const d = newAdvertisingDraft("adq");
  assert.equal(canEditField(designField(d, "age"), true), true);
  assert.equal(canEditField(designField(d, "interest")), false);
  assert.match(
    submissionBlock(d, null, "API", actor.permissions, false),
    /未接入|授权/,
  );
  assert.deepEqual(d.audience.locations, []);
});
test("context binds objective, placements, bid, live strategy and target objects", () => {
  const d = draft(),
    base = configurationContextKey(configurationContext(d));
  for (const change of [
    { objectiveId: "other" },
    { optimizationId: "other" },
    { targets: [resource("different")] },
  ])
    assert.notEqual(
      configurationContextKey(configurationContext({ ...d, ...change })),
      base,
    );
  assert.notEqual(
    configurationContextKey(
      configurationContext({
        ...d,
        heating: { ...d.heating, orderTypeId: "global" },
      }),
    ),
    base,
  );
});
test("account and objective switches clear incompatible platform resources", () => {
  const d = draft();
  d.audience.include = [resource("audience")];
  const changed = changeConfiguration(d, { accountId: "other" }).draft;
  assert.equal(changed.shopId, "");
  assert.deepEqual(changed.targets, []);
  assert.deepEqual(changed.creative.assets, []);
  assert.equal(changed.name, d.name);
  const objective = changeConfiguration(d, { objectiveId: "other" }).draft;
  assert.deepEqual(objective.targets, []);
  assert.deepEqual(objective.audience, emptyAudience());
  assert.equal(objective.budget.bidValue, "");
});
test("duration-only edits preserve targeting, order changes invalidate it", () => {
  const d = draft();
  d.audience.gender = "FEMALE";
  assert.equal(
    changeConfiguration(d, { heating: { ...d.heating, durationId: "2h" } })
      .draft.audience.gender,
    "FEMALE",
  );
  assert.equal(
    changeConfiguration(d, { heating: { ...d.heating, orderTypeId: "global" } })
      .draft.audience.gender,
    "ALL",
  );
});
test("effective capabilities remove unsupported targeting and smart-managed overrides", () => {
  const d = draft();
  d.audience.behaviors = [resource("behavior")];
  d.audience.include = [resource("audience")];
  const cap = capability(d);
  cap.fields.customAudience = {
    state: "NOT_APPLICABLE",
    reason: "此出价不支持",
  };
  let result = reconcileConfiguration(d, cap);
  assert.deepEqual(result.audience.behaviors, []);
  assert.deepEqual(result.audience.include, []);
  cap.audienceManagement = "PLATFORM_SMART";
  result = reconcileConfiguration(d, cap);
  assert.equal(result.audience.mode, "SMART");
  assert.deepEqual(result.audience.locations, []);
});
test("decimal units keep CNY precision, integer beans and ROI separate", () => {
  assert.equal(decimalUnits("100.25", "CNY_MINOR"), 10025);
  assert.equal(decimalUnits("1.251", "CNY_MINOR"), null);
  assert.equal(decimalUnits("1.5", "BEAN"), null);
  assert.equal(decimalUnits("2.1234", "RATIO"), 2.1234);
  assert.equal(decimalUnits("-10", "BEAN"), null);
  assert.equal(decimalUnits("1e5", "BEAN"), null);
});
test("complete permitted configuration passes frontend checks", () => {
  assert.deepEqual(validateConfiguration(draft(), capability()), []);
});
test("age boundaries follow effective account rules", () => {
  const d = draft();
  d.audience.age = { min: "16", max: "45" };
  assert.ok(
    validateConfiguration(d, capability(d)).some((i) => i.field === "age"),
  );
  d.audience.age = { min: "18", max: "45" };
  assert.ok(
    !validateConfiguration(d, capability(d)).some((i) => i.field === "age"),
  );
  d.audience.age.max = "";
  assert.ok(validateConfiguration(d, null).some((i) => i.field === "age"));
});
test("regions require native IDs and inclusion/exclusion cannot conflict", () => {
  const d = draft();
  d.audience.locationMode = "REGION";
  assert.ok(validateConfiguration(d, null).some((i) => i.field === "region"));
  d.audience.locations = [{ id: "region", platformId: "", name: "中文地名" }];
  assert.ok(
    validateConfiguration(d, null).some((i) => i.field === "resources"),
  );
  d.audience.include = [resource("same")];
  d.audience.exclude = [resource("same")];
  assert.ok(
    validateConfiguration(d, null).some((i) => i.field === "customAudience"),
  );
});
test("budget limits, bid unit and schedule overlap are checked", () => {
  const d = draft();
  d.budget.amount = "0.01";
  d.budget.bidValue = "2.001";
  d.schedule.end = d.schedule.start;
  d.schedule.allDay = false;
  d.schedule.slots = [
    { days: [1], start: "10:00", end: "12:00" },
    { days: [1], start: "11:00", end: "13:00" },
  ];
  const fields = validateConfiguration(d, capability(d)).map((i) => i.field);
  for (const field of ["budget", "bid", "schedule", "timeSlots"])
    assert.ok(fields.includes(field));
});
test("auth, permissions, API mode, channel, expiry and shop pending all gate submission", () => {
  const d = draft(),
    cap = capability(d);
  assert.equal(submissionBlock(d, cap, "API", actor.permissions, true), "");
  assert.match(
    submissionBlock(d, cap, "MOCK", actor.permissions, true),
    /演示/,
  );
  assert.match(submissionBlock(d, cap, "API", [], true), /权限/);
  assert.match(
    submissionBlock(
      d,
      { ...cap, writeChannel: "MANUAL" },
      "API",
      actor.permissions,
      true,
    ),
    /正式执行通道/,
  );
  assert.match(
    submissionBlock(
      d,
      { ...cap, authorization: "UNAUTHORIZED" },
      "API",
      actor.permissions,
      false,
    ),
    /授权/,
  );
  assert.match(
    submissionBlock(
      newAdvertisingDraft("shop"),
      null,
      "API",
      actor.permissions,
      true,
    ),
    /尚未核验/,
  );
  assert.match(
    submissionBlock(
      d,
      { ...cap, expiresAt: "2020-01-01" },
      "API",
      actor.permissions,
      true,
    ),
    /过期/,
  );
});
test("local storage restores drafts and templates without grants or tokens", () => {
  const d = draft();
  d.audience.interests = [resource("interest")];
  const raw = JSON.stringify({
    drafts: [{ ...d, validationToken: "secret", capabilities: capability(d) }],
    templates: [
      {
        id: "template",
        name: "本地模板",
        product: "adq",
        scenario: "ADQ",
        accountId: d.accountId,
        audience: d.audience,
      },
    ],
    tickets: [ticket(d)],
  });
  const saved = readAdvertisingStorage(raw);
  assert.equal(
    saved.drafts[0].audience.interests[0].platformId,
    "native-test-interest",
  );
  assert.equal(saved.templates[0].name, "本地模板");
  assert.equal(saved.tickets[0].idempotencyKey, "test-idempotency");
  assert.equal(JSON.stringify(saved).includes("secret"), false);
  assert.notEqual(
    storageKey(actor),
    storageKey({ ...actor, userId: "another" }),
  );
  assert.notEqual(storageKey(actor), storageKey({ ...actor, mode: "MOCK" }));
});
test("malformed local data cannot crash resource or schedule controls", () => {
  const d = draft();
  const result = readAdvertisingStorage(
    JSON.stringify({
      drafts: [
        {
          ...d,
          audience: {
            ...d.audience,
            interests: [null, 5, { name: "fake" }],
            gender: "bad",
          },
          schedule: { slots: [null, { days: "bad" }] },
        },
      ],
    }),
  );
  assert.equal(result.drafts.length, 1);
  assert.deepEqual(result.drafts[0].audience.interests, []);
  assert.equal(result.drafts[0].audience.gender, "ALL");
  assert.deepEqual(result.drafts[0].schedule.slots, []);
  assert.deepEqual(readAdvertisingStorage("bad-json").drafts, []);
});
test("missing configuration endpoint throws instead of returning pretend tags", async () => {
  const api = new AdvertisingService(
    new ApiClient(
      "https://test.invalid",
      async () => new Response(null, { status: 404 }),
    ),
    actor,
  );
  await assert.rejects(api.lookup(configurationContext(draft()), "interests"), {
    code: "NO_DATA",
  });
});
test("demo service cannot read native options or perform writes", async () => {
  const api = new AdvertisingService(
    new MockAdapter({
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
    }),
    {
      ...actor,
      mode: "MOCK",
    },
  );
  await assert.rejects(api.catalog("adq"), { code: "API_NOT_CONFIGURED" });
});
test("catalog rejects cross-tenant and unlinked shop accounts", async () => {
  const base = {
    contractVersion: "advertising-v9.0",
    source: "API",
    tenantId: actor.tenantId,
    product: "adq",
    reason: "",
    accounts: [
      {
        id: "test-account",
        name: "测试",
        authorization: "AUTHORIZED",
        writable: true,
        shopIds: ["foreign-shop"],
      },
    ],
    shops: [{ id: "foreign-shop", name: "不可访问" }],
  };
  const api = new AdvertisingService(
    new ApiClient("https://test.invalid", async () => envelope(base)),
    actor,
  );
  await assert.rejects(api.catalog("adq"), { code: "INVALID_RESPONSE" });
});
test("capabilities reject stale or mismatched context", async () => {
  const d = draft();
  for (const cap of [
    { ...capability(d), contextKey: "wrong" },
    { ...capability(d), expiresAt: "2020-01-01" },
  ]) {
    const api = new AdvertisingService(
      new ApiClient("https://test.invalid", async () => envelope(cap)),
      actor,
    );
    await assert.rejects(api.capabilities(configurationContext(d)), {
      code: "INVALID_RESPONSE",
    });
  }
});
test("native lookup verifies account context and retains search cursor", async () => {
  const d = draft();
  let path = "";
  const api = new AdvertisingService(
    new ApiClient("https://test.invalid", async (url) => {
      path = String(url);
      return envelope({
        ...metadata(d),
        kind: "interests",
        items: [resource("interest")],
        nextCursor: "cursor-2",
        reason: "",
      });
    }),
    actor,
  );
  const result = await api.lookup(configurationContext(d), "interests", {
    q: "测试",
    cursor: "cursor-1",
  });
  assert.equal(result.items[0].platformId, "native-test-interest");
  assert.match(path, /cursor=cursor-1/);
  assert.equal(new URL(path).searchParams.get("q"), "测试");
});
test("unknown write responses are rejected, never transformed to success", async () => {
  const d = draft(),
    cap = capability(d);
  const api = new AdvertisingService(
    new ApiClient("https://test.invalid", async (url) =>
      String(url).endsWith("validations")
        ? envelope(validation(d))
        : envelope({
            ...metadata(d),
            taskId: "test-task",
            idempotencyKey: "test-idempotency",
            state: "SUCCEEDED",
            businessId: null,
            platformStatus: null,
            message: "bad",
          }),
    ),
    actor,
  );
  const checked = await api.validate(d, cap);
  await assert.rejects(api.submit(d, cap, checked, ticket(d)), {
    code: "INVALID_RESPONSE",
  });
});
test("validation token is bound to full configuration and cannot survive edits", async () => {
  const d = draft(),
    cap = capability(d);
  let creates = 0;
  const api = new AdvertisingService(
    new ApiClient("https://test.invalid", async (url) => {
      if (String(url).endsWith("validations")) return envelope(validation(d));
      creates++;
      return envelope({});
    }),
    actor,
  );
  const checked = await api.validate(d, cap);
  const edited = structuredClone(d);
  edited.budget.amount = "101";
  await assert.rejects(api.submit(edited, cap, checked, ticket(edited)), {
    code: "FORBIDDEN",
  });
  assert.equal(creates, 0);
});
test("successful task rendering requires real backend business ID and idempotent match", async () => {
  const d = draft(),
    cap = capability(d),
    t = ticket(d);
  let requestBody: Record<string, unknown> | null = null;
  const api = new AdvertisingService(
    new ApiClient("https://test.invalid", async (url, init) => {
      if (String(url).endsWith("validations")) return envelope(validation(d));
      requestBody = JSON.parse(String(init?.body));
      return envelope({
        ...metadata(d),
        taskId: "test-task",
        idempotencyKey: t.idempotencyKey,
        state: "PENDING",
        verification: "PENDING",
        approvalId:null,version:1,cancellable:true,
        businessId: null,
        platformStatus: null,
        message: "测试异步任务",
      });
    }),
    actor,
  );
  const checked = await api.validate(d, cap);
  const result = await api.submit(d, cap, checked, t);
  assert.equal(result.state, "PENDING");
  assert.equal(result.businessId, null);
  assert.equal(
    (requestBody as unknown as { idempotencyKey: string }).idempotencyKey,
    t.idempotencyKey,
  );
});
test("cancellation requires the original task context and backend cancellable state", async () => {
  const d = draft(), t = ticket(d);
  const pending: ConfigurationTask = {
    ...metadata(d), contractVersion: "advertising-v9.0", source: "API", taskId: "test-task", idempotencyKey: t.idempotencyKey,
    state: "PENDING", verification: "PENDING", approvalId: null, version: 3,
    cancellable: true, businessId: null, platformStatus: null, message: "测试待执行申请",
  };
  let requests = 0;
  const api = new AdvertisingService(new ApiClient("https://test.invalid", async (_url, init) => {
    requests++;
    const body = JSON.parse(String(init?.body));
    assert.equal(body.expectedVersion, 3);
    assert.equal(body.originalIdempotencyKey, t.idempotencyKey);
    return envelope({ ...pending, state: "CANCELLED", cancellable: false, version: 4 });
  }), actor);
  await assert.rejects(api.cancel(t, { ...pending, contextKey: "wrong" }), { code: "INVALID_RESPONSE" });
  await assert.rejects(api.cancel(t, { ...pending, state: "PROCESSING" }), { code: "FORBIDDEN" });
  assert.equal(requests, 0);
  assert.equal((await api.cancel(t, pending)).state, "CANCELLED");
  assert.equal(requests, 1);
});
