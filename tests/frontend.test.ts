import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { workspaces, pages, loginTarget } from "../src/lib/routes";
import { businessTotals, type TaskAction } from "../src/lib/domain";
import { MockAdapter, snapshotKey } from "../src/services/mock-adapter";
import {
  MockIdentityAdapter,
  identityKey,
} from "../src/services/mock-identity";
import { ApiClient } from "../src/services/api-client";
import { createMockSnapshot } from "../src/services/mock-data";
const storage = () => {
  const items = new Map<string, string>();
  return {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => {
      items.set(key, value);
    },
    removeItem: (key: string) => {
      items.delete(key);
    },
  };
};
async function clients() {
  const shared = storage(),
    pc = new MockAdapter(shared),
    mobile = new MockAdapter(shared);
  await pc.login({ email: "demo@yuliang.local", password: "demo123" });
  return { pc, mobile, shared };
}

test("four workspaces retain the audited 21 IDs", () => {
  assert.deepEqual(
    workspaces.map((group) => group.pages.length),
    [4, 7, 5, 5],
  );
  assert.deepEqual(
    pages.map(([id]) => id),
    [
      "overview",
      "goals",
      "monitor",
      "tasks",
      "planning",
      "scripts",
      "videos",
      "graphics",
      "editing",
      "assets",
      "experiments",
      "plans",
      "decisions",
      "executions",
      "products",
      "budgets",
      "analytics",
      "reports",
      "agents",
      "connections",
      "settings",
    ],
  );
});
test("login allowlist restores valid targets and rejects external or malformed URLs", () => {
  for (const input of ["campaigns", "/app/campaigns", "#/campaigns"])
    assert.equal(loginTarget(input), "/app/plans");
  assert.equal(loginTarget("/app/reports"), "/app/reports");
  assert.equal(loginTarget("mobile"), "/mobile");
  assert.equal(loginTarget("#/reports"), "/app/reports");
  assert.equal(loginTarget("#reports"), "/app/reports");
  for (const input of [
    null,
    "https://evil.example",
    "//evil.example",
    "/app/plans?next=https://evil.example",
    "%2F%2Fevil.example",
    "javascript:alert(1)",
    "unknown",
    "/app/../settings",
  ])
    assert.equal(loginTarget(input), "/app/overview");
});
test("frozen homepage HTML CSS and JS match the pre-migration SHA256", () => {
  const baseline = JSON.parse(
    readFileSync("docs/homepage-baseline.json", "utf8"),
  );
  for (const [name, expected] of Object.entries(baseline.files))
    assert.equal(
      createHash("sha256")
        .update(readFileSync(join("public", name)))
        .digest("hex"),
      expected,
    );
});
test("all eight business connectors remain independent", () => {
  const data = createMockSnapshot();
  assert.equal(data.connectors.length, 8);
  assert.deepEqual(
    data.connectors.map((x) => x.id),
    [
      "adq",
      "wxpromo",
      "beans",
      "wxshop",
      "qianchuan",
      "douyinshop",
      "juguang",
      "redshop",
    ],
  );
  assert.ok(
    data.connectors.every(
      (x) => x.status === "UNKNOWN" && x.scopes.length === 0,
    ),
  );
});
test("PC and mobile share distinct budget, bid and ROI actions and approval history", async () => {
  const { pc, mobile } = await clients();
  const actions: TaskAction[] = [
    { type: "AD_SET_BUDGET", budgetMinor: 500000 },
    { type: "AD_SET_BID", bidMinor: 320 },
    { type: "AD_SET_ROI_TARGET", targetRoi: 5 },
  ];
  for (const action of actions) {
    const task = await mobile.createTask({
      origin: "MOBILE",
      planId: "plan-qc",
      intent: "手机申请",
      action,
      idempotencyKey: crypto.randomUUID(),
    });
    assert.deepEqual(
      (await pc.getSnapshot()).tasks.find((x) => x.id === task.id)!.action,
      action,
    );
    await pc.approveTask({
      taskId: task.id,
      expectedVersion: 1,
      decision: "APPROVE",
    });
    const current = await mobile.getSnapshot();
    assert.equal(
      current.tasks.find((x) => x.id === task.id)!.status,
      "APPROVED",
    );
    assert.equal(
      current.approvals.filter((x) => x.taskId === task.id).length,
      1,
    );
    assert.equal(current.receipts.length, 0);
    assert.equal(current.plans[0].budgetMinor, 600000);
  }
});
test("live handoff cannot be treated as an ad pause or change streaming state", async () => {
  const { mobile } = await clients();
  const task = await mobile.createTask({
    origin: "MOBILE",
    liveSessionId: "live-tea",
    intent: "交接关播",
    action: { type: "REQUEST_LIVE_END" },
    idempotencyKey: "handoff-1",
  });
  assert.equal(task.status, "MANUAL_REQUIRED");
  assert.equal(task.planId, undefined);
  const data = await mobile.getSnapshot();
  assert.equal(data.plans[0].enabled, true);
  assert.equal(data.liveSessions[0].status, "MOCK_LIVE");
});
test("duplicate mock submissions are deduplicated; stale approvals are rejected", async () => {
  const { pc } = await clients();
  const input = {
    origin: "PC" as const,
    planId: "plan-qc",
    intent: "暂停广告申请",
    action: { type: "AD_PAUSE" as const },
    idempotencyKey: "same-request",
  };
  const first = await pc.createTask(input),
    repeat = await pc.createTask(input);
  assert.equal(first.id, repeat.id);
  await pc.approveTask({
    taskId: first.id,
    expectedVersion: 1,
    decision: "REJECT",
  });
  await assert.rejects(
    pc.approveTask({
      taskId: first.id,
      expectedVersion: 1,
      decision: "APPROVE",
    }),
    { code: "CONFLICT" },
  );
});
test("invalid values, unscoped objects, unauthenticated and forbidden access fail explicitly", async () => {
  const { pc, shared } = await clients();
  await assert.rejects(
    pc.createTask({
      origin: "PC",
      planId: "plan-qc",
      intent: "错误金额",
      action: { type: "AD_SET_BID", bidMinor: Number.NaN },
      idempotencyKey: "invalid",
    }),
    { code: "INVALID_INPUT" },
  );
  await assert.rejects(
    pc.createTask({
      origin: "PC",
      planId: "other-tenant",
      intent: "错误对象",
      action: { type: "AD_PAUSE" },
      idempotencyKey: "invalid-object",
    }),
    { code: "INVALID_INPUT" },
  );
  const actor = await pc.getIdentity();
  shared.setItem(identityKey, JSON.stringify({ ...actor, permissions: [] }));
  await assert.rejects(pc.getSnapshot(), { code: "FORBIDDEN" });
  await pc.logout();
  await assert.rejects(pc.getSnapshot(), { code: "UNAUTHENTICATED" });
  await assert.rejects(
    new MockIdentityAdapter(shared).login({
      email: "real@example.com",
      password: "not-demo",
    }),
    { code: "UNAUTHENTICATED" },
  );
});
test("historical metrics include paused plans, and unknown data never becomes zero", () => {
  const snapshot = createMockSnapshot(),
    before = businessTotals(snapshot.plans);
  snapshot.plans[0].enabled = false;
  assert.deepEqual(businessTotals(snapshot.plans), before);
  snapshot.plans[0].spendMinor = null;
  assert.deepEqual(businessTotals(snapshot.plans), {
    spend: null,
    gmv: null,
    roi: null,
  });
});
test("corrupted demo state fails explicitly and can be restored without destroying identity", async () => {
  const { pc, shared } = await clients();
  shared.setItem(snapshotKey, "broken-json");
  await assert.rejects(pc.getSnapshot(), { code: "INVALID_RESPONSE" });
  await pc.resetDemo();
  assert.equal((await pc.getSnapshot()).tasks.length, 1);
  assert.ok(await pc.getIdentity());
});
test("API mode never silently falls back to Mock and exposes auth, permission and response errors", async () => {
  await assert.rejects(new ApiClient("").getIdentity(), {
    code: "API_NOT_CONFIGURED",
  });
  const mockFetch = (response: Response) =>
    (async () => response) as typeof fetch;
  assert.equal(
    await new ApiClient(
      "https://backend.invalid",
      mockFetch(new Response(null, { status: 401 })),
    ).getIdentity(),
    null,
  );
  await assert.rejects(
    new ApiClient(
      "https://backend.invalid",
      mockFetch(new Response(null, { status: 403 })),
    ).getSnapshot(),
    { code: "FORBIDDEN" },
  );
  await assert.rejects(
    new ApiClient(
      "https://backend.invalid",
      mockFetch(
        Response.json({
          data: createMockSnapshot(),
          schemaVersion: "g1a-draft-1",
          requestId: "draft-1",
        }),
      ),
    ).getSnapshot(),
    { code: "INVALID_RESPONSE" },
  );
});
test("no Next business Route Handlers or database files are introduced", () => {
  const files: string[] = [];
  function walk(dir: string) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else files.push(path);
    }
  }
  walk("src");
  assert.ok(
    files.every((file) => !/(?:^|[\\/])route\.[jt]s$|\.sql$/.test(file)),
  );
});
