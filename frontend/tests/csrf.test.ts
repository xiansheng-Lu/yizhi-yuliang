import test from "node:test";
import assert from "node:assert/strict";
import { ApiClient } from "../src/services/api-client";
import type { Identity } from "../src/lib/domain";

const base = "https://backend.example";
const actor: Identity = {
  userId: "csrf-user", displayName: "测试用户", email: "operator@example.com",
  tenantId: "csrf-tenant", shopIds: [], permissions: ["snapshot.read"],
  workspacePages: ["command", "overview"], mode: "API",
};
const login = { email: actor.email!, password: "test-password" };
const envelope = (data: unknown) => Response.json({ data, requestId: "csrf-request", schemaVersion: "command-job-v1.0" });
const csrf = (token = "test-csrf", expiresAt = new Date(Date.now() + 600000).toISOString()) => envelope({ token, expiresAt });
const path = (url: RequestInfo | URL) => new URL(String(url)).pathname;
const header = (options?: RequestInit) => new Headers(options?.headers).get("X-CSRF-Token");
const flush = () => new Promise<void>((resolve) => setImmediate(resolve));

test("default browser fetch is called without binding it to ApiClient", async () => {
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async function (this: unknown) {
    // Browsers reject a native fetch invoked with an ApiClient receiver.
    assert.ok(this === undefined || this === globalThis);
    calls++;
    return envelope(null);
  };
  try {
    assert.equal(await new ApiClient(base).getIdentity(), null);
    assert.equal(calls, 1);
  } finally {
    globalThis.fetch = original;
  }
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

test("password login bootstraps preauth then obtains a new session-bound CSRF for writes", async () => {
  const calls: { path: string; options?: RequestInit }[] = [];
  let bootstraps = 0;
  const client = new ApiClient(`${base}/`, async (url, options) => {
    calls.push({ path: path(url), options });
    if (path(url) === "/v1/auth/csrf") return csrf(`token-${++bootstraps}`);
    if (path(url) === "/v1/auth/login") {
      assert.equal(header(options), "token-1");
      return envelope(actor);
    }
    assert.equal(header(options), "token-2");
    return envelope(actor);
  });
  assert.deepEqual(await client.login(login), actor);
  await client.updateProfile({ displayName: "更新" });
  assert.deepEqual(calls.map((call) => call.path), ["/v1/auth/csrf", "/v1/auth/login", "/v1/auth/csrf", "/v1/users/me"]);
  for (const call of calls) {
    assert.equal(call.options?.credentials, "include");
    assert.equal(call.options?.cache, "no-store");
  }
  assert.equal(header(calls[0].options), null);
  assert.equal(new Headers(calls[1].options?.headers).get("X-Yuliang-Client"), "web");
});

test("email challenge and code login share one preauth token without sending a real email", async () => {
  const seen: string[] = [];
  const challenge = { challengeId: "test-challenge", expiresAt: new Date(Date.now() + 300000).toISOString(), retryAfterSeconds: 60 };
  const client = new ApiClient(base, async (url, options) => {
    seen.push(path(url));
    if (path(url) === "/v1/auth/csrf") return csrf("shared-preauth");
    assert.equal(header(options), "shared-preauth");
    if (path(url).endsWith("/email-codes")) return envelope(challenge);
    assert.deepEqual(JSON.parse(String(options?.body)), { method: "email_code", email: actor.email, challengeId: "test-challenge", code: "123456" });
    return envelope(actor);
  });
  await client.requestEmailCode({ email: actor.email! });
  await client.login({ method: "email_code", email: actor.email!, challengeId: challenge.challengeId, code: "123456" });
  assert.deepEqual(seen, ["/v1/auth/csrf", "/v1/auth/email-codes", "/v1/auth/login"]);
});

test("focus checks returning no session preserve the preauth token bound to an issued email code", async () => {
  const seen: string[] = [];
  let bootstraps = 0;
  const challenge = { challengeId: "focus-challenge", expiresAt: new Date(Date.now() + 300000).toISOString(), retryAfterSeconds: 60 };
  const client = new ApiClient(base, async (url, options) => {
    seen.push(path(url));
    if (path(url) === "/v1/auth/csrf") return csrf(`preauth-${++bootstraps}`);
    if (path(url) === "/v1/auth/session") return envelope(null);
    assert.equal(header(options), "preauth-1", "the issued code must stay bound to its original preauth context");
    if (path(url) === "/v1/auth/email-codes") return envelope(challenge);
    assert.deepEqual(JSON.parse(String(options?.body)), { method: "email_code", email: actor.email, challengeId: challenge.challengeId, code: "123456" });
    return envelope(actor);
  });
  await client.requestEmailCode({ email: actor.email! });
  assert.equal(await client.getIdentity(), null);
  assert.equal(await client.getIdentity(), null);
  assert.deepEqual(await client.login({ method: "email_code", email: actor.email!, challengeId: challenge.challengeId, code: "123456" }), actor);
  assert.equal(bootstraps, 1);
  assert.deepEqual(seen, ["/v1/auth/csrf", "/v1/auth/email-codes", "/v1/auth/session", "/v1/auth/session", "/v1/auth/login"]);
});

test("an incorrect email code keeps its preauth token so the user can enter the correct code", async () => {
  let bootstraps = 0, notifications = 0;
  const codes: string[] = [];
  const challenge = { challengeId: "retry-code-challenge", expiresAt: new Date(Date.now() + 300000).toISOString(), retryAfterSeconds: 60 };
  const client = new ApiClient(base, async (url, options) => {
    if (path(url) === "/v1/auth/csrf") return csrf(`preauth-${++bootstraps}`);
    assert.equal(header(options), "preauth-1");
    if (path(url) === "/v1/auth/email-codes") return envelope(challenge);
    assert.equal(path(url), "/v1/auth/login");
    const body = JSON.parse(String(options?.body));
    assert.equal(body.challengeId, challenge.challengeId);
    codes.push(body.code);
    return body.code === "123456" ? envelope(actor) : new Response(null, { status: 401 });
  });
  client.subscribe(() => { notifications++; });
  await client.requestEmailCode({ email: actor.email! });
  await assert.rejects(client.login({ method: "email_code", email: actor.email!, challengeId: challenge.challengeId, code: "000000" }), { code: "UNAUTHENTICATED" });
  assert.equal(bootstraps, 1); assert.equal(notifications, 0);
  assert.deepEqual(await client.login({ method: "email_code", email: actor.email!, challengeId: challenge.challengeId, code: "123456" }), actor);
  assert.deepEqual(codes, ["000000", "123456"]);
  assert.equal(bootstraps, 1); assert.equal(notifications, 0);
});

test("an incorrect password does not discard preauth or broadcast session expiry", async () => {
  let bootstraps = 0, notifications = 0;
  const passwords: string[] = [];
  const client = new ApiClient(base, async (url, options) => {
    if (path(url) === "/v1/auth/csrf") return csrf(`preauth-${++bootstraps}`);
    assert.equal(path(url), "/v1/auth/login");
    assert.equal(header(options), "preauth-1");
    const body = JSON.parse(String(options?.body));
    passwords.push(body.password);
    return body.password === login.password ? envelope(actor) : new Response(null, { status: 401 });
  });
  client.subscribe(() => { notifications++; });
  await assert.rejects(client.login({ ...login, password: "incorrect-password" }), { code: "UNAUTHENTICATED" });
  assert.deepEqual(await client.login(login), actor);
  assert.deepEqual(passwords, ["incorrect-password", login.password]);
  assert.equal(bootstraps, 1); assert.equal(notifications, 0);
});

test("read methods do not fetch CSRF and all mutation methods attach the cached token", async () => {
  let bootstraps = 0;
  const seen: { method?: string; token: string | null; contentType: string | null; body?: BodyInit | null }[] = [];
  const client = new ApiClient(base, async (url, options) => {
    if (path(url) === "/v1/auth/csrf") { bootstraps++; return csrf(); }
    seen.push({ method: options?.method, token: header(options), contentType: new Headers(options?.headers).get("Content-Type"), body: options?.body });
    return envelope(null);
  });
  for (const method of ["GET", "HEAD", "OPTIONS"]) await client.deliveryRequest("/v1/delivery/probe", method);
  assert.equal(bootstraps, 0);
  assert.ok(seen.every((call) => call.token === null));
  for (const method of ["POST", "PATCH", "PUT", "DELETE"]) await client.deliveryRequest("/v1/delivery/probe", method, { value: method });
  const form = new FormData(); form.set("file", new Blob(["test-image"], { type: "image/png" }), "probe.png");
  await client.deliveryRequest("/v1/delivery/upload", "POST", form);
  assert.equal(bootstraps, 1);
  assert.ok(seen.slice(3).every((call) => call.token === "test-csrf"));
  assert.ok(seen.slice(3, 7).every((call) => call.contentType === "application/json"));
  assert.equal(seen.at(-1)?.contentType, null, "browser must supply multipart boundary");
  assert.equal(seen.at(-1)?.body, form);
});

test("concurrent mutations share one pending bootstrap and each business request is sent once", async () => {
  const pending = deferred<Response>();
  let bootstraps = 0;
  const writes: string[] = [];
  const client = new ApiClient(base, async (url, options) => {
    if (path(url) === "/v1/auth/csrf") { bootstraps++; return pending.promise; }
    assert.equal(header(options), "single-flight");
    writes.push(path(url)); return envelope(null);
  });
  const first = client.deliveryRequest("/v1/delivery/first", "POST");
  const second = client.deliveryRequest("/v1/delivery/second", "PATCH");
  await flush();
  assert.equal(bootstraps, 1); assert.deepEqual(writes, []);
  pending.resolve(csrf("single-flight"));
  await Promise.all([first, second]);
  assert.deepEqual(writes.sort(), ["/v1/delivery/first", "/v1/delivery/second"]);
});

test("one aborted waiter cannot cancel shared bootstrap or send its original mutation", async () => {
  const pending = deferred<Response>();
  const aborter = new AbortController();
  const reason = new Error("caller changed scope");
  const writes: string[] = [];
  let sharedSignal: AbortSignal | null | undefined;
  let bootstraps = 0;
  const client = new ApiClient(base, async (url, options) => {
    if (path(url) === "/v1/auth/csrf") { bootstraps++; sharedSignal = options?.signal; return pending.promise; }
    writes.push(path(url)); return envelope(null);
  });
  const first = client.deliveryRequest("/v1/delivery/aborted", "POST", {}, aborter.signal).then(() => "sent", (error: unknown) => error);
  const second = client.deliveryRequest("/v1/delivery/valid", "POST");
  await flush(); aborter.abort(reason); await flush();
  const bootstrapWasAborted = sharedSignal?.aborted;
  pending.resolve(csrf());
  assert.equal(await first, reason);
  await second;
  assert.equal(bootstrapWasAborted, false);
  assert.equal(bootstraps, 1);
  assert.deepEqual(writes, ["/v1/delivery/valid"]);
});

test("strict malformed or expired CSRF responses prevent the original mutation", async () => {
  const expiresAt = new Date(Date.now() + 600000).toISOString();
  const badData = [null, {}, { token: "", expiresAt }, { token: "two tokens", expiresAt }, { token: "line\nbreak", expiresAt }, { token: "x".repeat(513), expiresAt }, { token: 42, expiresAt }, { token: "valid", expiresAt: "not-a-date" }, { token: "valid", expiresAt: "2000-01-01T00:00:00Z" }];
  for (const data of badData) {
    let writes = 0;
    const client = new ApiClient(base, async (url) => {
      if (path(url) === "/v1/auth/csrf") return envelope(data);
      writes++; return envelope(null);
    });
    await assert.rejects(client.deliveryRequest("/v1/delivery/probe", "POST"), { code: "INVALID_RESPONSE" });
    assert.equal(writes, 0);
  }
  let writes = 0;
  const invalidEnvelope = new ApiClient(base, async (url) => {
    if (path(url) === "/v1/auth/csrf") return Response.json({ data: { token: "valid", expiresAt }, requestId: "bad-schema", schemaVersion: "other" });
    writes++; return envelope(null);
  });
  await assert.rejects(invalidEnvelope.deliveryRequest("/v1/delivery/probe", "POST"), { code: "INVALID_RESPONSE" });
  assert.equal(writes, 0);
});

test("512-character token is accepted and expiring cache is refreshed before the next write", async (t) => {
  let now = Date.now(); t.mock.method(Date, "now", () => now);
  let bootstraps = 0;
  const tokens: (string | null)[] = [];
  const client = new ApiClient(base, async (url, options) => {
    if (path(url) === "/v1/auth/csrf") return csrf(String(++bootstraps).repeat(512), new Date(now + 10000).toISOString());
    tokens.push(header(options)); return envelope(null);
  });
  await client.deliveryRequest("/v1/delivery/first", "POST");
  now += 11000;
  await client.deliveryRequest("/v1/delivery/second", "POST");
  assert.equal(bootstraps, 2);
  assert.deepEqual(tokens, ["1".repeat(512), "2".repeat(512)]);
});

test("403 clears the token but never replays the original write or refreshes in the background", async () => {
  const calls: string[] = [];
  let bootstraps = 0;
  const client = new ApiClient(base, async (url, options) => {
    calls.push(path(url));
    if (path(url) === "/v1/auth/csrf") return csrf(`token-${++bootstraps}`);
    if (calls.filter((call) => call === "/v1/delivery/change").length === 1) return new Response(null, { status: 403 });
    assert.equal(header(options), "token-2"); return envelope(null);
  });
  await assert.rejects(client.deliveryRequest("/v1/delivery/change", "POST", { budget: 100 }), { code: "FORBIDDEN" });
  await flush();
  assert.deepEqual(calls, ["/v1/auth/csrf", "/v1/delivery/change"]);
  await client.deliveryRequest("/v1/delivery/change", "POST", { budget: 200 });
  assert.deepEqual(calls, ["/v1/auth/csrf", "/v1/delivery/change", "/v1/auth/csrf", "/v1/delivery/change"]);
});

test("only the CSRF GET retries one conflict; conflicting mutations are never retried", async () => {
  let bootstraps = 0, writes = 0;
  const client = new ApiClient(base, async (url) => {
    if (path(url) === "/v1/auth/csrf") return ++bootstraps === 1 ? new Response(null, { status: 409 }) : csrf();
    writes++; return new Response(null, { status: 409 });
  });
  await assert.rejects(client.deliveryRequest("/v1/delivery/change", "POST"), { code: "CONFLICT" });
  assert.equal(bootstraps, 2); assert.equal(writes, 1);
  let rejectedBootstraps = 0;
  const conflicting = new ApiClient(base, async (url) => {
    assert.equal(path(url), "/v1/auth/csrf"); rejectedBootstraps++; return new Response(null, { status: 409 });
  });
  await assert.rejects(conflicting.deliveryRequest("/v1/delivery/change", "POST"), { code: "CONFLICT" });
  assert.equal(rejectedBootstraps, 2);
});

test("failed bootstrap releases single flight and preserves retry metadata for the next explicit attempt", async () => {
  let bootstraps = 0, writes = 0;
  const client = new ApiClient(base, async (url) => {
    if (path(url) === "/v1/auth/csrf") return ++bootstraps === 1 ? new Response(null, { status: 429, headers: { "Retry-After": "30" } }) : csrf();
    writes++; return envelope(null);
  });
  await assert.rejects(client.deliveryRequest("/v1/delivery/change", "POST"), { code: "RATE_LIMITED", retryAfterSeconds: 30 });
  assert.equal(writes, 0);
  await client.deliveryRequest("/v1/delivery/change", "POST");
  assert.equal(bootstraps, 2); assert.equal(writes, 1);
});

test("logout and a known signed-in owner losing its session clear cached CSRF", async () => {
  let bootstraps = 0;
  const tokens: (string | null)[] = [];
  const client = new ApiClient(base, async (url, options) => {
    if (path(url) === "/v1/auth/csrf") return csrf(`token-${++bootstraps}`);
    if (path(url) === "/v1/auth/session") return envelope(null);
    tokens.push(header(options));
    return envelope(path(url) === "/v1/auth/login" ? actor : null);
  });
  await client.deliveryRequest("/v1/delivery/warm", "POST");
  await client.logout();
  await client.login(login);
  await client.deliveryRequest("/v1/delivery/session", "POST");
  assert.equal(await client.getIdentity(), null);
  await client.deliveryRequest("/v1/delivery/after-session-null", "POST");
  assert.deepEqual(tokens, ["token-1", "token-1", "token-2", "token-3", "token-4"]);
});

test("session reads retain the same owner's token and refresh it when another tenant becomes current", async () => {
  let sessionActor = actor;
  let bootstraps = 0;
  const tokens: (string | null)[] = [];
  const client = new ApiClient(base, async (url, options) => {
    if (path(url) === "/v1/auth/csrf") return csrf(`token-${++bootstraps}`);
    if (path(url) === "/v1/auth/session") return envelope(sessionActor);
    tokens.push(header(options)); return envelope(null);
  });
  await client.deliveryRequest("/v1/delivery/before-owner-known", "POST");
  assert.deepEqual(await client.getIdentity(), actor);
  await client.deliveryRequest("/v1/delivery/current-owner", "POST");
  assert.deepEqual(await client.getIdentity(), actor);
  await client.deliveryRequest("/v1/delivery/same-owner", "POST");
  sessionActor = { ...actor, tenantId: "another-tenant" };
  assert.deepEqual(await client.getIdentity(), sessionActor);
  await client.deliveryRequest("/v1/delivery/another-owner", "POST");
  assert.equal(bootstraps, 3);
  assert.deepEqual(tokens, ["token-1", "token-2", "token-2", "token-3"]);
});

for (const status of [401, 403]) {
  test(`late ${status} from the previous identity does not invalidate the new token or notify its UI`, async () => {
    const late = deferred<Response>();
    let bootstraps = 0, invalidations = 0;
    const tokens: (string | null)[] = [];
    const client = new ApiClient(base, async (url, options) => {
      if (path(url) === "/v1/auth/csrf") return csrf(`token-${++bootstraps}`);
      if (path(url) === "/v1/delivery/old") return late.promise;
      if (path(url) === "/v1/auth/login") return envelope(actor);
      tokens.push(header(options)); return envelope(null);
    });
    client.subscribe(() => { invalidations++; });
    await client.deliveryRequest("/v1/delivery/warm", "POST");
    const stale = client.deliveryRequest("/v1/delivery/old", "POST").then(() => null, (error: unknown) => error);
    await flush();
    await client.login(login);
    await client.deliveryRequest("/v1/delivery/new", "POST");
    late.resolve(new Response(null, { status }));
    assert.equal((await stale as { code: string }).code, status === 401 ? "UNAUTHENTICATED" : "FORBIDDEN");
    await client.deliveryRequest("/v1/delivery/new-again", "POST");
    assert.equal(invalidations, 0); assert.equal(bootstraps, 2);
    assert.deepEqual(tokens, ["token-1", "token-2", "token-2"]);
  });
}

test("unauthenticated bootstrap notifies once even if a subscriber reads protected history", async () => {
  let writes = 0, notifications = 0;
  const refreshes: Promise<unknown>[] = [];
  const client = new ApiClient(base, async (url) => {
    if (path(url) !== "/v1/auth/csrf" && path(url) !== "/v1/workspace/history") writes++;
    return new Response(null, { status: 401 });
  });
  client.subscribe(() => { notifications++; refreshes.push(client.getHistory().catch(() => null)); });
  await assert.rejects(client.deliveryRequest("/v1/delivery/change", "POST"), { code: "UNAUTHENTICATED" });
  await Promise.all(refreshes);
  assert.equal(notifications, 1); assert.equal(writes, 0);
});

test("an old pending bootstrap cannot overwrite a new externally established session token", async () => {
  const oldBootstrap = deferred<Response>();
  let bootstraps = 0;
  const writes: { path: string; token: string | null }[] = [];
  const client = new ApiClient(base, async (url, options) => {
    if (path(url) === "/v1/auth/csrf") {
      bootstraps++;
      return bootstraps === 1 ? oldBootstrap.promise : csrf(`token-${bootstraps}`);
    }
    if (path(url) === "/v1/auth/session") return envelope(actor);
    writes.push({ path: path(url), token: header(options) });
    return envelope(path(url) === "/v1/auth/login" ? actor : null);
  });
  const stale = client.deliveryRequest("/v1/delivery/stale", "POST").then(() => null, (error: unknown) => error);
  await flush();
  assert.deepEqual(await client.getIdentity(), actor);
  await client.login(login);
  await client.deliveryRequest("/v1/delivery/current", "POST");
  oldBootstrap.resolve(csrf("old-token"));
  assert.equal((await stale as { code: string }).code, "FORBIDDEN");
  await client.deliveryRequest("/v1/delivery/current-again", "POST");
  assert.equal(bootstraps, 3);
  assert.deepEqual(writes, [
    { path: "/v1/auth/login", token: "token-2" },
    { path: "/v1/delivery/current", token: "token-3" },
    { path: "/v1/delivery/current-again", token: "token-3" },
  ]);
});

test("bootstrap and business write share one 15-second operation deadline", async (t) => {
  const deadlines: { milliseconds: number; controller: AbortController }[] = [];
  t.mock.method(AbortSignal, "timeout", (milliseconds: number) => {
    const controller = new AbortController(); deadlines.push({ milliseconds, controller }); return controller.signal;
  });
  const pending = deferred<Response>();
  let businessSignal: AbortSignal | null | undefined;
  const client = new ApiClient(base, async (url, options) => {
    if (path(url) === "/v1/auth/csrf") return pending.promise;
    businessSignal = options?.signal;
    assert.ok(businessSignal);
    return new Promise<Response>((_resolve, reject) => {
      businessSignal!.addEventListener("abort", () => reject(businessSignal!.reason), { once: true });
    });
  });
  const result = client.deliveryRequest("/v1/delivery/change", "POST").then(() => null, (error: unknown) => error);
  await flush();
  const timersBeforeBusiness = deadlines.length;
  assert.ok(timersBeforeBusiness >= 1);
  pending.resolve(csrf());
  await flush();
  assert.equal(deadlines.length, timersBeforeBusiness, "business dispatch must not restart a fresh timeout after CSRF acquisition");
  assert.equal(deadlines[0].milliseconds, 15000);
  assert.equal(businessSignal, deadlines[0].controller.signal);
  deadlines[0].controller.abort(new DOMException("operation budget exhausted", "TimeoutError"));
  assert.equal((await result as { code: string }).code, "TIMEOUT");
});
