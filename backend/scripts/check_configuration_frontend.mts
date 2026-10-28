/** Consume actual PG/HTTP output through the unchanged frontend AdvertisingService. */
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { ApiClient } = require("../../frontend/src/services/api-client.ts");
const { AdvertisingService } = require("../../frontend/src/services/advertising-service.ts");
const { configurationContextKey, fieldNames, fieldStateNames } = require("../../frontend/src/lib/advertising.ts");
const trace = JSON.parse(readFileSync(process.argv[2], "utf8"));
// 归档按实际HTTP核验时刻回放；不延长响应期限，也不声称旧响应今天仍可用。
assert.ok(Date.parse(trace.capability.expiresAt) > Date.parse(trace.capability.checkedAt));
const capturedClock = Date.parse(trace.capability.checkedAt) + 1;
Date.now = () => capturedClock;
assert.equal(trace.capability.contextKey, configurationContextKey(trace.context));
assert.deepEqual(Object.keys(trace.capability.fields).sort(), Object.keys(fieldNames).sort());
let requests = 0;
function consumer(catalog: unknown, capability: unknown) {
  const api = new ApiClient("https://synthetic.invalid", async (url: string) => {
    requests++;
    const data = String(url).includes("/catalog?") ? catalog : capability;
    return new Response(JSON.stringify({ schemaVersion: "command-job-v1.0", requestId: "synthetic-context", data }),
      { status: 200, headers: { "Content-Type": "application/json" } });
  });
  return new AdvertisingService(api, trace.identity);
}
const service = consumer(trace.catalog, trace.capability);
assert.deepEqual(await service.catalog(trace.context.product), trace.catalog);
assert.deepEqual(await service.capabilities(trace.context), trace.capability);
for (const state of Object.keys(fieldStateNames)) {
  const capability = { ...trace.capability, fields: { ...trace.capability.fields,
    budget: { state, reason: "explicit synthetic state contract", valueType: "INTEGER" } } };
  assert.equal((await consumer(trace.catalog, capability).capabilities(trace.context)).fields.budget.state, state);
}
const mutations = [
  { ...trace.capability, contextKey: "different-context" },
  { ...trace.capability, tenantId: "another-tenant" },
  { ...trace.capability, source: "MOCK" },
  { ...trace.capability, expiresAt: "2000-01-01T00:00:00Z" },
  { ...trace.capability, fields: { ...trace.capability.fields, budget: { state: "CONFIRMED", reason: "invalid seventh state" } } },
  { ...trace.capability, fields: { ...trace.capability.fields, budget: { state: "AVAILABLE", reason: "bad unit", unit: "CNY" } } },
];
for (const capability of mutations) {
  await assert.rejects(consumer(trace.catalog, capability).capabilities(trace.context),
    (error: { code: string }) => error.code === "INVALID_RESPONSE");
}
console.log(JSON.stringify({ result: "PASS", actualAdvertisingService: true, validCatalogs: 1,
  validCapabilities: 1, fields: Object.keys(fieldNames).length, fieldStates: Object.keys(fieldStateNames).length,
  invalidResponsesRejected: mutations.length, injectedFetchCalls: requests, realNetworkCalls: 0,
  clock: "RECORDED_HTTP_CHECKED_AT", browserAcceptance: "NOT_RUN", platformAcceptance: "NOT_RUN" }));
