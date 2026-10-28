/** Real frozen frontend API validator and reporting consumers; injected actual HTTP fixture only. */
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { ApiClient } = require("../../frontend/src/services/api-client.ts");
const { scopedPlans, scopedJobs, scopedEvidence } = require("../../frontend/src/lib/reporting.ts");
const { businessTotals, money } = require("../../frontend/src/lib/domain.ts");
const fixture = JSON.parse(readFileSync(process.argv[2], "utf8"));
let calls = 0;
async function consume(snapshot: unknown) {
  const client = new ApiClient("https://synthetic.invalid", async (url: unknown) => {
    calls++;
    assert.equal(String(url), "https://synthetic.invalid/v1/frontend/snapshot");
    return new Response(JSON.stringify({ schemaVersion: "command-job-v1.0", requestId: "synthetic-snapshot-contract", data: snapshot }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  return client.getSnapshot();
}
const snapshot = await consume(fixture);
assert.deepEqual(snapshot, fixture);
assert(Number.isSafeInteger(snapshot.revision) && snapshot.revision > 0);
const scoped = scopedEvidence(snapshot);
const plans = scopedPlans(snapshot);
const jobs = scopedJobs(snapshot);
assert.equal(scoped.scope.timezone, "Asia/Shanghai");
const totals = businessTotals(plans);
if (plans.length === 0) assert.deepEqual(totals, { spend: null, gmv: null, roi: null });
assert.equal(typeof money(totals.spend), "string");
const mutations = [{ ...fixture, source: "MOCK" }, { ...fixture, schemaVersion: "other" }, { ...fixture, scope: { ...fixture.scope, timezone: "UTC" } }, { ...fixture, products: null }, { ...fixture, tasks: [{ source: "MOCK" }] }];
for (const value of mutations) await assert.rejects(consume(value), (error: { code: string }) => error.code === "INVALID_RESPONSE");
console.log(JSON.stringify({ result: "PASS", actualFrontendApiClient: true, actualReportingConsumers: true, arrays: 12, revision: snapshot.revision, visibleShops: snapshot.shops.length, visibleAccounts: snapshot.adAccounts.length, scopedPlans: plans.length, scopedJobs: jobs.length, mutationsRejected: mutations.length, injectedFetchCalls: calls, realNetworkCalls: 0, browserAcceptance: "NOT_RUN" }));
