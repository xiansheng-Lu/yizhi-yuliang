import test from "node:test";
import assert from "node:assert/strict";
import type { Identity } from "../src/lib/domain";
import { canAccessPage, canAccessOptimizationSection, hasWorkspaceGrant, authorizedLoginTarget } from "../src/lib/access";
import { isOptimizationSection, legacyBudgetTarget, optimizationSections } from "../src/lib/optimization";
import { deliveryHref, parseScope } from "../src/lib/delivery";
import { customerPageIds, navigationGroups, isPageId, loginTarget, pageName } from "../src/lib/routes";
const actor: Identity = { userId: "test-member", tenantId: "test-tenant", displayName: "测试", shopIds: ["test-shop"], mode: "API", permissions: [], workspacePages: [] };

test("optimization replaces duplicate navigation while retaining the legacy grant and route", () => {
  const group = navigationGroups.find(group => group.name === "投放管理")!;
  assert.deepEqual(group.pages, [["goals", "经营目标"], ["plans", "计划中心"], ["decisions", "投放优化"], ["executions", "执行记录"]]);
  assert.equal(pageName("decisions"), "投放优化");
  assert.ok(isPageId("budgets"));
  assert.ok(customerPageIds.includes("budgets"));
  assert.equal(loginTarget("/app/budgets"), "/app/decisions?section=allocation");
});
test("old budget links preserve platform, product, account, dates and object IDs", () => {
  const query = new URLSearchParams({ platform: "all", product: "juguang", entityId: "entity-1", productAccountId: "native/account", start: "2026-10-21", end: "2026-10-23", planId: "plan-1", budgetId: "budget-1" });
  const result = new URL(legacyBudgetTarget(query), "https://test.invalid");
  assert.equal(result.pathname, "/app/decisions");
  assert.equal(result.searchParams.get("section"), "allocation");
  for (const [key, value] of query) assert.equal(result.searchParams.get(key), value);
  assert.equal(query.has("section"), false);
  const scope = parseScope(new URLSearchParams("platform=adq&accountId=account-1&start=2026-10-21&end=2026-10-23"));
  const link = new URL(deliveryHref("budgets", scope, { section: "rules", product: "adq", decisionId: "decision-1" }), "https://test.invalid");
  assert.equal(link.pathname, "/app/decisions");
  assert.equal(link.searchParams.get("section"), "rules");
  assert.equal(link.searchParams.get("decisionId"), "decision-1");
});
test("legacy tabs map to canonical sections and invalid section input cannot become a view", () => {
  assert.deepEqual(optimizationSections.map(item => item.id), ["suggestions", "allocation", "funds", "rules"]);
  for (const [tab, section] of [["账户资金", "funds"], ["风险控制", "rules"], ["AI预算优化", "suggestions"]]) {
    const target = new URL(legacyBudgetTarget(new URLSearchParams({ tab })), "https://test.invalid");
    assert.equal(target.searchParams.get("section"), section);
    assert.equal(target.searchParams.has("tab"), false);
  }
  assert.equal(isOptimizationSection("settings"), false);
  assert.equal(isOptimizationSection(null), false);
  assert.equal(new URL(legacyBudgetTarget(new URLSearchParams("section=unknown")), "https://test.invalid").searchParams.get("section"), "allocation");
  assert.equal(new URL(legacyBudgetTarget(new URLSearchParams("tab=constructor")), "https://test.invalid").searchParams.get("section"), "allocation");
});
test("a unified entry does not grant access to another original workspace", () => {
  const decisions = { ...actor, workspacePages: ["decisions"] };
  const budgets = { ...actor, workspacePages: ["budgets"] };
  assert.equal(canAccessPage(actor, "decisions"), false);
  assert.equal(canAccessPage(null, "decisions"), false);
  for (const member of [decisions, budgets]) assert.equal(canAccessPage(member, "decisions"), true);
  assert.equal(canAccessOptimizationSection(decisions, "suggestions"), true);
  assert.equal(canAccessOptimizationSection(decisions, "funds"), false);
  assert.equal(canAccessOptimizationSection(decisions, "allocation"), false);
  assert.equal(canAccessOptimizationSection(budgets, "suggestions"), false);
  assert.equal(canAccessOptimizationSection(budgets, "allocation"), true);
  assert.equal(canAccessOptimizationSection(budgets, "funds"), true);
  assert.equal(hasWorkspaceGrant(budgets, "decisions"), false);
  assert.equal(canAccessPage(decisions, "budgets"), false);
  for (const member of [decisions, budgets]) assert.equal(canAccessOptimizationSection(member, "rules"), true);
  assert.equal(authorizedLoginTarget(budgets, loginTarget("budgets")), "/app/decisions?section=allocation");
  assert.equal(authorizedLoginTarget(actor, "/app/decisions?section=funds"), "/app/account");
});
