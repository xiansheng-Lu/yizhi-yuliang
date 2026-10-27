import test from "node:test";
import assert from "node:assert/strict";
import { createMockSnapshot } from "../src/services/mock-data";
import { MockAnalysisAdapter, ApiAnalysisAdapter, validateAnalysisResult } from "../src/services/analysis-service";
import { CsrfApiClient as ApiClient } from "./helpers/csrf-fetch";
import { buildMockAnalysis } from "../src/services/analysis-mock";
import { analysisMetricDefinitions, analysisShops, defaultAnalysisRules, diagnoseAnalysis, dimensions, groupAnalysisPoints, pointValue, selectDimension, sumPoints, validateAnalysisQuery, type AnalysisQuery } from "../src/lib/business-analysis";
import { createReportDraft, exportReportHtml, moveReportModule, reportPeriodIssue, queryForReviewPeriod } from "../src/lib/analysis-draft";
import type { Identity } from "../src/lib/domain";

const snapshot = createMockSnapshot();
const identity: Identity = { userId: "test", tenantId: "tenant-demo", displayName: "测试", shopIds: ["shop-tea", "shop-mountain"], permissions: [], mode: "MOCK" };
const query = (patch: Partial<AnalysisQuery> = {}): AnalysisQuery => ({ shopId: "shop-tea", platformId: "all", startDate: "2026-10-15", endDate: "2026-10-21", dimensions: [...dimensions], timezone: "Asia/Shanghai", ...patch });
const signal = () => new AbortController().signal;
function setup(actor = identity, store = new Map<string, string>()) { return { adapter: new MockAnalysisAdapter(snapshot, actor, { getItem: key => store.get(key) ?? null, setItem: (key, value) => { store.set(key, value); } }), store }; }
async function execute(adapter: MockAnalysisAdapter, q = query(), scenario: "normal" | "empty" | "partial" | "failure" = "normal") { const task = await adapter.createTask(q, crypto.randomUUID(), signal(), scenario); await adapter.getTask(task.id, signal()); return adapter.getTask(task.id, signal()); }

