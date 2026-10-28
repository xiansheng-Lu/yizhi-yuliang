/** Real ApiAnalysisAdapter with actual admission output; no mock result generation. */
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { ApiClient } = require("../../frontend/src/services/api-client.ts");
const { ApiAnalysisAdapter } = require("../../frontend/src/services/analysis-service.ts");
const trace = JSON.parse(readFileSync(process.argv[2], "utf8"));
let fetches = 0;
const api = new ApiClient("https://synthetic.invalid", async (url: string) => {
  fetches++;
  const path = new URL(String(url)).pathname;
  const data = path.endsWith("/csrf")
    ? { token: "synthetic-csrf", expiresAt: new Date(Date.now()+60000).toISOString() }
    : path.endsWith("/results") ? trace.results : trace.task;
  return Response.json({ schemaVersion: "command-job-v1.0", requestId: "synthetic-analysis-contract", data });
});
// 这里只给查询验证器提供受理时合成授权目录项，不作为FrontendSnapshot交付。
const snapshot = { shops: [{ id: trace.input.query.shopId, tenantId: trace.identity.tenantId,
  connectorId: "wxshop" }], adAccounts: [{ shopId: trace.input.query.shopId,
  connectorId: trace.input.query.platformId }], products: [], plans: [], liveSessions: [] };
const adapter = new ApiAnalysisAdapter(api, snapshot);
const signal = new AbortController().signal;
assert.deepEqual(await adapter.createTask(trace.input.query, trace.input.idempotencyKey, signal), trace.task);
assert.deepEqual(await adapter.getTask(trace.task.id, signal), trace.task);
assert.deepEqual(await adapter.listResults(signal), []);
assert.equal(trace.task.status, "queued");
assert.equal(trace.task.progress, 0);
console.log(JSON.stringify({ result: "PASS", actualApiAnalysisAdapter: true, taskRestore: true,
  arrayResults: true, state: trace.task.status, progress: trace.task.progress,
  injectedFetches: fetches, realNetworkCalls: 0, browserAcceptance: "NOT_RUN",
  computedResults: 0, frontendSnapshotDelivery: false }));
