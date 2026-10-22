import test from "node:test";
import assert from "node:assert/strict";
import {
  actionAvailability,
  amount,
  compatibleRoi,
  dailyGoalGaps,
  defaultPlanFilter,
  deliveryHref,
  executionStates,
  filterPlans,
  LatestDeliveryRead,
  normalizeScope,
  parseScope,
  redact,
  scopeKey,
  validDate,
  type DeliveryData,
  type DeliveryScope,
  type PlatformCapabilities,
} from "../src/lib/delivery";
import { createMockSnapshot } from "../src/services/mock-data";
import {
  adaptDeliverySnapshot,
  DeliveryService,
  legacyExecution,
  retainDeliveryFailure,
  validateDeliveryData,
} from "../src/services/delivery-service";
import { ApiClient } from "../src/services/api-client";
import { MockAdapter } from "../src/services/mock-adapter";
import { ServiceError } from "../src/services/contracts";
import type { Identity } from "../src/lib/domain";
import type { CommandJob } from "../src/lib/command-job";
const scope: DeliveryScope = {
  platform: "adq",
  shopId: "shop-tea",
  accountId: "account-adq",
  start: "2026-10-21",
  end: "2026-10-21",
};
const actor: Identity = {
  userId: "user-1",
  tenantId: "tenant-demo",
  shopIds: ["shop-tea"],
  displayName: "测试",
  mode: "API",
  permissions: ["delivery.manage", "task.create"],
  workspacePages: ["plans"],
};
const snapshot = () => ({ ...createMockSnapshot(), source: "API" as const });
const envelope = (data: unknown) =>
  new Response(
    JSON.stringify({
      schemaVersion: "command-job-v1.0",
      requestId: "test-request",
      data,
    }),
    { headers: { "Content-Type": "application/json" } },
  );
