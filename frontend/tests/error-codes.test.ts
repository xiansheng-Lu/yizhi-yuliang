// A08 验收：四类错误码的页面文案、字段级 details、重新核对提示与 Retry-After 生效。
import test from "node:test";
import assert from "node:assert/strict";
import { ApiClient } from "../src/services/api-client";
import { ServiceError } from "../src/services/contracts";

const base = "https://backend.example";
const envelope = (data: unknown) =>
  Response.json({ data, requestId: "ok", schemaVersion: "command-job-v1.0" });
const csrf = () =>
  envelope({ token: "token-1", expiresAt: new Date(Date.now() + 600000).toISOString() });
const path = (url: RequestInfo | URL) => new URL(String(url)).pathname;

/** 与后端 platform/errors.failure() 相同结构的受控错误信封。 */
const failure = (status: number, code: string, message: string, details?: Record<string, string>[], retryAfter?: string) =>
  new Response(
    JSON.stringify({ requestId: "req-1", error: { code, message, details: details ?? [], retryable: false } }),
    {
      status,
      headers: {
        "Content-Type": "application/json",
        ...(retryAfter ? { "Retry-After": retryAfter } : {}),
      },
    },
  );

/** 一次写请求的调用计数，供断言客户端从不自动重发。 */
function writeClient(respond: () => Response | Promise<Response>) {
  const writes: string[] = [];
  const client = new ApiClient(base, async (url) => {
    if (path(url) === "/v1/auth/csrf") return csrf();
    writes.push(path(url));
    return respond();
  });
  return { client, writes };
}

async function capture(run: () => Promise<unknown>): Promise<ServiceError> {
  try {
    await run();
  } catch (error) {
    assert.ok(error instanceof ServiceError);
    return error;
  }
  throw new Error("预期抛出 ServiceError，但调用成功了");
}

test("422 展示后端中文业务原因与字段级 details", async () => {
  const { client, writes } = writeClient(() =>
    failure(422, "INVALID_INPUT", "预算调整幅度超过服务端允许范围", [
      { field: "new_value", reason: "字段格式不正确" },
      { field: "risk_policy_version", reason: "字段格式不正确" },
    ]),
  );
  const error = await capture(() => client.deliveryRequest("/v1/delivery/change", "POST", { new_value: 999 }));
  assert.equal(error.code, "INVALID_INPUT");
  assert.equal(error.message, "预算调整幅度超过服务端允许范围");
  assert.deepEqual(error.details, [
    { field: "new_value", reason: "字段格式不正确" },
    { field: "risk_policy_version", reason: "字段格式不正确" },
  ]);
  assert.deepEqual(writes, ["/v1/delivery/change"]);
});

test("409 透传可重新核对的中文提示且不自动重发", async () => {
  const { client, writes } = writeClient(() =>
    failure(409, "CONFLICT", "任务已被其它会话更新，请重新核对当前版本", [
      { field: "job_id", reason: "版本已过期" },
    ]),
  );
  const error = await capture(() => client.deliveryRequest("/v1/delivery/change", "POST"));
  assert.equal(error.code, "CONFLICT");
  assert.equal(error.message, "任务已被其它会话更新，请重新核对当前版本");
  assert.deepEqual(writes, ["/v1/delivery/change"]);
});

test("423 映射为 LOCKED、读取 Retry-After 且写请求只发一次", async () => {
  const { client, writes } = writeClient(() =>
    failure(423, "LOCKED", "任务正在执行中，请稍后核对结果，勿重复提交", undefined, "15"),
  );
  const error = await capture(() => client.deliveryRequest("/v1/delivery/change", "POST"));
  assert.equal(error.code, "LOCKED");
  assert.equal(error.message, "任务正在执行中，请稍后核对结果，勿重复提交");
  assert.equal(error.retryAfterSeconds, 15);
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.deepEqual(writes, ["/v1/delivery/change"]);
});

test("429 保留 Retry-After 秒数与后端限流原因", async () => {
  const { client } = writeClient(() =>
    failure(429, "RATE_LIMITED", "登录尝试过于频繁，请稍后重试", undefined, "30"),
  );
  const error = await capture(() => client.deliveryRequest("/v1/delivery/change", "POST"));
  assert.equal(error.code, "RATE_LIMITED");
  assert.equal(error.message, "登录尝试过于频繁，请稍后重试");
  assert.equal(error.retryAfterSeconds, 30);
});

test("错误响应不是 JSON 时回落到客户端中文兜底文案", async () => {
  const { client } = writeClient(() => new Response("<html>bad gateway</html>", { status: 503 }));
  const error = await capture(() => client.deliveryRequest("/v1/delivery/change", "POST"));
  assert.equal(error.code, "NETWORK_ERROR");
  assert.equal(error.message, "服务暂时不可用，请稍后重试。");
  assert.equal(error.details, undefined);
});
