import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createMockSnapshot } from "../src/services/mock-data";
import { aggregateOverview, loadSnapshotOverview, overviewPlatforms } from "../src/services/overview-adapter";
import { funnelRate, percentChange, shanghaiDate, shiftDate, validDateRange, type OverviewFilter } from "../src/lib/overview";
import { OverviewTrend, OverviewFunnel } from "../src/components/overview-charts";
import type { CommandJob } from "../src/lib/command-job";

function fixture() {
  const snapshot = createMockSnapshot();
  const filter: OverviewFilter = { scope: snapshot.scope, startDate: snapshot.scope.date, endDate: snapshot.scope.date };
  return { snapshot, filter };
}
test("overview keeps KPI, channel, live and distribution totals on the same scope", () => {
  const { snapshot, filter } = fixture();
  const report = aggregateOverview(snapshot, filter);
  assert.equal(report.totals.spend, 1257000);
  assert.equal(report.totals.gmv, 8704000);
  assert.equal(report.channels.reduce((n, row) => n + row.spend!, 0), report.totals.spend);
  assert.equal(report.lives.reduce((n, row) => n + row.gmv!, 0), report.totals.gmv);
  assert.equal(report.distributions.status.reduce((n, slice) => n + slice.value, 0), report.totals.spend);
  for (const connector of ["adq", "qianchuan", "wxpromo", "juguang"]) {
    const scoped = aggregateOverview(snapshot, { ...filter, scope: { ...filter.scope, connector_id: connector } });
    assert.equal(scoped.channels.length, 1);
    assert.equal(scoped.channels[0].id, connector);
    assert.equal(scoped.channels[0].spend, scoped.totals.spend);
    assert.equal(scoped.lives[0].gmv, scoped.totals.gmv);
  }
  const mountain = aggregateOverview(snapshot, { ...filter, scope: { ...filter.scope, shop_id: "shop-mountain", live_session_id: "all" } });
  assert.equal(mountain.channels.length, 1);
  assert.equal(mountain.lives[0].id, "live-mountain");
  assert.equal(mountain.lives[0].spend, mountain.totals.spend);
});
test("dates unsupported by a daily snapshot never reuse that day's numbers", () => {
  const { snapshot, filter } = fixture();
  for (const range of [{ startDate: "2026-10-22", endDate: "2026-10-22" }, { startDate: "2026-10-15", endDate: "2026-10-21" }]) {
    const report = aggregateOverview(snapshot, { ...filter, ...range });
    assert.equal(report.totals.spend, null);
    assert.equal(report.totals.gmv, null);
    assert.equal(report.channels.length, 0);
    assert.equal(report.lives.length, 0);
    assert.deepEqual(report.distributions.status, []);
    assert.match(report.coverageNote, /单日快照/);
  }
});
test("missing historical, order, impression and traffic data are never inferred", () => {
  const { snapshot, filter } = fixture();
  const report = aggregateOverview(snapshot, filter);
  assert.equal(report.trend.status, "unavailable");
  assert.equal(report.funnel.status, "unavailable");
  assert.equal(report.traffic.status, "unavailable");
  assert.equal(report.previous, null);
  assert.equal(report.totals.orders, null);
  assert.equal(report.totals.cpm, null);
  assert.equal(report.totals.conversion, null);
  snapshot.plans[0].spendMinor = null;
  assert.equal(aggregateOverview(snapshot, filter).totals.spend, null);
  assert.deepEqual(aggregateOverview(snapshot, filter).distributions.status, []);
  snapshot.plans[0].spendMinor = Number.NaN;
  assert.equal(aggregateOverview(snapshot, filter).totals.spend, null);
});
test("paused spend remains included and attribution windows cannot be mixed", () => {
  const { snapshot, filter } = fixture();
  snapshot.plans[0].enabled = false;
  const paused = aggregateOverview(snapshot, filter);
  assert.equal(paused.totals.spend, 1257000);
  assert.equal(paused.distributions.status.find((s) => s.id === "paused")?.value, 426000);
  snapshot.plans[0].attributionWindow = "different-window";
  const mixed = aggregateOverview(snapshot, filter);
  assert.equal(mixed.totals.spend, paused.totals.spend);
  assert.equal(mixed.totals.gmv, null);
  assert.equal(mixed.totals.roi, null);
  assert.match(mixed.coverageNote, /归因窗口不一致/);
});
test("API platforms require both an associated account and a verified connector", () => {
  const { snapshot, filter } = fixture();
  snapshot.source = "API";
  snapshot.connectors.find((c) => c.id === "adq")!.status = "READ_ONLY";
  snapshot.connectors.find((c) => c.id === "beans")!.status = "SUPPORTED";
  assert.deepEqual(overviewPlatforms(snapshot, "shop-tea").map((c) => c.id), ["adq"]);
  const report = aggregateOverview(snapshot, filter);
  assert.equal(report.source, "API");
  assert.equal(report.channels.length, 1);
  assert.equal(report.totals.spend, 318000);
});
test("task counters require verified receipts and honor Shanghai day, scope and execution denominator", () => {
  const { snapshot, filter } = fixture();
  const job = (patch: Partial<CommandJob>): CommandJob => ({
    shop_id: "shop-tea", live_session_id: "live-tea", account_id: "account-adq",
    updated_at: "2026-10-20T16:01:00Z", status: "EXECUTING", ...patch,
  }) as CommandJob;
  snapshot.tasks = [job({}), job({ status: "SUCCEEDED", execution_receipt: { source: "MOCK", verification: "MATCHED" } as CommandJob["execution_receipt"] }),
    job({ status: "FAILED" }), job({ status: "REJECTED" }), job({ status: "PENDING_APPROVAL" }),
    job({ updated_at: "2026-10-21T16:00:00Z" }), job({ shop_id: "shop-mountain", live_session_id: "live-mountain" }),
    job({ status: "SUCCEEDED" })];
  const report = aggregateOverview(snapshot, filter);
  assert.deepEqual(report.ai, { running: 1, completed: 1, exceptions: 1, pending: 1, successRate: .5 });
  snapshot.source = "API";
  assert.equal(aggregateOverview(snapshot, filter).ai.completed, 0);
});
test("dates, zero comparison and funnel denominator edge cases stay explicit", () => {
  assert.equal(shanghaiDate("2026-10-21T16:00:00Z"), "2026-10-22");
  assert.equal(shiftDate("2026-01-01", -1), "2025-12-31");
  assert.equal(validDateRange("2026-02-30", "2026-03-01"), false);
  assert.equal(validDateRange("2026-10-22", "2026-10-21"), false);
  assert.equal(percentChange(100, 0), null);
  assert.equal(percentChange(100, null), null);
  assert.equal(percentChange(150, 100), .5);
  assert.equal(funnelRate([{ id: "a", name: "a", count: 0 }, { id: "b", name: "b", count: 2 }], 1), null);
  assert.equal(funnelRate([{ id: "a", name: "a", count: 1 }, { id: "b", name: "b", count: 2 }], 1), null);
});
test("report loader honors cancellation and rejects invalid date ranges", async () => {
  const { snapshot, filter } = fixture();
  const controller = new AbortController(); controller.abort();
  await assert.rejects(() => loadSnapshotOverview({ snapshot, filter, signal: controller.signal }), { name: "AbortError" });
  assert.throws(() => aggregateOverview(snapshot, { ...filter, startDate: "invalid" }), /有效的日期范围/);
});
test("trend supports dual axes, null gaps and real zero values without inventing a line", () => {
  const { filter } = fixture();
  const html = renderToStaticMarkup(createElement(OverviewTrend, { filter, trend: { status: "ready", data: [
    { at: "2026-10-21T01:00:00+08:00", spend: 0, gmv: 300, roi: null },
    { at: "2026-10-21T02:00:00+08:00", spend: null, gmv: 500, roi: 2 },
    { at: "2026-10-21T03:00:00+08:00", spend: 200, gmv: 800, roi: 4 },
  ] } }));
  assert.match(html, /金额使用左轴，ROI使用右轴/);
  assert.match(html, /使用左右方向键选择趋势时间点/);
  assert.doesNotMatch(html, /NaN|Infinity/);
  const empty = renderToStaticMarkup(createElement(OverviewTrend, { filter, trend: { status: "unavailable", reason: "待接入" } }));
  assert.match(empty, /经营趋势待接入/);
  assert.doesNotMatch(empty, /linearGradient/);
  const failed = renderToStaticMarkup(createElement(OverviewTrend, { filter, trend: { status: "error", reason: "请求失败" }, onRetry: () => {} }));
  assert.match(failed, /趋势数据读取失败/);
  assert.match(failed, /重新加载趋势/);
  const funnel = renderToStaticMarkup(createElement(OverviewFunnel, { funnel: { status: "ready", data: [{ id: "a", name: "点击", count: 100 }, { id: "b", name: "支付", count: 10 }] } }));
  assert.match(funnel, /环节流失90.0%/);
});
