import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { customerPageIds, isPageId, loginTarget, navigationGroups, pageName, pages } from "../src/lib/routes";

test("six customer navigation entries exclude administration and preserve legacy routes", () => {
  assert.deepEqual(navigationGroups.map((group) => group.name), ["AI驭量中枢", "经营概况", "直播运营", "投放管理", "内容工厂", "数据复盘"]);
  const ids = navigationGroups.flatMap((group) => group.pages.map(([id]) => id));
  assert.equal(ids.length, 17);
  assert.equal(new Set(ids).size, 17);
  for (const [id] of pages) assert.ok(isPageId(id), id);
  for (const id of ["agents", "connections", "settings", "tasks"]) assert.ok(!ids.some((value) => value === id));
  assert.ok(customerPageIds.includes("tasks"));
  assert.ok(isPageId("account"));
  assert.ok(isPageId("command"));
  assert.ok(!isPageId("unknown"));
  assert.equal(pageName("overview"), "经营概况");
  assert.equal(pageName("command"), "AI驭量中枢");
});

test("V12 default and frozen homepage CTA open command while explicit overview deep links survive", () => {
  for (const target of [null, "", "overview", "command", "/app/command"]) assert.equal(loginTarget(target), "/app/command");
  for (const target of ["/app/overview", "#/overview", "#overview"]) assert.equal(loginTarget(target), "/app/overview");
  assert.equal(loginTarget("campaigns"), "/app/plans");
  assert.equal(loginTarget("/app/reports"), "/app/reports");
  assert.equal(loginTarget("mobile"), "/mobile");
});

test("approved logo exception leaves every other homepage byte and original CSS/JS unchanged", () => {
  const originalBrand = '<span class="brand-emblem">✦</span><span class="brand-text"><strong>易致·驭量</strong><small>YULIANG INTELLIGENCE</small></span>';
  const html = readFileSync("public/index.html", "utf8");
  assert.equal((html.match(/<img class="brand-logo"/g) ?? []).length, 2);
  const normalized = html
    .replace('<link rel="stylesheet" href="/assets/brand.css">', "")
    .replace('<link rel="icon" type="image/svg+xml" href="/assets/yuliang-mark.svg">', "")
    .replace(/<img class="brand-logo" src="\/assets\/yuliang-logo(?:-white)?\.png"[^>]*>/g, originalBrand);
  const sha = (content: string | Buffer) => createHash("sha256").update(content).digest("hex");
  assert.equal(sha(normalized), "5841a01138fa82ab6d9b018409f7fe23c81053f8a916c07290e6a2c1ddd92c88");
  assert.equal(sha(readFileSync("public/assets/styles.css")), "6281438a8b2c363c4a9fe5486727f3f996fa4fc7de95f351ea792e083263a5ed");
  assert.equal(sha(readFileSync("public/assets/site.js")), "c03c34b14868d84acaac3038b62a45f532c3e47d0d29862a677cbf4d664f230e");
});
