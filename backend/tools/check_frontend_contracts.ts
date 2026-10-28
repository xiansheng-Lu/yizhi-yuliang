/** 使用当前真实ApiClient验证黄金样例；只验证协议，不调用真实平台。 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { ApiClient } from "../../frontend/src/services/api-client";
import { DeliveryService } from "../../frontend/src/services/delivery-service";
import { AdvertisingService } from "../../frontend/src/services/advertising-service";
import { ApiAnalysisAdapter } from "../../frontend/src/services/analysis-service";
import { configurationContextKey, type ConfigurationContext } from "../../frontend/src/lib/advertising";
import type { CommandJob } from "../../frontend/src/lib/command-job";

const golden = JSON.parse(readFileSync(new URL("../../docs/backend/contracts/golden.json", import.meta.url), "utf8"));
let checks = 0;
function csrfResponse(url: RequestInfo | URL): Response | undefined {
  if (new URL(String(url)).pathname !== "/v1/auth/csrf") return undefined;
  return Response.json({
    schemaVersion: "command-job-v1.0", requestId: "golden-csrf",
    data: { token: "golden-csrf-token", expiresAt: new Date(Date.now() + 600000).toISOString() },
  });
}
function client(data: unknown, outerVersion = "command-job-v1.0") {
  return new ApiClient("https://api.example.invalid", async (url) => csrfResponse(url) ?? new Response(
    JSON.stringify({ schemaVersion: outerVersion, requestId: "golden-request", data }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  ));
}
function recording(data: unknown, status = 200) {
  const requests: { path: string; options?: RequestInit }[] = [];
  const service = new ApiClient("https://api.example.invalid", async (url, options) => {
    const csrf = csrfResponse(url);
    if (csrf) return csrf;
    requests.push({ path: new URL(String(url)).pathname, options });
    return new Response(JSON.stringify({ schemaVersion: "command-job-v1.0", requestId: "golden", data }),
      { status, headers: { "Content-Type": "application/json" } });
  });
  return { service, requests };
}
async function run() {
  assert.deepEqual(await client(golden.identity).getIdentity(), golden.identity); checks++;
  assert.equal(await client(null).getIdentity(), null); checks++;
  assert.deepEqual(await client(golden.emptySnapshot).getSnapshot(), golden.emptySnapshot); checks++;
  assert.equal(await client(null).logout(), undefined); checks++;
  await assert.rejects(client(golden.identity, "delivery-v8.0").getIdentity()); checks++;
  await assert.rejects(client({ ...golden.identity, workspacePages: ["delivery"] }).getIdentity()); checks++;
  for (const [key, value] of Object.entries(golden.emptySnapshot)) {
    if (!Array.isArray(value)) continue;
    await assert.rejects(client({ ...golden.emptySnapshot, [key]: { items: [] } }).getSnapshot()); checks++;
  }
  await assert.rejects(client({ ...golden.emptySnapshot, source: "MOCK" }).getSnapshot()); checks++;
  await assert.rejects(client({ ...golden.emptySnapshot, scope: { timezone: "UTC" } }).getSnapshot()); checks++;
  const logout204 = new ApiClient("https://api.example.invalid", async (url) => csrfResponse(url) ?? new Response(null, { status: 204 }));
  await assert.rejects(logout204.logout()); checks++;
  const v1 = recording(golden.job);
  await v1.service.reviewJob(golden.approval);
  assert.equal(v1.requests[0].path, `/v1/command-jobs/${golden.approval.job_id}/approvals`);
  assert.deepEqual(JSON.parse(String(v1.requests[0].options?.body)), golden.approval);
  assert.equal("comment" in JSON.parse(String(v1.requests[0].options?.body)), false); checks++;
  const validCreate = { source: "PC_MANUAL" as const, shop_id: "shop_contract", target_type: "AD_PLAN" as const,
    target_id: "plan_contract", action_type: "CHANGE_DAILY_BUDGET" as const,
    new_value: { kind: "MONEY" as const, currency: "CNY" as const, amount_minor: 120000 },
    intent: "契约样例，未执行真实广告写入", idempotency_key: "contract-key-001" };
  assert.throws(() => v1.service.createJob({ ...validCreate, source: "AUTOPILOT" })); checks++;
  assert.throws(() => v1.service.createJob({ ...validCreate,
    new_value: { ...validCreate.new_value, amount_minor: Number.MAX_SAFE_INTEGER + 1 } })); checks++;
  await assert.rejects(client({ ...golden.job, status: "SUCCEEDED" }).createJob(validCreate)); checks++;
  const succeeded = { ...golden.job, status: "SUCCEEDED", execution_receipt: golden.receipt };
  assert.equal((await client(succeeded).createJob(validCreate)).status, "SUCCEEDED"); checks++;
  await assert.rejects(client({ ...succeeded, execution_receipt: { ...golden.receipt, verification: "UNKNOWN" } }).createJob(validCreate)); checks++;
  const unknown: CommandJob = { ...golden.job, status: "EXECUTING", execution_receipt: {
    ...golden.receipt, accepted: null, verification: "UNKNOWN", verified_at: null } };
  assert.equal((await client(unknown).createJob(validCreate)).status, "EXECUTING"); checks++;

  const v8 = recording(golden.v8Preview);
  assert.deepEqual(await new DeliveryService(v8.service).preview(golden.v8Request), golden.v8Preview);
  assert.equal(v8.requests[0].path, "/v1/delivery/plan-changes/preview");
  assert.deepEqual(JSON.parse(String(v8.requests[0].options?.body)), golden.v8Request); checks++;
  await assert.rejects(new DeliveryService(client({ ...golden.v8Preview, expectedVersion: 999 })).preview(golden.v8Request)); checks++;
  await assert.rejects(new DeliveryService(client({ ...golden.v8Preview, checks: [] })).preview(golden.v8Request)); checks++;

  const context: ConfigurationContext = golden.configurationContext;
  const contextKey = configurationContextKey(context);
  const v9Base = { contractVersion: "advertising-v9.0", source: "API", tenantId: golden.identity.tenantId, contextKey };
  const resourceResponse = { ...v9Base, resource: golden.selectedResource };
  const v9 = recording(resourceResponse);
  const service9 = new AdvertisingService(v9.service, golden.identity);
  assert.deepEqual(await service9.uploadCreative(context, new File(["contract-only"], "contract.mp4")), golden.selectedResource);
  assert.equal(v9.requests[0].path, "/v1/delivery/configuration/materials"); checks++;
  await assert.rejects(new AdvertisingService(recording({ ...v9Base, taskId: "pending", resource: null }, 202).service,
    golden.identity).uploadCreative(context, new File(["contract-only"], "contract.mp4"))); checks++;
  const lookup = { ...v9Base, kind: "creatives" as const, items: [golden.selectedResource], nextCursor: null, reason: "契约样例" };
  assert.deepEqual(await new AdvertisingService(client(lookup), golden.identity).lookup(context, "creatives"), lookup); checks++;
  await assert.rejects(new AdvertisingService(client({ ...lookup, tenantId: "another-tenant" }), golden.identity).lookup(context, "creatives")); checks++;
  await assert.rejects(new AdvertisingService(client({ ...lookup, contextKey: "wrong-account" }), golden.identity).lookup(context, "creatives")); checks++;
  const ticket = { idempotencyKey: "contract-task-key", draftId: "draft_contract", product: context.product,
    accountId: context.accountId, contextKey, createdAt: "2026-10-26T00:00:00Z" };
  const task = { ...v9Base, taskId: "configuration_contract", idempotencyKey: ticket.idempotencyKey,
    state: "SUCCEEDED", businessId: "platform_contract_id", platformStatus: "ACCEPTED", verification: "MATCHED",
    approvalId: "approval_contract", version: 1, cancellable: false, message: "契约样例" };
  assert.equal((await new AdvertisingService(client(task), golden.identity).result(ticket)).verification, "MATCHED"); checks++;
  await assert.rejects(new AdvertisingService(client({ ...task, verification: "UNKNOWN" }), golden.identity).result(ticket)); checks++;
  await assert.rejects(new AdvertisingService(client({ ...task, businessId: null }), golden.identity).result(ticket)); checks++;

  const snapshot = { ...golden.emptySnapshot, shops: [{ id: "shop_contract", tenantId: "1", name: "契约店铺", connectorId: "wxshop" }] };
  const analysis = recording(golden.analysisTask);
  const adapter = new ApiAnalysisAdapter(analysis.service, snapshot);
  const signal = new AbortController().signal;
  assert.equal((await adapter.getTask("analysis_contract", signal)).status, "queued");
  assert.equal(analysis.requests[0].path, "/v1/business-analysis/tasks/analysis_contract"); checks++;
  await assert.rejects(new ApiAnalysisAdapter(client({ ...golden.analysisTask, status: "QUEUED" }), snapshot).getTask("analysis_contract", signal)); checks++;
  await assert.rejects(new ApiAnalysisAdapter(client({ ...golden.analysisTask, status: "succeeded" }), snapshot).getTask("analysis_contract", signal)); checks++;
  assert.deepEqual(await new ApiAnalysisAdapter(client([]), snapshot).listResults(signal), []); checks++;
  await assert.rejects(new ApiAnalysisAdapter(client({ items: [] }), snapshot).listResults(signal)); checks++;
  const conflict = new ApiClient("https://api.example.invalid", async (url) => csrfResponse(url) ?? new Response(JSON.stringify({
    requestId: "golden", error: { code: "VERSION_CONFLICT", message: "样例", details: [{ current: 2 }] },
  }), { status: 409 }));
  await assert.rejects(conflict.logout(), error => Boolean(error && typeof error === "object" && "code" in error && error.code === "CONFLICT")); checks++;
  console.log(JSON.stringify({ result: "PASS", checks, scope: "真实前端校验器+黄金样例，非后端联调/真实平台验收" }));
}
run().catch(error => { console.error(error); process.exitCode = 1; });
