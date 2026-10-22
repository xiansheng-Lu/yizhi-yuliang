import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { workspaces, pages, loginTarget } from "../src/lib/routes";
import { businessTotals } from "../src/lib/domain";
import {
  MockAdapter,
  snapshotKey,
  legacySnapshotKey,
} from "../src/services/mock-adapter";
import {
  displayTime,
  jobDelta,
  parseMoneyInput,
  validateJobValue,
  type CreateJobRequest,
  type CommandJob,
} from "../src/lib/command-job";
import { scopedPlans } from "../src/lib/reporting";
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
    assert.equal(loginTarget(input), "/app/command");
});
test("homepage files match the approved branding SHA256 baseline", () => {
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

const request = (
  overrides: Partial<CreateJobRequest> = {},
): CreateJobRequest => ({
  source: "MOBILE_COMMAND",
  shop_id: "shop-tea",
  target_type: "AD_PLAN",
  target_id: "plan-qc",
  action_type: "CHANGE_DAILY_BUDGET",
  new_value: { kind: "MONEY", currency: "CNY", amount_minor: 500000 },
  intent: "把千川A计划日预算调到5000",
  idempotency_key: crypto.randomUUID(),
  ...overrides,
});
const version = (j: CommandJob) => ({
  job_id: j.job_id,
  expected_version: j.version,
});
async function approve(pc: MockAdapter, job: CommandJob) {
  return pc.reviewJob({
    ...version(await pc.confirmJob(version(job))),
    decision: "APPROVE",
  });
}
async function execute(pc: MockAdapter, job: CommandJob) {
  let current = job;
  for (
    let i = 0;
    i < 3 && ["APPROVED", "EXECUTING", "VERIFYING"].includes(current.status);
    i++
  )
    current = await pc.advanceMockJob(version(current));
  return current;
}
async function same(pc: MockAdapter, mobile: MockAdapter, id: string) {
  const a = (await pc.getSnapshot()).tasks.find((j) => j.job_id === id)!,
    b = (await mobile.getSnapshot()).tasks.find((j) => j.job_id === id)!;
  assert.deepEqual(a, b);
  return a;
}

test("1: live query resolves associated sample and Asia/Shanghai timestamp", async () => {
  const { mobile } = await clients(),
    snapshot = await mobile.getSnapshot();
  const result = await mobile.interpretCommand({
    text: "看看现在直播间情况",
    scope: snapshot.scope,
  });
  assert.equal(result.kind, "QUERY");
  if (result.kind !== "QUERY") return;
  const live = snapshot.liveSessions.find(
    (l) => l.id === result.live_session_id,
  )!;
  assert.equal(live.id, "live-tea");
  assert.ok(live.online);
  assert.match(displayTime(live.sourceUpdatedAt), /2026/);
  assert.equal(snapshot.scope.timezone, "Asia/Shanghai");
});
test("2–6: mobile budget → shared draft → confirmation → PC approval → execution → verified result", async () => {
  const { pc, mobile } = await clients(),
    snapshot = await mobile.getSnapshot();
  const parsed = await mobile.interpretCommand({
    text: "把千川A计划日预算调到5000",
    scope: snapshot.scope,
  });
  assert.equal(parsed.kind, "ACTION");
  if (parsed.kind !== "ACTION") return;
  assert.deepEqual(parsed.candidate_ids, ["plan-qc"]);
  assert.equal(parsed.action_type, "CHANGE_DAILY_BUDGET");
  assert.deepEqual(parsed.new_value, {
    kind: "MONEY",
    currency: "CNY",
    amount_minor: 500000,
  });
  const draft = await mobile.createJob(request());
  assert.equal(draft.status, "DRAFT");
  assert.deepEqual(draft.old_value, {
    kind: "MONEY",
    currency: "CNY",
    amount_minor: 600000,
  });
  assert.equal(draft.account_id, "account-qianchuan");
  assert.match(jobDelta(draft), /1,000/);
  await same(pc, mobile, draft.job_id);
  const pending = await mobile.confirmJob(version(draft));
  assert.equal(pending.status, "PENDING_APPROVAL");
  const approved = await pc.reviewJob({
    ...version(pending),
    decision: "APPROVE",
  });
  assert.equal(approved.status, "APPROVED");
  assert.equal(approved.execution_receipt, null);
  assert.equal((await mobile.getSnapshot()).plans[0].budgetMinor, 600000);
  await same(pc, mobile, draft.job_id);
  const executing = await pc.advanceMockJob(version(approved));
  assert.equal(executing.status, "EXECUTING");
  assert.equal(executing.execution_receipt?.accepted, null);
  const verifying = await pc.advanceMockJob(version(executing));
  assert.equal(verifying.status, "VERIFYING");
  assert.equal((await mobile.getSnapshot()).plans[0].budgetMinor, 600000);
  assert.equal(verifying.execution_receipt?.verification, "PENDING");
  const done = await pc.advanceMockJob(version(verifying));
  assert.equal(done.status, "SUCCEEDED");
  assert.equal(done.execution_receipt?.verification, "MATCHED");
  assert.equal(done.execution_receipt?.source, "MOCK");
  assert.equal(done.execution_receipt?.platform_receipt_id, null);
  assert.equal((await mobile.getSnapshot()).plans[0].budgetMinor, 500000);
  await same(pc, mobile, draft.job_id);
  assert.deepEqual(
    done.history.map((h) => h.status),
    [
      "DRAFT",
      "PENDING_APPROVAL",
      "APPROVED",
      "EXECUTING",
      "VERIFYING",
      "SUCCEEDED",
    ],
  );
});
test("7: aggregate budget limit blocks without granting approval or execution", async () => {
  const { pc, mobile } = await clients();
  const job = await mobile.createJob(
    request({
      new_value: { kind: "MONEY", currency: "CNY", amount_minor: 900000 },
    }),
  );
  assert.equal(job.status, "BLOCKED");
  assert.equal(job.risk_summary.source, "MOCK");
  assert.ok(
    job.risk_summary.checks.some((c) => c.code === "BUDGET_LIMIT" && !c.passed),
  );
  assert.equal(job.approval.state, "NOT_REQUESTED");
  await assert.rejects(pc.reviewJob({ ...version(job), decision: "APPROVE" }), {
    code: "CONFLICT",
  });
  assert.equal(job.execution_receipt, null);
  await same(pc, mobile, job.job_id);
});
test("8: pausing plan retains historical spend and GMV, scoped identically", async () => {
  const { pc, mobile } = await clients(),
    before = await pc.getSnapshot();
  const job = await execute(
    pc,
    await approve(
      pc,
      await mobile.createJob(
        request({
          action_type: "PAUSE_AD_PLAN",
          new_value: { kind: "BOOLEAN", value: false },
        }),
      ),
    ),
  );
  assert.equal(job.status, "SUCCEEDED");
  const after = await mobile.getSnapshot();
  assert.equal(after.plans[0].enabled, false);
  assert.deepEqual(
    businessTotals(scopedPlans(after)),
    businessTotals(scopedPlans(before)),
  );
  assert.equal(after.plans[0].spendMinor, before.plans[0].spendMinor);
  assert.deepEqual((await pc.getSnapshot()).scope, after.scope);
  await same(pc, mobile, job.job_id);
});
test("9: ROI target never changes bid; units remain distinct", async () => {
  const { pc, mobile } = await clients(),
    before = await pc.getSnapshot();
  const parsed = await mobile.interpretCommand({
    text: "把千川A计划ROI目标调到6",
    scope: before.scope,
  });
  assert.equal(parsed.kind, "ACTION");
  if (parsed.kind !== "ACTION") return;
  assert.equal(parsed.action_type, "SET_ROI_TARGET");
  assert.deepEqual(parsed.new_value, { kind: "RATIO", value: 6 });
  const done = await execute(
    pc,
    await approve(
      pc,
      await mobile.createJob(
        request({
          action_type: parsed.action_type,
          new_value: parsed.new_value,
        }),
      ),
    ),
  );
  assert.equal(done.status, "SUCCEEDED");
  const after = await mobile.getSnapshot();
  assert.equal(after.plans[0].targetRoi, 6);
  assert.equal(after.plans[0].bidMinor, before.plans[0].bidMinor);
  assert.equal(after.plans[0].budgetMinor, before.plans[0].budgetMinor);
  assert.equal(
    validateJobValue("CHANGE_BID", { kind: "RATIO", value: 6 }),
    false,
  );
});
for (const scenario of [
  "REQUEST_FAILURE",
  "TIMEOUT",
  "VERIFY_FAILURE",
] as const)
  test(`10: ${scenario} is consistent on PC/mobile with no false success`, async () => {
    const { pc, mobile } = await clients(),
      before = await pc.getSnapshot();
    const done = await execute(
      pc,
      await approve(pc, await mobile.createJob(request(), scenario)),
    );
    assert.equal(done.status, "FAILED");
    assert.ok(done.error);
    assert.equal(done.execution_receipt?.source, "MOCK");
    assert.notEqual(done.execution_receipt?.verification, "MATCHED");
    assert.equal(
      (await mobile.getSnapshot()).plans[0].budgetMinor,
      before.plans[0].budgetMinor,
    );
    if (scenario === "TIMEOUT") {
      assert.equal(done.error.code, "MOCK_TIMEOUT");
      assert.equal(done.execution_receipt?.accepted, null);
      assert.equal(done.execution_receipt?.verification, "UNKNOWN");
    }
    await same(pc, mobile, done.job_id);
    await assert.rejects(pc.advanceMockJob(version(done)), {
      code: "CONFLICT",
    });
  });
test("10: rejection, cancellation and expiry are shared terminal states", async () => {
  const { pc, mobile } = await clients();
  for (const action of ["REJECTED", "CANCELLED", "EXPIRED"]) {
    const draft = await mobile.createJob(request());
    const pending = await mobile.confirmJob(version(draft));
    const done =
      action === "REJECTED"
        ? await pc.reviewJob({ ...version(pending), decision: "REJECT" })
        : action === "CANCELLED"
          ? await mobile.cancelJob(version(pending))
          : await pc.expireMockJob(version(pending));
    assert.equal(done.status, action);
    assert.equal(done.execution_receipt, null);
    await same(pc, mobile, done.job_id);
  }
});
test("ambiguous target requires selection; shops and 8 connectors are never conflated", async () => {
  const { mobile } = await clients(),
    snapshot = await mobile.getSnapshot();
  const ambiguous = await mobile.interpretCommand({
    text: "把预算调到5000",
    scope: snapshot.scope,
  });
  assert.equal(ambiguous.kind, "ACTION");
  if (ambiguous.kind !== "ACTION") return;
  assert.ok(ambiguous.candidate_ids.length > 1);
  assert.equal((await mobile.getSnapshot()).tasks.length, 1);
  const all = { ...snapshot.scope, shop_id: "all", live_session_id: "all" };
  const named = await mobile.interpretCommand({
    text: "把千川A计划日预算调到5000",
    scope: all,
  });
  assert.equal(named.kind, "ACTION");
  if (named.kind === "ACTION") assert.equal(named.candidate_ids.length, 2);
  for (const [name, connector] of [
    ["ADQ", "adq"],
    ["微信小店推广", "wxpromo"],
    ["微信豆", "beans"],
  ]) {
    const parsed = await mobile.interpretCommand({
      text: `把${name}出价调到3.2`,
      scope: snapshot.scope,
    });
    assert.equal(parsed.kind, "ACTION");
    if (parsed.kind === "ACTION")
      assert.ok(
        parsed.candidate_ids.every(
          (id) =>
            snapshot.adAccounts.find(
              (a) =>
                a.id === snapshot.plans.find((p) => p.id === id)!.adAccountId,
            )!.connectorId === connector,
        ),
      );
  }
  const mixed = await mobile.interpretCommand({
    text: "把预算和ROI目标调到6",
    scope: snapshot.scope,
  });
  assert.equal(mixed.kind, "UNRECOGNIZED");
});
test("plan-specific bid change is distinct from budget and ROI", async () => {
  const { pc, mobile } = await clients(),
    before = await pc.getSnapshot();
  const done = await execute(
    pc,
    await approve(
      pc,
      await mobile.createJob(
        request({
          action_type: "CHANGE_BID",
          new_value: { kind: "MONEY", currency: "CNY", amount_minor: 320 },
        }),
      ),
    ),
  );
  assert.equal(done.status, "SUCCEEDED");
  const after = await mobile.getSnapshot();
  assert.equal(after.plans[0].bidMinor, 320);
  assert.equal(after.plans[0].budgetMinor, before.plans[0].budgetMinor);
  assert.equal(after.plans[0].targetRoi, before.plans[0].targetRoi);
});
test("live handoff never pauses ads or controls stream", async () => {
  const { pc, mobile } = await clients();
  const draft = await mobile.createJob(
    request({
      target_type: "LIVE_SESSION",
      target_id: "live-tea",
      action_type: "REQUEST_LIVE_END",
      new_value: { kind: "HANDOFF", value: "END" },
    }),
  );
  const confirmed = await mobile.confirmJob(version(draft));
  assert.equal(confirmed.status, "MANUAL_REQUIRED");
  assert.equal(confirmed.account_id, null);
  assert.equal(confirmed.approval.state, "NOT_REQUESTED");
  await assert.rejects(pc.advanceMockJob(version(confirmed)), {
    code: "CONFLICT",
  });
  const data = await pc.getSnapshot();
  assert.equal(data.plans[0].enabled, true);
  assert.equal(data.liveSessions[0].status, "MOCK_LIVE");
});
test("autopilot candidates use the same protocol and cannot auto approve", async () => {
  const { pc } = await clients(),
    snap = await pc.getSnapshot(),
    job = snap.tasks[0];
  assert.equal(job.source, "AUTOPILOT");
  assert.equal(job.schema_version, "1.0");
  assert.equal(job.status, "PENDING_APPROVAL");
  await assert.rejects(pc.advanceMockJob(version(job)), { code: "CONFLICT" });
  await pc.updatePolicy({ ...snap.riskPolicies[0], mode: "AUTOPILOT" });
  assert.equal((await pc.getSnapshot()).tasks[0].status, "PENDING_APPROVAL");
});
test("idempotency and version checks prevent duplicate and stale mutation", async () => {
  const { pc } = await clients(),
    input = request({ idempotency_key: "repeat" });
  const first = await pc.createJob(input),
    repeat = await pc.createJob(input);
  assert.equal(first.job_id, repeat.job_id);
  await assert.rejects(
    pc.createJob({
      ...input,
      new_value: { kind: "MONEY", currency: "CNY", amount_minor: 400000 },
    }),
    { code: "CONFLICT" },
  );
  await pc.confirmJob(version(first));
  await assert.rejects(pc.confirmJob(version(first)), { code: "CONFLICT" });
});
test("policy changes and stale targets require reconfirmation", async () => {
  const { pc, mobile } = await clients();
  const pending = await mobile.confirmJob(
    version(await mobile.createJob(request())),
  );
  const snap = await pc.getSnapshot();
  await pc.updatePolicy({ ...snap.riskPolicies[0], targetRoi: 6 });
  const blocked = await pc.reviewJob({
    ...version(pending),
    decision: "APPROVE",
  });
  assert.equal(blocked.status, "BLOCKED");
  assert.equal(blocked.error?.code, "POLICY_CHANGED");
  assert.notEqual(blocked.approval.state, "APPROVED");
  const a = await mobile.createJob(request()),
    b = await mobile.createJob(request());
  await execute(pc, await approve(pc, a));
  const stale = await mobile.confirmJob(version(b));
  assert.equal(stale.status, "BLOCKED");
  assert.equal(stale.error?.code, "STALE_TARGET");
});
test("approval expiry uses adapter clock; executing tasks are not marked expired", async () => {
  const shared = storage();
  let now = new Date("2026-10-21T04:00:00Z");
  const pc = new MockAdapter(
    shared,
    () => {},
    () => now,
  );
  await pc.login({ email: "demo@yuliang.local", password: "demo123" });
  const pending = await pc.confirmJob(version(await pc.createJob(request())));
  const executing = await pc.advanceMockJob(
    version(
      await approve(
        pc,
        await pc.createJob(
          request({
            action_type: "PAUSE_AD_PLAN",
            new_value: { kind: "BOOLEAN", value: false },
          }),
        ),
      ),
    ),
  );
  now = new Date(now.getTime() + 16 * 60000);
  const snap = await pc.getSnapshot();
  assert.equal(
    snap.tasks.find((j) => j.job_id === pending.job_id)?.status,
    "EXPIRED",
  );
  assert.equal(
    snap.tasks.find((j) => j.job_id === executing.job_id)?.status,
    "EXECUTING",
  );
});
test("scope changes persist across clients and preserve metric definitions", async () => {
  const { pc, mobile } = await clients(),
    snap = await pc.getSnapshot();
  await mobile.setScope({
    ...snap.scope,
    shop_id: "shop-mountain",
    live_session_id: "live-mountain",
    connector_id: "qianchuan",
  });
  const current = await pc.getSnapshot();
  assert.deepEqual(current.scope, (await mobile.getSnapshot()).scope);
  assert.deepEqual(
    scopedPlans(current).map((p) => p.id),
    ["plan-mountain-a"],
  );
  assert.deepEqual(
    businessTotals(scopedPlans(current)),
    businessTotals(scopedPlans(await mobile.getSnapshot())),
  );
  await assert.rejects(pc.setScope({ ...current.scope, shop_id: "unknown" }), {
    code: "INVALID_INPUT",
  });
});
test("money parsing uses exact integer CNY minor units; unknown metrics never become zero", () => {
  assert.equal(parseMoneyInput("5000"), 500000);
  assert.equal(parseMoneyInput("0.29"), 29);
  for (const value of ["0", "1.001", "-1", "NaN", "1e5"])
    assert.throws(() => parseMoneyInput(value));
  const snap = createMockSnapshot();
  snap.plans[0].spendMinor = null;
  assert.deepEqual(businessTotals(snap.plans), {
    spend: null,
    gmv: null,
    roi: null,
  });
});
test("invalid action values and unscoped/unauthorized mutations fail explicitly", async () => {
  const { pc, shared } = await clients();
  await assert.rejects(
    pc.createJob(
      request({
        new_value: { kind: "MONEY", currency: "CNY", amount_minor: 0.1 },
      }),
    ),
    { code: "INVALID_INPUT" },
  );
  await assert.rejects(pc.createJob(request({ target_id: "other-tenant" })), {
    code: "INVALID_INPUT",
  });
  const actor = await pc.getIdentity();
  shared.setItem(
    identityKey,
    JSON.stringify({ ...actor, permissions: ["snapshot.read", "task.create"] }),
  );
  const draft = await pc.createJob(request());
  const pending = await pc.confirmJob(version(draft));
  await assert.rejects(
    pc.reviewJob({ ...version(pending), decision: "APPROVE" }),
    { code: "FORBIDDEN" },
  );
  await assert.rejects(pc.advanceMockJob(version(pending)), {
    code: "FORBIDDEN",
  });
  await assert.rejects(
    pc.updatePolicy((await pc.getSnapshot()).riskPolicies[0]),
    { code: "FORBIDDEN" },
  );
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
test("G1-A migration preserves original storage and does not inherit approvals", async () => {
  const { pc, shared } = await clients();
  const legacy = {
    schemaVersion: "g1a-draft-1",
    plans: [
      { id: "plan-qc", budgetMinor: 600000, bidMinor: 300, targetRoi: 5 },
    ],
    tasks: [
      {
        id: "legacy-budget",
        origin: "MOBILE",
        planId: "plan-qc",
        intent: "旧任务",
        status: "APPROVED",
        action: { type: "AD_SET_BUDGET", budgetMinor: 500000 },
      },
    ],
  };
  const raw = JSON.stringify(legacy);
  shared.setItem(legacySnapshotKey, raw);
  const snap = await pc.getSnapshot();
  const migrated = snap.tasks.find((j) => j.job_id === "g1a:legacy-budget")!;
  assert.equal(migrated.status, "DRAFT");
  assert.equal(migrated.approval.state, "NOT_REQUESTED");
  assert.equal(shared.getItem(legacySnapshotKey), raw);
});
test("corrupted state can be reset without destroying identity or legacy backup", async () => {
  const { pc, shared } = await clients();
  shared.setItem(snapshotKey, "broken-json");
  await assert.rejects(pc.getSnapshot(), { code: "INVALID_RESPONSE" });
  await pc.resetDemo();
  assert.equal((await pc.getSnapshot()).tasks.length, 1);
  assert.ok(await pc.getIdentity());
});
test("API mode refuses Mock execution and invalid authoritative results, no fallback", async () => {
  await assert.rejects(new ApiClient("").getIdentity(), {
    code: "API_NOT_CONFIGURED",
  });
  const fetchWith = (response: Response) =>
    (async () => response) as typeof fetch;
  assert.equal(
    await new ApiClient(
      "https://backend.invalid",
      fetchWith(new Response(null, { status: 401 })),
    ).getIdentity(),
    null,
  );
  await assert.rejects(
    new ApiClient(
      "https://backend.invalid",
      fetchWith(new Response(null, { status: 403 })),
    ).getSnapshot(),
    { code: "FORBIDDEN" },
  );
  await assert.rejects(
    new ApiClient(
      "https://backend.invalid",
      fetchWith(
        Response.json({
          data: createMockSnapshot(),
          schemaVersion: "command-job-v1.0",
          requestId: "1",
        }),
      ),
    ).getSnapshot(),
    { code: "INVALID_RESPONSE" },
  );
  const { pc } = await clients();
  const job = (await pc.getSnapshot()).tasks[0];
  const invalid = {
    ...job,
    mode: "API",
    status: "SUCCEEDED",
    risk_summary: { ...job.risk_summary, source: "SERVER" },
  };
  await assert.rejects(
    new ApiClient(
      "https://backend.invalid",
      fetchWith(
        Response.json({
          data: invalid,
          schemaVersion: "command-job-v1.0",
          requestId: "2",
        }),
      ),
    ).reviewJob({ ...version(job), decision: "APPROVE" }),
    { code: "INVALID_RESPONSE" },
  );
  await assert.rejects(
    new ApiClient("https://backend.invalid").advanceMockJob(version(job)),
    { code: "FORBIDDEN" },
  );
  const timeoutFetch = (async () => {
    throw new DOMException("timeout", "TimeoutError");
  }) as typeof fetch;
  await assert.rejects(
    new ApiClient("https://backend.invalid", timeoutFetch).getSnapshot(),
    { code: "TIMEOUT" },
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
