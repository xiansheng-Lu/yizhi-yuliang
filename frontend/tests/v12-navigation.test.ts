import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { customerPageIds, isPageId, legacyContentTarget, loginTarget, navigationGroups, pageName, pages } from "../src/lib/routes";
import { canAccessPage } from "../src/lib/access";
import type { Identity } from "../src/lib/domain";
import { CsrfApiClient as ApiClient } from "./helpers/csrf-fetch";

test("six customer navigation entries exclude administration and preserve legacy routes", () => {
  assert.deepEqual(navigationGroups.map((group) => group.name), ["AI驭量中枢", "经营概况", "直播运营", "投放管理", "内容中心", "数据复盘"]);
  const ids = navigationGroups.flatMap((group) => group.pages.map(([id]) => id));
  assert.equal(ids.length, 14);
  assert.equal(new Set(ids).size, 14);
  for (const [id] of pages) assert.ok(isPageId(id), id);
  for (const id of ["agents", "connections", "settings", "tasks"]) assert.ok(!ids.some((value) => value === id));
  assert.ok(customerPageIds.includes("tasks"));
  assert.ok(isPageId("account"));
  assert.ok(isPageId("command"));
  assert.ok(!isPageId("unknown"));
  assert.equal(pageName("overview"), "经营概况");
  assert.equal(pageName("command"), "AI驭量中枢");
});

test("content center exposes five workspaces and redirects removed entries with document context", () => {
  assert.deepEqual(navigationGroups.find(group => group.name === "内容中心")?.pages, [
    ["planning", "内容策划"], ["videos", "视频创作"], ["graphics", "平面创作"], ["editing", "智能剪辑"], ["assets", "素材库"],
  ]);
  for (const [id, name] of navigationGroups.find(group => group.name === "内容中心")!.pages) assert.equal(pageName(id), name);
  const query = new URLSearchParams({ document: "已有脚本", shop_id: "shop-tea" });
  assert.equal(legacyContentTarget("scripts", query), `/app/planning?${query}`);
  assert.equal(legacyContentTarget("experiments", query), `/app/assets?${query}`);
  assert.equal(legacyContentTarget("planning", query), null);
  for (const target of ["scripts", "/app/scripts", "#/scripts"]) assert.equal(loginTarget(target), "/app/planning");
  for (const target of ["experiments", "/app/experiments", "#/experiments"]) assert.equal(loginTarget(target), "/app/assets");
});

test("existing script grants open planning without granting other content workspaces", () => {
  assert.ok(customerPageIds.includes("scripts"));
  assert.ok(customerPageIds.includes("experiments"));
  const actor: Identity = { userId: "member", tenantId: "tenant", displayName: "成员", shopIds: ["shop"], permissions: [], mode: "API", workspacePages: ["scripts"] };
  assert.equal(canAccessPage(actor, "planning"), true);
  for (const page of ["assets", "videos", "graphics", "experiments"] as const) assert.equal(canAccessPage(actor, page), false);
  assert.equal(canAccessPage({ ...actor, workspacePages: [] }, "planning"), false);
});

test("sessions issued before the content navigation change still accept legacy content grants", async () => {
  const identity: Identity = { userId: "member", tenantId: "tenant", displayName: "成员", email: "member@example.com", shopIds: ["shop"], permissions: [], mode: "API", workspacePages: ["planning", "scripts", "experiments", "assets"] };
  const client = new ApiClient("https://backend.example", (async () => Response.json({ data: identity, requestId: "test-content-session", schemaVersion: "command-job-v1.0" })) as typeof fetch);
  assert.deepEqual(await client.getIdentity(), identity);
});

test("V12 default and frozen homepage CTA open command while explicit overview deep links survive", () => {
  for (const target of [null, "", "overview", "command", "/app/command"]) assert.equal(loginTarget(target), "/app/command");
  for (const target of ["/app/overview", "#/overview", "#overview"]) assert.equal(loginTarget(target), "/app/overview");
  assert.equal(loginTarget("campaigns"), "/app/plans");
  assert.equal(loginTarget("/app/reports"), "/app/reports");
  assert.equal(loginTarget("mobile"), "/mobile");
});

test("approved logo and heading spacing exceptions preserve the recorded homepage baseline", () => {
  const originalBrand = '<span class="brand-emblem">✦</span><span class="brand-text"><strong>易致·驭量</strong><small>YULIANG INTELLIGENCE</small></span>';
  const html = readFileSync("public/index.html", "utf8");
  assert.equal((html.match(/<img class="brand-logo"/g) ?? []).length, 2);
  const normalized = html
    .replace('<link rel="stylesheet" href="/assets/brand.css">', "")
    .replace('<link rel="icon" type="image/svg+xml" href="/assets/yuliang-mark.svg">', "")
    .replace(/<img class="brand-logo" src="\/assets\/yuliang-logo(?:-white)?\.png"[^>]*>/g, originalBrand);
  const sha = (content: string | Buffer) => createHash("sha256").update(content).digest("hex");
  assert.equal(sha(normalized), "5841a01138fa82ab6d9b018409f7fe23c81053f8a916c07290e6a2c1ddd92c88");
  const baseline = JSON.parse(readFileSync("../docs/shared/baselines/homepage-baseline.json", "utf8")) as { files: Record<string, string> };
  assert.equal(sha(readFileSync("public/assets/styles.css")), baseline.files["assets/styles.css"]);
  assert.equal(sha(readFileSync("public/assets/site.js")), "c03c34b14868d84acaac3038b62a45f532c3e47d0d29862a677cbf4d664f230e");
});

test("data review has one direct navigation entry and retains legacy analysis grants/deep links", () => {
  assert.deepEqual(navigationGroups.find(group => group.name === "数据复盘")?.pages, [["reports", "数据复盘"]]);
  assert.equal(pageName("reports"), "数据复盘");
  assert.equal(loginTarget("/app/analytics?result=existing-result&next=https://untrusted.example"), "/app/reports?result=existing-result");
  assert.equal(loginTarget("#/analytics"), "/app/reports");
  assert.equal(loginTarget("/app/reports?draft=existing-draft"), "/app/reports?draft=existing-draft");
  const actor: Identity = { userId: "reviewer", tenantId: "tenant", displayName: "成员", shopIds: [], permissions: [], mode: "API", workspacePages: ["analytics"] };
  assert.equal(canAccessPage(actor, "reports"), true);
  assert.equal(canAccessPage({ ...actor, workspacePages: [] }, "reports"), false);
});
