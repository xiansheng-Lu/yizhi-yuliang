/** Actual frozen V9 consumer, captured local PG/HTTP; no platform network. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { ApiClient } = require("../../frontend/src/services/api-client.ts");
const { PlatformService } = require("../../frontend/src/services/platform-service.ts");
const trace = JSON.parse(readFileSync(process.argv[2], "utf8"));
Date.now = () => Date.parse(trace.context.checkedAt) + 1;
let requests = 0;
const client = new ApiClient("https://synthetic.invalid", async (url: string, init: RequestInit) => {
  requests++;
  if (String(url).includes("/auth/csrf")) return Response.json({ schemaVersion:"command-job-v1.0", requestId:"local", data:{token:"synthetic-csrf",expiresAt:new Date(Date.now()+60000).toISOString()} });
  assert.ok(String(url).includes("/integrations/queries"));
  assert.deepEqual(JSON.parse(String(init.body)), trace.input);
  return Response.json({schemaVersion:"command-job-v1.0",requestId:"local",data:trace.page});
});
const service = new PlatformService(client, trace.identity);
const page = await service.query(trace.context, trace.input.scope, trace.input.resource, trace.input.filters);
assert.deepEqual(page, trace.page);
assert.ok(page.rows.every((row: any) => row.metrics.spendMinor === 0 && row.metrics.roi === null));
assert.ok(page.rows.every((row: any) => row.updatedAt === "源更新时间未提供"));
assert.ok(Number.isFinite(Date.parse(page.syncedAt)));
console.log(JSON.stringify({result:"PASS",actualPlatformService:true,unknownDistinctFromZero:true,requests,realNetworkCalls:0,browserAcceptance:"NOT_RUN"}));
