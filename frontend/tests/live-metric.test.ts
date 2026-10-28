import test from "node:test";
import assert from "node:assert/strict";
import { liveMetricHref, liveMetricIds, liveMetricMoney, livePlanAmounts, parseLiveMetric } from "../src/lib/live-metric";
import { createMockSnapshot } from "../src/services/mock-data";

test("metric drill-down accepts only the six known metrics on the existing monitor route", () => {
  for (const id of liveMetricIds) {
    assert.equal(parseLiveMetric(id), id);
    assert.equal(new URL(liveMetricHref(id), "https://app.example").pathname, "/app/monitor");
  }
  for (const value of [null, "", "unknown", "GMV", "../plans", "https://example.com", "gmv&metric=spend"])
    assert.equal(parseLiveMetric(value), null);
});

test("detail changes and return navigation preserve other filters and remove duplicated metric parameters", () => {
  const search = new URLSearchParams({ platform: "qianchuan", room: "live-tea", date: "2026-10-21", metric: "gmv" });
  search.append("metric", "orders");
  const next = new URL(liveMetricHref("roi", search.toString()), "https://app.example");
  assert.deepEqual(next.searchParams.getAll("metric"), ["roi"]);
  assert.equal(next.searchParams.get("platform"), "qianchuan");
  assert.equal(next.searchParams.get("room"), "live-tea");
  const home = new URL(liveMetricHref(null, next.search), "https://app.example");
  assert.equal(home.pathname, "/app/monitor");
  assert.equal(home.searchParams.has("metric"), false);
  assert.equal(home.searchParams.get("date"), "2026-10-21");
});

test("detail money distinguishes missing and invalid values from a genuine zero", () => {
  for (const value of [null, undefined, NaN, Infinity, -1]) assert.equal(liveMetricMoney(value), "--");
  assert.equal(liveMetricMoney(0), "¥0.00");
  assert.equal(liveMetricMoney(12345), "¥123.45");
});

test("plan details retain historical paused amounts and the existing ROI zero-denominator rule", () => {
  const plan = { ...createMockSnapshot().plans[0], enabled: false, spendMinor: 10000, paidGmvMinor: 25000 };
  assert.deepEqual(livePlanAmounts(plan), { spend: 10000, gmv: 25000, roi: 2.5 });
  assert.deepEqual(livePlanAmounts({ ...plan, spendMinor: 0 }), { spend: 0, gmv: 25000, roi: null });
  assert.deepEqual(livePlanAmounts({ ...plan, paidGmvMinor: 0 }), { spend: 10000, gmv: 0, roi: 0 });
});

test("not-started sessions and incomplete amounts never become zero or a computed ROI", () => {
  const plan = createMockSnapshot().plans[0];
  const missing = { spend: null, gmv: null, roi: null };
  assert.deepEqual(livePlanAmounts(plan, true), missing);
  assert.deepEqual(livePlanAmounts({ ...plan, paidGmvMinor: null }), missing);
  assert.deepEqual(livePlanAmounts({ ...plan, spendMinor: NaN }), missing);
});
