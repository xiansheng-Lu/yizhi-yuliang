import test from "node:test";
import assert from "node:assert/strict";
import { ApiClient } from "../src/services/api-client";
import { canAccessPage, authorizedLoginTarget } from "../src/lib/access";
import type { Identity } from "../src/lib/domain";

const actor: Identity = {
  userId: "user-test", displayName: "测试用户", email: "operator@example.com",
  tenantId: "tenant-test", shopIds: ["shop-test"], permissions: ["snapshot.read"],
  workspacePages: ["command", "overview"], mode: "API",
};
const success = (data: unknown) => Response.json({ data, requestId: "test-request", schemaVersion: "command-job-v1.0" });

test("email-code login uses the issued challenge and cookie session without a password", async () => {
  const calls: { path: string; options: RequestInit }[] = [];
  const challenge = { challengeId: "challenge-test", expiresAt: new Date(Date.now() + 300000).toISOString(), retryAfterSeconds: 60 };
  const client = new ApiClient("https://backend.example", (async (url, options) => {
    calls.push({ path: String(url), options: options! });
    return success(String(url).endsWith("/email-codes") ? challenge : actor);
  }) as typeof fetch);
  assert.deepEqual(await client.requestEmailCode({ email: " Operator@Example.com " }), challenge);
  await client.login({ method: "email_code", email: "Operator@Example.com", challengeId: challenge.challengeId, code: "123456" });
  assert.deepEqual(await client.getIdentity(), actor);
  assert.equal(calls[0].path, "https://backend.example/v1/auth/email-codes");
  assert.deepEqual(JSON.parse(String(calls[0].options.body)), { email: "operator@example.com", purpose: "LOGIN" });
  assert.deepEqual(JSON.parse(String(calls[1].options.body)), { method: "email_code", email: "operator@example.com", challengeId: "challenge-test", code: "123456" });
  assert.ok(calls.every((call) => call.options.credentials === "include" && call.options.cache === "no-store"));
  assert.equal(new Headers(calls[1].options.headers).get("X-Yuliang-Client"), "web");
});

test("email-password login remains available and rejects malformed identity responses", async () => {
  let payload: unknown;
  const client = new ApiClient("https://backend.example", (async (_url, options) => {
    payload = JSON.parse(String(options?.body)); return success(actor);
  }) as typeof fetch);
  await client.login({ method: "password", email: "OPERATOR@example.com", password: "test-password" });
  assert.deepEqual(payload, { method: "password", email: "operator@example.com", password: "test-password" });
  for (const invalid of [{ ...actor, mode: "MOCK" }, { ...actor, workspacePages: undefined }, { ...actor, workspacePages: ["settings"] }, { ...actor, userId: 12 }]) {
    const invalidClient = new ApiClient("https://backend.example", (async () => success(invalid)) as typeof fetch);
    await assert.rejects(invalidClient.login({ email: "operator@example.com", password: "test-password" }), { code: "INVALID_RESPONSE" });
  }
});

test("invalid credentials never send a request and invalid code metadata cannot simulate sending", async () => {
  let calls = 0;
  const client = new ApiClient("https://backend.example", (async () => { calls++; return success({}); }) as typeof fetch);
  await assert.rejects(client.requestEmailCode({ email: "invalid-email" }), { code: "INVALID_INPUT" });
  await assert.rejects(client.login({ method: "email_code", email: actor.email!, code: "123", challengeId: "test" }), { code: "INVALID_INPUT" });
  await assert.rejects(client.login({ email: actor.email!, password: "" }), { code: "INVALID_INPUT" });
  assert.equal(calls, 0);
  await assert.rejects(client.requestEmailCode({ email: actor.email! }), { code: "INVALID_RESPONSE" });
});

test("rate limiting preserves server retry interval and expired sessions invalidate the UI", async () => {
  const limited = new ApiClient("https://backend.example", (async () => new Response(null, { status: 429, headers: { "Retry-After": "120" } })) as typeof fetch);
  await assert.rejects(limited.requestEmailCode({ email: actor.email! }), { code: "RATE_LIMITED", retryAfterSeconds: 120 });
  const expired = new ApiClient("https://backend.example", (async () => new Response(null, { status: 401 })) as typeof fetch);
  let invalidations = 0;
  const unsubscribe = expired.subscribe(() => invalidations++);
  await assert.rejects(expired.updateProfile({ displayName: "新昵称" }), { code: "UNAUTHENTICATED" });
  assert.equal(invalidations, 1);
  assert.equal(await expired.getIdentity(), null);
  assert.equal(invalidations, 1);
  unsubscribe();
  await assert.rejects(expired.getSnapshot(), { code: "UNAUTHENTICATED" });
  assert.equal(invalidations, 1);
  const cascading = new ApiClient("https://backend.example", (async () => new Response(null, { status: 401 })) as typeof fetch);
  const refreshes: Promise<unknown>[] = [];
  cascading.subscribe(() => { refreshes.push(cascading.getHistory().catch(() => null)); });
  await assert.rejects(cascading.getSnapshot(), { code: "UNAUTHENTICATED" });
  await Promise.all(refreshes);
  assert.equal(refreshes.length, 1, "session invalidation must not recursively trigger history reloads");
});

test("profile updates only submit editable fields, never workspace or role grants", async () => {
  let submitted: unknown;
  const client = new ApiClient("https://backend.example", (async (url, options) => {
    assert.equal(String(url), "https://backend.example/v1/users/me");
    assert.equal(options?.method, "PATCH");
    submitted = JSON.parse(String(options?.body)); return success({ ...actor, displayName: "新昵称" });
  }) as typeof fetch);
  await client.updateProfile({ displayName: "  新昵称  ", workspacePages: ["settings"] } as { displayName: string });
  assert.deepEqual(submitted, { displayName: "新昵称" });
  await assert.rejects(client.updateProfile({ displayName: " " }), { code: "INVALID_INPUT" });
});

test("customer access follows explicit backend grants while account recovery stays available", () => {
  assert.equal(canAccessPage(null, "command"), false);
  assert.equal(canAccessPage(actor, "command"), true);
  assert.equal(canAccessPage(actor, "plans"), false);
  for (const id of ["settings", "agents", "connections"] as const) assert.equal(canAccessPage({ ...actor, workspacePages: [id] }, id), false);
  const pending = { ...actor, workspacePages: [] };
  assert.equal(canAccessPage(pending, "account"), true);
  assert.equal(canAccessPage(pending, "command"), false);
  assert.equal(authorizedLoginTarget(actor, "/app/overview"), "/app/overview");
  assert.equal(authorizedLoginTarget(actor, "/app/plans"), "/app/account");
  assert.equal(authorizedLoginTarget(pending, "/mobile"), "/app/account");
  assert.equal(authorizedLoginTarget(actor, "https://example.com"), "/app/account");
});