function fixture(): DeliveryData {
  const data = adaptDeliverySnapshot(snapshot(), actor, scope);
  const at = new Date().toISOString();
  const cap: PlatformCapabilities = {
    platform: "adq",
    accountId: "account-adq",
    version: 1,
    checkedAt: at,
    actions: {
      budget: {
        status: "AUTHORIZED",
        reason: "后端已授权",
        permission: "delivery.manage",
      },
      review: {
        status: "AUTHORIZED",
        reason: "允许审批",
        permission: "delivery.manage",
      },
      verify: {
        status: "AUTHORIZED",
        reason: "允许核验",
        permission: "delivery.manage",
      },
    },
    execution: "COMMAND_JOB",
    automationMode: "OBSERVE",
    refreshSeconds: null,
    maxAgeSeconds: 300,
    supportedObjects: ["CAMPAIGN", "LIVE"],
    supportedMetrics: ["spend", "attributedGmvMinor", "roi"],
    supportedFilters: ["status", "level"],
    supportedColumns: ["spend", "result", "budget", "bid", "updated"],
    schema: {
      objectives: ["TEST_OBJECTIVE"],
      targets: ["LIVE"],
      bidEditable: true,
      minAmount: 100,
      maxAmount: 10000000,
    },
  };
  data.accounts = data.accounts.map((a) => ({
    ...a,
    authorization: "AUTHORIZED",
  }));
  data.capabilities = [cap];
  data.plans = data.plans.map((p) => ({ ...p, version: 1 }));
  data.sync = { ...data.sync, state: "READY", syncedAt: at };
  return data;
}
test("delivery: formal channels only include ADQ and beans; shop and wxpromo remain distinct", () => {
  const data = adaptDeliverySnapshot(snapshot(), actor, {
    ...scope,
    platform: "all",
    accountId: "all",
  });
  assert.deepEqual(
    data.accounts.map((a) => a.platform),
    ["adq"],
  );
  assert.deepEqual(
    data.plans.map((p) => p.id),
    ["plan-adq"],
  );
  assert.equal(data.plans[0].nativeId, "mock-plan-adq");
  assert.equal(data.shops[0].id, "shop-tea");
  assert.equal(data.capabilities.length, 0);
  assert.equal(data.funds.length, 0);
  assert.equal(data.goals.length, 0);
});
test("delivery: tenant, authorized shop and source are independent boundaries", () => {
  assert.equal(
    adaptDeliverySnapshot(snapshot(), { ...actor, shopIds: [] }, scope).plans
      .length,
    0,
  );
  assert.equal(
    adaptDeliverySnapshot(snapshot(), { ...actor, tenantId: "other" }, scope)
      .accounts.length,
    0,
  );
  assert.equal(
    adaptDeliverySnapshot(createMockSnapshot(), actor, scope).plans.length,
    0,
  );
  const data = adaptDeliverySnapshot(snapshot(), actor, {
    ...scope,
    accountId: "account-else",
  });
  assert.equal(data.plans.length, 0);
  assert.equal(data.executions.length, 0);
});
test("delivery: wx beans orders are never inferred from legacy CNY campaigns", () => {
  const data = adaptDeliverySnapshot(snapshot(), actor, {
    ...scope,
    platform: "beans",
    accountId: "all",
  });
  assert.equal(data.plans.length, 0);
  assert.equal(amount(500, "CNY"), "¥5");
  assert.equal(amount(500, "BEAN"), "500 豆");
  assert.equal(amount(null, "BEAN"), "--");
});
test("delivery: unsupported date ranges retain configuration, never reuse historical actuals", () => {
  const data = adaptDeliverySnapshot(snapshot(), actor, {
    ...scope,
    start: "2026-10-22",
    end: "2026-10-22",
  });
  assert.equal(data.plans.length, 1);
  assert.equal(data.plans[0].metrics.spend, null);
  assert.equal(data.plans[0].metrics.attributedGmvMinor, null);
  assert.equal(data.plans[0].metrics.roi, null);
  assert.equal(
    data.sync.syncedAt,
    null,
    "publication is not a successful sync timestamp",
  );
});
test("delivery: ROI aggregation requires a nonempty explicit comparable key", () => {
  const plans = fixture().plans;
  assert.equal(compatibleRoi(plans), null);
  plans[0].metrics.comparableKey = "adq:paid:day:CNY";
  assert.ok(compatibleRoi(plans)! > 0);
  const second = structuredClone(plans[0]);
  second.id = "second";
  second.metrics.comparableKey = "adq:paid:7day:CNY";
  assert.equal(compatibleRoi([...plans, second]), null);
  plans[0].metrics.spend = 0;
  assert.equal(compatibleRoi(plans), null);
});
test("delivery: backend capability, account authorization and user permission all gate mutations", () => {
  const data = fixture();
  assert.equal(
    actionAvailability(data, actor, scope.accountId, "budget").status,
    "AUTHORIZED",
  );
  assert.equal(
    actionAvailability(
      data,
      { ...actor, permissions: [] },
      scope.accountId,
      "budget",
    ).status,
    "FORBIDDEN",
  );
  data.accounts[0].authorization = "UNAUTHORIZED";
  assert.equal(
    actionAvailability(data, actor, scope.accountId, "budget").status,
    "UNAUTHORIZED",
  );
  data.accounts[0].authorization = "AUTHORIZED";
  data.capabilities = [];
  assert.equal(
    actionAvailability(data, actor, scope.accountId, "budget").status,
    "NOT_INTEGRATED",
  );
});
test("delivery: aggregate scope cannot write or approve; expired capabilities and demo never grant authority", () => {
  const data = fixture();
  data.scope = { ...scope, platform: "all", accountId: "all" };
  assert.equal(
    actionAvailability(data, actor, scope.accountId, "budget").status,
    "NOT_APPLICABLE",
  );
  data.scope = scope;
  data.capabilities[0].checkedAt = "2020-01-01T00:00:00Z";
  assert.equal(
    actionAvailability(data, actor, scope.accountId, "budget").status,
    "ERROR",
  );
  data.capabilities[0].checkedAt = new Date().toISOString();
  data.source = "MOCK";
  assert.equal(
    actionAvailability(data, actor, scope.accountId, "budget").status,
    "NOT_INTEGRATED",
  );
});
test("delivery: failed reads retain only the current scope; permission revocation clears business caches", () => {
  const data = fixture();
  const stale = retainDeliveryFailure(
    data,
    new ServiceError("NETWORK_ERROR", "offline"),
  );
  assert.equal(stale.plans[0].id, "plan-adq");
  assert.equal(stale.sync.state, "STALE");
  assert.equal(stale.capabilities.length, 0);
  const forbidden = retainDeliveryFailure(
    data,
    new ServiceError("FORBIDDEN", "revoked"),
  );
  assert.equal(forbidden.plans.length, 0);
  assert.equal(forbidden.executions.length, 0);
  assert.equal(forbidden.sync.state, "FORBIDDEN");
});
test("delivery: platform switching drops incompatible accounts and validates malformed date input", () => {
  const data = fixture();
  const switched = normalizeScope(
    { ...scope, platform: "beans" },
    data.shops,
    data.accounts,
  );
  assert.equal(switched.accountId, "all");
  assert.equal(switched.shopId, scope.shopId);
  for (const value of [null, "", "2026-99-99", "2026-02-30", "<script>"])
    assert.equal(validDate(value), false);
  assert.equal(
    parseScope(new URLSearchParams("platform=all&accountId=old")).accountId,
    "all",
  );
  assert.equal(
    parseScope(new URLSearchParams("start=2026-10-22&end=2026-10-20")).end,
    "2026-10-22",
  );
});
test("delivery: drill-down links preserve real scope and object IDs with safe encoding", () => {
  const href = deliveryHref(
    "executions",
    { ...scope, accountId: "account & 1" },
    { taskId: "task/1", planId: "plan 1" },
  );
  const q = new URL(href, "https://local.test").searchParams;
  assert.equal(q.get("taskId"), "task/1");
  assert.equal(q.get("accountId"), "account & 1");
  assert.equal(q.get("shopId"), scope.shopId);
});
test("delivery: filtering, sorting and page inputs never mutate shared business objects", () => {
  const plans = fixture().plans;
  const other = {
    ...structuredClone(plans[0]),
    id: "p2",
    nativeId: "native-2",
    name: "第二计划",
    status: "PAUSED" as const,
  };
  const original = JSON.stringify(plans);
  assert.equal(
    filterPlans([...plans, other], {
      ...defaultPlanFilter,
      status: "PAUSED",
    })[0].id,
    "p2",
  );
  assert.equal(
    filterPlans([...plans, other], {
      ...defaultPlanFilter,
      search: "NATIVE-2",
    })[0].id,
    "p2",
  );
  assert.equal(
    filterPlans(plans, { ...defaultPlanFilter, level: "LIVE" }).length,
    0,
  );
  assert.equal(JSON.stringify(plans), original);
});
test("delivery: rapid account A -> B -> C ignores non-abortable late requests and late failures", async () => {
  const reader = new LatestDeliveryRead();
  const pending: {
    resolve: (value: string) => void;
    reject: (error: Error) => void;
    signal: AbortSignal;
  }[] = [];
  const seen: string[] = [],
    errors: string[] = [];
  const read = (signal: AbortSignal) =>
    new Promise<string>((resolve, reject) =>
      pending.push({ resolve, reject, signal }),
    );
  const a = reader.run(
    read,
    (v) => seen.push(v),
    (e) => errors.push(String(e)),
  );
  const b = reader.run(
    read,
    (v) => seen.push(v),
    (e) => errors.push(String(e)),
  );
  const c = reader.run(
    read,
    (v) => seen.push(v),
    (e) => errors.push(String(e)),
  );
  pending[2].resolve("account-C");
  pending[0].resolve("account-A");
  pending[1].reject(new Error("late account-B failure"));
  await Promise.all([a, b, c]);
  assert.deepEqual(seen, ["account-C"]);
  assert.deepEqual(errors, []);
  assert.equal(pending[0].signal.aborted, true);
  assert.equal(pending[1].signal.aborted, true);
});
test("delivery: unmount cancels a read even if the server ignores cancellation", async () => {
  const reader = new LatestDeliveryRead();
  let finish!: (value: string) => void;
  const seen: string[] = [];
  const promise = reader.run(
    () =>
      new Promise<string>((resolve) => {
        finish = resolve;
      }),
    (value) => seen.push(value),
    () => assert.fail("cancelled error surfaced"),
  );
  reader.cancel();
  finish("old account");
  await promise;
  assert.deepEqual(seen, []);
});
test("delivery: production workspace reads keep cookies and reject response scope or source contamination", async () => {
  const calls: RequestInit[] = [];
  const data = fixture();
  const service = new DeliveryService(
    new ApiClient("https://api.test", async (_url, init) => {
      calls.push(init!);
      return envelope(data);
    }),
  );
  const result = await service.load(
    scope,
    snapshot(),
    actor,
    new AbortController().signal,
  );
  assert.equal(result.plans.length, 1);
  assert.equal(calls[0].credentials, "include");
  assert.equal(calls[0].cache, "no-store");
  data.scope = { ...scope, accountId: "wrong-account" };
  await assert.rejects(
    () => service.load(scope, snapshot(), actor, new AbortController().signal),
    (e) => e instanceof ServiceError && e.code === "INVALID_RESPONSE",
  );
  data.scope = scope;
  data.source = "MOCK";
  await assert.rejects(
    () => service.load(scope, snapshot(), actor, new AbortController().signal),
    (e) => e instanceof ServiceError && e.code === "INVALID_RESPONSE",
  );
});
test("delivery: missing new endpoint preserves the existing API snapshot without inventing capabilities", async () => {
  const api = new DeliveryService(
    new ApiClient(
      "https://api.test",
      async () => new Response("", { status: 404 }),
    ),
  );
  const data = await api.load(
    scope,
    snapshot(),
    actor,
    new AbortController().signal,
  );
  assert.equal(data.source, "API");
  assert.equal(data.plans[0].id, "plan-adq");
  assert.equal(data.capabilities.length, 0);
  assert.equal(data.sync.state, "NOT_INTEGRATED");
});
test("delivery: 429 Retry-After suppresses manual and automatic request storms", async () => {
  let calls = 0;
  const api = new DeliveryService(
    new ApiClient("https://api.test", async () => {
      calls++;
      return new Response("", {
        status: 429,
        headers: { "Retry-After": "30" },
      });
    }),
  );
  await assert.rejects(() =>
    api.load(scope, snapshot(), actor, new AbortController().signal),
  );
  await assert.rejects(() =>
    api.load(scope, snapshot(), actor, new AbortController().signal),
  );
  assert.equal(calls, 1);
});
test("delivery: validation rejects malformed plan models, duplicates and out-of-scope trends", () => {
  const data = fixture();
  validateDeliveryData(data);
  const duplicate = structuredClone(data);
  duplicate.plans.push(duplicate.plans[0]);
  assert.throws(() => validateDeliveryData(duplicate), ServiceError);
  const wrongModel = structuredClone(data);
  Object.assign(wrongModel.plans[0], { kind: "PROMOTION_ORDER" });
  assert.throws(() => validateDeliveryData(wrongModel), ServiceError);
  const badTrend = structuredClone(data);
  badTrend.trends = [
    {
      planId: "plan-adq",
      points: [
        {
          date: "2026-10-20",
          actualGmvMinor: null,
          targetGmvMinor: null,
          spend: 0,
          budget: 0,
          roi: null,
          targetRoi: null,
        },
      ],
    },
  ];
  assert.throws(() => validateDeliveryData(badTrend), ServiceError);
});
test("delivery: API acceptance cannot masquerade as a verified effect and retries remain server-controlled", () => {
  const data = fixture();
  const job = {
    job_id: "job-1",
    mode: "API",
    source: "PC_MANUAL",
    shop_id: scope.shopId,
    target_id: "plan-adq",
    action_type: "PAUSE_AD_PLAN",
    old_value: null,
    new_value: { kind: "BOOLEAN", value: false },
    status: "SUCCEEDED",
    approval: { state: "APPROVED", actor_id: "reviewer" },
    execution_receipt: {
      source: "PLATFORM",
      verification: "PENDING",
      accepted: true,
      platform_receipt_id: "r-1",
    },
    history: [],
    version: 1,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    error: null,
    idempotency_key: "key-1",
  } as unknown as CommandJob;
  const task = legacyExecution(job, data.accounts[0]);
  assert.equal(task.status, "VERIFYING");
  assert.equal(task.safeRetry, false);
  job.status = "FAILED";
  job.execution_receipt!.verification = "UNKNOWN";
  assert.equal(legacyExecution(job, data.accounts[0]).status, "UNKNOWN");
  assert.equal(Object.keys(executionStates).length, 10);
});
test("delivery: nested receipts redact credentials and token-bearing strings", () => {
  assert.ok(!String(redact('{"access_token":"sensitive-value","observed":100}')).includes("sensitive-value"));
  const safe = redact({
    access_token: "secret",
    nested: [
      {
        Authorization: "Bearer abc",
        message: "access_token=abc123&ok=true",
        password: "p",
        observed: 100,
      },
    ],
    idempotencyKey: "k",
  }) as Record<string, unknown>;
  assert.equal(safe.access_token, "[已脱敏]");
  assert.equal(safe.idempotencyKey, "[已脱敏]");
  assert.ok(!JSON.stringify(safe).includes("abc123"));
  assert.ok(JSON.stringify(safe).includes("100"));
});
test("delivery: no production mutation is available through the browser mock adapter", async () => {
  const items = new Map<string, string>();
  const api = new DeliveryService(
    new MockAdapter({
      getItem: (key) => items.get(key) ?? null,
      setItem: (key, v) => {
        items.set(key, v);
      },
      removeItem: (key) => {
        items.delete(key);
      },
    }),
  );
  await assert.rejects(
    () => api.verify("task", 1),
    (e) => e instanceof ServiceError && e.code === "API_NOT_CONFIGURED",
  );
  assert.equal(scopeKey(scope), scopeKey({ ...scope }));
});
test("delivery: daily goal gaps derive only from consecutive cumulative evidence", () => {
  const point = { actualGmvMinor: 2000, targetGmvMinor: 1000, spend: null, budget: null, roi: null, targetRoi: null };
  const gaps = dailyGoalGaps([{ ...point, date: "2026-10-01" }, { ...point, date: "2026-10-02", actualGmvMinor: 2500, targetGmvMinor: 3000 }, { ...point, date: "2026-10-05" }], "2026-10-01");
  assert.deepEqual(gaps.map((g) => g.gapMinor), [1000, -1500, null]);
});
