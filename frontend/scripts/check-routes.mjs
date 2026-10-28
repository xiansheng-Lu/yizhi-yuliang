import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
const base = process.env.FRONTEND_TEST_URL ?? "http://127.0.0.1:3000";
const pages = [
  "overview",
  "goals",
  "monitor",
  "tasks",
  "planning",
  "videos",
  "graphics",
  "editing",
  "assets",
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
];
const rows = [];
for (const path of [
  "/",
  "/login",
  "/mobile",
  "/app/command",
  "/app/account",
  ...pages.map((id) => `/app/${id}`),
]) {
  const response = await fetch(base + path);
  assert.equal(response.status, 200, path);
  const body = await response.text();
  assert.ok(body.includes("易致") || body.includes("正在"), path);
  if (path === "/")
    assert.equal(
      body,
      readFileSync("public/index.html", "utf8"),
      "Next root must serve exact frozen homepage",
    );
  rows.push({ path, status: response.status });
}
const baseline = JSON.parse(readFileSync("../docs/shared/baselines/homepage-baseline.json"));
for (const [file, sha] of Object.entries(baseline.files))
  assert.equal(
    createHash("sha256")
      .update(Buffer.from(await (await fetch(`${base}/${file}`)).arrayBuffer()))
      .digest("hex"),
    sha,
    file,
  );
for (const [path, expected] of [
  ["/app/analytics", "/app/reports"],
  ["/app/analytics?result=existing-analysis&shop=shop-tea&ref=one&ref=two", "/app/reports?result=existing-analysis&shop=shop-tea&ref=one&ref=two"],
  ["/login.html?next=campaigns", "/login?next=campaigns"],
  ["/mobile.html", "/mobile"],
  ["/app.html", "/legacy"],
  ["/app/campaigns", "/app/plans"],
  ["/app", "/app/command"],
  ["/app/scripts", "/app/planning"],
  ["/app/scripts?document=existing-script&shop_id=shop-tea&ref=one&ref=two", "/app/planning?document=existing-script&shop_id=shop-tea&ref=one&ref=two"],
  ["/app/experiments", "/app/assets"],
  ["/app/experiments?shop_id=shop-tea", "/app/assets?shop_id=shop-tea"],
  ["/app/budgets?platform=adq&accountId=test-account&product=adq&budgetId=test-budget", "/app/decisions?platform=adq&accountId=test-account&product=adq&budgetId=test-budget&section=allocation"],
]) {
  const response = await fetch(base + path, { redirect: "manual" });
  assert.equal(response.status, 307);
  assert.equal(
    new URL(response.headers.get("location"), base).pathname +
      new URL(response.headers.get("location"), base).search,
    expected,
  );
  rows.push({ path, status: response.status, destination: expected });
}
for (const path of ["/app/not-a-page", "/api/v1/frontend/snapshot"])
  assert.equal((await fetch(base + path)).status, 404, path);
console.log(
  JSON.stringify(
    { base, rootHashVerified: true, legacyRouteCount: pages.length, addedRouteCount: 2, rows },
    null,
    2,
  ),
);