test("dimension selection supports zero, exclusive single, and four unique dimensions", () => {
  assert.deepEqual(selectDimension([], "shop", false), ["shop"]);
  assert.deepEqual(selectDimension(["shop"], "live", false), ["live"]);
  assert.deepEqual(selectDimension(["shop"], "shop", false), []);
  let selected: AnalysisQuery["dimensions"] = [];
  dimensions.forEach(dimension => { selected = selectDimension(selected, dimension, true); });
  assert.deepEqual(selected, dimensions);
  assert.throws(() => validateAnalysisQuery(query({ dimensions: [] }), snapshot), /至少/);
  assert.throws(() => validateAnalysisQuery(query({ dimensions: ["shop", "shop"] }), snapshot));
});
test("platform/shop relation includes only direct shop or associated ad accounts", () => {
  assert.deepEqual(analysisShops(snapshot, "wxshop").map(s => s.id), ["shop-tea"]);
  assert.deepEqual(analysisShops(snapshot, "douyinshop").map(s => s.id), ["shop-mountain"]);
  assert.throws(() => validateAnalysisQuery(query({ platformId: "wxshop", shopId: "shop-mountain" }), snapshot), /不属于/);
  assert.throws(() => validateAnalysisQuery(query({ startDate: "2026-02-30" }), snapshot));
});
test("tasks are idempotent and survive adapter reload without duplicate results", async () => {
  const { adapter, store } = setup();
  const task = await adapter.createTask(query(), "same-key", signal());
  assert.equal((await adapter.createTask(query(), "same-key", signal())).id, task.id);
  await assert.rejects(adapter.createTask(query({ dimensions: ["ads"] }), "same-key", signal()));
  const reloaded = setup(identity, store).adapter;
  assert.equal((await reloaded.getTask(task.id, signal())).status, "running");
  const done = await reloaded.getTask(task.id, signal());
  assert.equal(done.status, "succeeded");
  assert.equal((await adapter.getTask(task.id, signal())).resultId, done.resultId);
  assert.equal((await adapter.listResults(signal())).length, 1);
});
test("selected dimensions determine KPI and specialist/cross sections", () => {
  for (const dimension of dimensions) {
    const result = buildMockAnalysis(snapshot, query({ dimensions: [dimension] }), "r", "t", "normal");
    assert.ok(result.metrics.every(m => m.dimension === dimension));
    assert.equal(result.sections.length, 1);
  }
  const pair = buildMockAnalysis(snapshot, query({ dimensions: ["shop", "ads"] }), "r", "t", "normal");
  assert.equal(pair.sections.filter(s => s.dimension === "cross").length, 1);
  const all = buildMockAnalysis(snapshot, query(), "r", "t", "normal");
  assert.equal(all.sections.filter(s => s.dimension === "cross").length, 7);
  assert.ok(all.attribution.includes("禁止求和"));
  assert.ok(!all.metrics.some(m => m.id === "total.gmv"));
});
test("fixed fixture changes with dates, platform, shop, and does not expand with queries", () => {
  const value = (q: AnalysisQuery) => buildMockAnalysis(snapshot, q, "r", "t", "normal").metrics.find(m => m.id === "shop.gmv")!.value;
  assert.notEqual(value(query()), value(query({ startDate: "2026-10-14" })));
  assert.notEqual(value(query()), value(query({ platformId: "adq" })));
  assert.notEqual(value(query()), value(query({ shopId: "shop-mountain" })));
  const partial = buildMockAnalysis(snapshot, query({ endDate: "2026-10-23" }), "r", "t", "normal");
  assert.equal(partial.completeness, "partial");
  assert.ok(partial.metrics.every(m => m.value === null));
  assert.ok(partial.points.every(p => p.date <= "2026-10-21"));
  assert.equal(partial.comparison.previous, null);
});
test("missing, unsupported and no-data states never manufacture metric values", async () => {
  const partial = buildMockAnalysis(snapshot, query(), "r", "t", "partial");
  assert.equal(partial.metrics.find(m => m.id === "shop.refund")!.status, "not_integrated");
  assert.equal(partial.metrics.find(m => m.id === "video.gmv")!.value, null);
  const beansSnapshot = structuredClone(snapshot); beansSnapshot.adAccounts.push({ id: "beans-test", shopId: "shop-tea", tenantId: "tenant-demo", connectorId: "beans", name: "测试关联" });
  const unsupported = buildMockAnalysis(beansSnapshot, query({ platformId: "beans", dimensions: ["video"] }), "r", "t", "normal");
  assert.ok(unsupported.metrics.every(m => m.status === "unsupported" && m.value === null));
  const empty = buildMockAnalysis(snapshot, query(), "r", "t", "empty");
  assert.equal(empty.completeness, "empty"); assert.deepEqual(empty.insights, []);
  const { adapter } = setup(); assert.equal((await execute(adapter, query(), "failure")).status, "failed");
});
test("weekly ratios use summed numerators and denominators, preserving nulls and zeros", () => {
  const rows = [{ date: "2026-10-12", values: { "ads.gmv": 1000, "ads.spend": 100 } }, { date: "2026-10-13", values: { "ads.gmv": 1000, "ads.spend": 1000 } }];
  const grouped = groupAnalysisPoints(rows, true); assert.equal(grouped.length, 1); assert.equal(grouped[0].date, "2026-10-12");
  assert.equal(pointValue(grouped[0].values, analysisMetricDefinitions.find(m => m.id === "ads.roi")!), 2000 / 1100);
  assert.equal(sumPoints([{ date: "2026-10-12", values: { a: 1 } }, { date: "2026-10-13", values: { a: null } }]).a, null);
  assert.equal(sumPoints([{ date: "2026-10-12", values: { a: 0 } }]).a, 0);
});
test("rule conclusions require evidence, thresholds and valid comparison periods", () => {
  const result = buildMockAnalysis(snapshot, query(), "r", "t", "normal");
  assert.ok(result.insights.some(i => i.id === "anomaly-ads.roi"));
  assert.ok(result.insights.some(i => i.id === "video-disconnect"));
  assert.ok(result.insights.some(i => i.date === "2026-10-19"));
  assert.ok(result.insights.filter(i => i.kind === "recommendation").every(i => i.evidence && i.action && i.priority && i.verificationMetric));
  const strict = diagnoseAnalysis(result, { ...defaultAnalysisRules, declinePercent: 99, spendRisePercent: 1000, roiFloor: .01 });
  assert.ok(!strict.some(i => i.kind === "anomaly"));
  const noComparison = { ...result, comparison: { previous: null, yearAgo: null } };
  assert.ok(!diagnoseAnalysis(noComparison, defaultAnalysisRules).some(i => i.id === "anomaly-ads.roi"));
});
test("draft edits and publication snapshots cannot modify raw analysis or prior publications", async () => {
  const { adapter } = setup(), task = await execute(adapter), result = await adapter.getResult(task.resultId!, signal());
  const original = structuredClone(result), draft = createReportDraft(result, "weekly", "draft-1");
  draft.modules[0].text = "人工结论"; draft.title = "客户复盘"; draft.metrics[0].value = 1;
  const saved = await adapter.saveDraft(draft, signal());
  assert.deepEqual(await adapter.getResult(result.id, signal()), original);
  const publication = await adapter.publishDraft(saved.id, saved.version, signal());
  saved.modules[0].text = "修改后的结论"; await adapter.saveDraft(saved, signal());
  assert.equal((await adapter.getPublished(publication.id, signal())).snapshot!.modules[0].text, "人工结论");
  await assert.rejects(adapter.saveDraft(draft, signal()), /其他页面/);
  assert.deepEqual(await setup({ ...identity, userId: "another" }).adapter.listDrafts(signal()), []);
});
test("HTML client export escapes edits, excludes hidden modules and includes actual charts", () => {
  const result = buildMockAnalysis(snapshot, query(), "r", "t", "normal"), draft = createReportDraft(result, "weekly", "d");
  draft.title = '<script>alert("x")</script>'; draft.modules[0].text = "<img src=x onerror=alert(1)>";
  draft.modules[1].visible = false; draft.modules[1].text = "THIS-MUST-BE-HIDDEN";
  const html = exportReportHtml(draft);
  assert.ok(!html.includes("<script>")); assert.ok(!html.includes("<img ")); assert.ok(html.includes("&lt;img")); assert.ok(!html.includes("THIS-MUST-BE-HIDDEN")); assert.ok(html.includes("<svg")); assert.ok(html.includes("演示数据"));
  const moved = moveReportModule(draft.modules, draft.modules[1].id, -1); assert.equal(moved[0].id, draft.modules[1].id);
});
test("API uses existing cookie transport with namespace guard and never falls back to mock", async () => {
  const calls: { url: string; options?: RequestInit }[] = [];
  const client = new ApiClient("https://backend.test", (async (url, options) => { calls.push({ url: String(url), options }); return new Response(JSON.stringify({ schemaVersion: "command-job-v1.0", requestId: "req", data: { id: "task", query: query(), progress: 0, status: "queued", createdAt: "2026-10-23T00:00:00Z" } }), { status: 200 }); }) as typeof fetch);
  const adapter = new ApiAnalysisAdapter(client, { ...snapshot, source: "API" });
  await adapter.createTask(query(), "idempotent", signal());
  assert.equal(calls[0].url, "https://backend.test/v1/business-analysis/tasks"); assert.equal(calls[0].options?.credentials, "include");
  assert.throws(() => client.analysisRequest("/v1/other"));
  const unavailable = new ApiAnalysisAdapter(new ApiClient(""), { ...snapshot, source: "API" });
  await assert.rejects(unavailable.createTask(query(), "x", signal()), /未配置/);
});
test("API rejects wrong-source, out-of-scope metrics and partial fake values", () => {
  const result = buildMockAnalysis(snapshot, query(), "r", "t", "normal");
  assert.throws(() => validateAnalysisResult(result, snapshot));
  result.source = "API"; validateAnalysisResult(result, snapshot);
  result.metrics[0].status = "unsupported"; assert.throws(() => validateAnalysisResult(result, snapshot));
});

