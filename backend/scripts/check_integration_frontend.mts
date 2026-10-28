/** Real PlatformService consumes captured PG/HTTP context; never calls a platform. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { ApiClient } = require("../../frontend/src/services/api-client.ts");
const { PlatformService } = require("../../frontend/src/services/platform-service.ts");
const { queryBlock } = require("../../frontend/src/lib/platform-catalog.ts");
const trace = JSON.parse(readFileSync(process.argv[2], "utf8"));
const recordedClock = Date.parse(trace.context.checkedAt) + 1;
Date.now = () => recordedClock;
let fetches = 0;
function consumer(context: unknown) {
  return new PlatformService(new ApiClient("https://synthetic.invalid", async () => {
    fetches++;
    return Response.json({ schemaVersion: "command-job-v1.0", requestId: "synthetic-context", data: context });
  }), trace.identity);
}
const service = consumer(trace.context);
assert.deepEqual(await service.context("adq"), trace.context);
const account = trace.context.accounts[0], link = trace.context.links[0];
const scope = { tenantId: trace.identity.tenantId, businessEntityId: account.businessEntityId,
  shopId: link.shopId, provider: account.provider, productLine: "adq", accountId: account.id,
  start: "2026-10-01", end: "2026-10-07" };
assert.equal(queryBlock(trace.context, scope, "adgroups", trace.identity.permissions, recordedClock), "");
const write = trace.context.operations.find((operation: {kind: string}) => operation.kind === "WRITE");
assert.ok(write);
const before = fetches;
await assert.rejects(service.preview(trace.context, scope, { catalogId: write.catalogId,
  targetId: "synthetic-target", nativeId: "synthetic-native", expectedVersion: 1,
  changes: { budget: "100" }, reason: "synthetic verification" }),
  (error: {code: string}) => error.code === "FORBIDDEN");
assert.equal(fetches, before, "Unimplemented resource write must stop before HTTP");
const mutations = [
  { ...trace.context, source: "MOCK" }, { ...trace.context, tenantId: "another" },
  { ...trace.context, productLine: "shop" }, { ...trace.context, expiresAt: "2000-01-01T00:00:00Z" },
  { ...trace.context, accounts: [{ ...account, provider: "ocean" }] },
  { ...trace.context, links: [{ ...link, state: "ACTIVE" }] },
];
for (const context of mutations) {
  await assert.rejects(consumer(context).context("adq"), (error: {code:string}) => error.code === "INVALID_RESPONSE");
}
console.log(JSON.stringify({ result: "PASS", actualPlatformService: true, validContext: 1,
  scopedReadReady: true, unimplementedWriteBlockedBeforeHttp: true,
  invalidResponsesRejected: mutations.length, injectedFetches: fetches, realNetworkCalls: 0,
  clock: "RECORDED_HTTP_CHECKED_AT", browserAcceptance: "NOT_RUN", platformAcceptance: "NOT_RUN" }));
