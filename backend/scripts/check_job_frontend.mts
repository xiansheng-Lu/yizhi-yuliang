/** Read the real frontend validator with injected fetch; never sends network requests. */
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const { ApiClient }: typeof import("../../frontend/src/services/api-client.ts") =
  createRequire(import.meta.url)("../../frontend/src/services/api-client.ts");

const trace = JSON.parse(readFileSync(process.argv[2], "utf8"));
const sample = trace.job;
let requests = 0;
const input = {
  source: sample.source, shop_id: sample.shop_id, target_type: sample.target_type,
  target_id: sample.target_id, action_type: sample.action_type,
  new_value: sample.new_value, intent: sample.intent, idempotency_key: sample.idempotency_key,
};
async function consume(job: unknown) {
  const client = new ApiClient("https://synthetic.invalid", async (url) => {
    requests++;
    const data = String(url).endsWith("/v1/auth/csrf")
      ? { token: "synthetic-csrf", expiresAt: new Date(Date.now() + 60000).toISOString() }
      : job;
    return new Response(JSON.stringify({ schemaVersion: "command-job-v1.0", requestId: "synthetic-contract", data }),
      { status: 200, headers: { "Content-Type": "application/json" } });
  });
  return client.createJob(input);
}
assert.deepEqual(await consume(sample), sample);
const mutations = [
  { ...sample, risk_summary: { ...sample.risk_summary, source: "MOCK" } },
  { ...sample, status: "SUCCEEDED", execution_receipt: null },
  { ...sample, approval: null },
  { ...sample, history: null },
  { ...sample, new_value: { kind: "MONEY", currency: "USD", amount_minor: 200 } },
];
for (const changed of mutations) {
  await assert.rejects(consume(changed), (error: { code: string }) => error.code === "INVALID_RESPONSE");
}
console.log(JSON.stringify({ result: "PASS", actualFrontendApiClient: true, validJobs: 1,
  invalidResponsesRejected: mutations.length, injectedFetchCalls: requests, realNetworkCalls: 0,
  browserAcceptance: "NOT_RUN" }));