test("daily/weekly/monthly templates enforce actual periods and use the previous complete month", () => {
  assert.equal(reportPeriodIssue(query(), "weekly"), "");
  assert.match(reportPeriodIssue(query(), "monthly"), /不能用7天/);
  assert.match(reportPeriodIssue(query(), "daily"), /1天/);
  const monthlyQuery = queryForReviewPeriod(query(), "monthly");
  assert.equal(monthlyQuery.startDate, "2026-09-01"); assert.equal(monthlyQuery.endDate, "2026-09-30");
  assert.equal(reportPeriodIssue(monthlyQuery, "monthly"), "");
  const monthlyResult = buildMockAnalysis(snapshot, monthlyQuery, "monthly", "task", "normal");
  assert.equal(monthlyResult.completeness, "complete"); assert.equal(monthlyResult.points.length, 30);
  assert.equal(createReportDraft(monthlyResult, "monthly", "monthly-draft").period, "monthly");
  const weeklyResult = buildMockAnalysis(snapshot, query(), "weekly", "task", "normal");
  assert.throws(() => createReportDraft(weeklyResult, "monthly", "invalid"), /不能用7天/);
  assert.equal(reportPeriodIssue(query({ startDate: "2028-02-01", endDate: "2028-02-29" }), "monthly"), "");
  assert.ok(reportPeriodIssue(query({ startDate: "2028-02-01", endDate: "2028-02-28" }), "monthly"));
});
test("publishing the same saved version is idempotent and history keeps distinct versions", async () => {
  const { adapter } = setup(), task = await execute(adapter), result = await adapter.getResult(task.resultId!, signal());
  const draft = await adapter.saveDraft(createReportDraft(result, "weekly", "versioned"), signal());
  const one = await adapter.publishDraft(draft.id, draft.version, signal());
  const two = await adapter.publishDraft(draft.id, draft.version, signal());
  assert.equal(one.id, two.id); assert.equal((await adapter.listPublished(signal())).length, 1);
  draft.title = "Second version"; const next = await adapter.saveDraft(draft, signal()); await adapter.publishDraft(next.id, next.version, signal());
  assert.equal((await adapter.listPublished(signal())).length, 2); assert.equal((await adapter.getPublished(one.id, signal())).snapshot!.title, one.snapshot!.title);
});
