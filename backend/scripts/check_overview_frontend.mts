/** Real Overview helpers consume PG serialized OverviewReport; no new route or FE loader claim. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import type { OverviewReport } from "../../frontend/src/lib/overview";
const require = createRequire(import.meta.url);
const { overviewKey, percentChange, validDateRange } = require("../../frontend/src/lib/overview.ts");
const report: OverviewReport = JSON.parse(readFileSync(process.argv[2],"utf8"));
assert.equal(report.source,"API");
assert.ok(validDateRange(report.filter.startDate,report.filter.endDate));
assert.equal(JSON.parse(overviewKey(report.filter))[0],report.filter.scope.shop_id);
assert.equal(report.totals.spend,300);
assert.equal(report.totals.gmv,900);
assert.equal(report.totals.roi,3);
assert.equal(percentChange(report.totals.spend,report.previous?.spend ?? null),0);
assert.equal(report.updatedAt,null);
assert.equal(report.trend.status,"ready");
if (report.trend.status === "ready") {
  assert.equal(report.trend.data.length,3);
  assert.equal(report.trend.data.reduce((n,p)=>n+(p.spend ?? 0),0),report.totals.spend);
}
assert.equal(report.funnel.status,"unavailable");
assert.deepEqual(report.lives,[]);
console.log(JSON.stringify({result:"PASS",actualOverviewHelpers:true,loaderWired:false,httpEndpointAdded:false,platformCalls:0}));
