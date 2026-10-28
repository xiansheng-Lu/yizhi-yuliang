import test from "node:test";
import assert from "node:assert/strict";
import { MockAdapter } from "../src/services/mock-adapter";
import { MockHistoryStore, historyKey } from "../src/services/mock-history";
import { CsrfApiClient as ApiClient } from "./helpers/csrf-fetch";
import { taskProjectId, type SendMessageRequest } from "../src/lib/workspace-history";
import type { Identity } from "../src/lib/domain";

function storage() {
  const state = new Map<string, string>();
  return { getItem: (key: string) => state.get(key) ?? null, setItem: (key: string, value: string) => { state.set(key, value); }, removeItem: (key: string) => { state.delete(key); } };
}
async function fixture() {
  const shared = storage();
  const pc = new MockAdapter(shared);
  await pc.login({ email: "demo@yuliang.local", password: "demo123" });
  const snapshot = await pc.getSnapshot();
  const message: SendMessageRequest = { text: "看看现在直播间情况", scope: snapshot.scope, source: "PC_MANUAL", idempotencyKey: "test-query" };
  return { shared, pc, snapshot, message };
}

test("conversations and projects survive reload and are shared by PC and mobile", async () => {
  const { shared, pc, message } = await fixture();
  const project = await pc.createProject({ name: "  秋茶运营  " });
  const turn = await pc.sendMessage({ ...message, projectId: project.id });
  assert.equal(turn.interpretation.kind, "QUERY");
  assert.equal(turn.conversation.messages.length, 2);
  assert.equal(turn.conversation.messages[0].text, message.text);
  const mobile = new MockAdapter(shared);
  const second = await mobile.sendMessage({ ...message, text: "策划一份秋茶短视频内容", conversationId: turn.conversation.id, source: "MOBILE_COMMAND", idempotencyKey: "test-mobile" });
  assert.equal(second.interpretation.kind, "UNRECOGNIZED");
  const restored = await new MockAdapter(shared).getHistory();
  assert.equal(restored.projects[0].name, "秋茶运营");
  assert.equal(restored.conversations.length, 1);
  assert.equal(restored.conversations[0].projectId, project.id);
  assert.deepEqual(restored.conversations[0].messages.map((message) => message.role), ["user", "assistant", "user", "assistant"]);
});

test("send retries are idempotent and changed content cannot reuse a message key", async () => {
  const { pc, message } = await fixture();
  const first = await pc.sendMessage(message);
  const repeated = await pc.sendMessage(message);
  assert.equal(repeated.conversation.id, first.conversation.id);
  assert.equal(repeated.conversation.messages.length, 2);
  const reordered = await pc.sendMessage({ ...message, scope: { timezone: message.scope.timezone, date: message.scope.date, connector_id: message.scope.connector_id, live_session_id: message.scope.live_session_id, shop_id: message.scope.shop_id } });
  assert.equal(reordered.messageId, first.messageId);
  await pc.sendMessage({ ...message, text: "继续整理记录", conversationId: first.conversation.id, idempotencyKey: "test-followup" });
  const replay = await pc.sendMessage(message);
  assert.equal(replay.messageId, first.messageId);
  assert.equal(replay.conversation.messages.length, 4);
  await assert.rejects(pc.sendMessage({ ...message, text: "暂停千川A计划广告" }), { code: "CONFLICT" });
  assert.equal((await pc.getHistory()).conversations.length, 1);
  await assert.rejects(pc.sendMessage({ ...message, idempotencyKey: "bad-source", source: "AUTOPILOT" }), { code: "FORBIDDEN" });
});

test("conversation tasks inherit project grouping without altering approval or execution state", async () => {
  const { pc, snapshot, message } = await fixture();
  const project = await pc.createProject({ name: "新品投放" });
  const turn = await pc.sendMessage({ ...message, text: "把千川A计划日预算调到5000", projectId: project.id });
  const action = turn.interpretation;
  assert.equal(action.kind, "ACTION");
  if (action.kind !== "ACTION") throw new Error("Expected action");
  const job = await pc.createJob({ source: "PC_MANUAL", shop_id: "shop-tea", target_id: action.candidate_ids[0], target_type: action.target_type, action_type: action.action_type, new_value: action.new_value, intent: turn.conversation.title, idempotency_key: "test-project-task" });
  const lastReply = turn.conversation.messages.at(-1)!;
  await pc.attachConversationJob({ conversationId: turn.conversation.id, messageId: lastReply.id, jobId: job.job_id });
  assert.equal(taskProjectId(await pc.getHistory(), job.job_id), project.id);
  const before = (await pc.getSnapshot()).tasks.find((task) => task.job_id === job.job_id)!;
  const secondProject = await pc.createProject({ name: "下周运营" });
  await pc.moveConversation({ conversationId: turn.conversation.id, projectId: secondProject.id });
  assert.equal(taskProjectId(await pc.getHistory(), job.job_id), secondProject.id);
  await pc.assignTaskProject({ jobId: job.job_id, projectId: null });
  assert.equal(taskProjectId(await pc.getHistory(), job.job_id), null);
  const after = (await pc.getSnapshot()).tasks.find((task) => task.job_id === job.job_id)!;
  assert.equal(after.status, before.status);
  assert.equal(after.version, before.version);
  assert.deepEqual(after.history, before.history);
  assert.equal((await pc.getSnapshot()).plans.find((plan) => plan.id === action.candidate_ids[0])!.budgetMinor, snapshot.plans.find((plan) => plan.id === action.candidate_ids[0])!.budgetMinor);
});

test("project and task references are validated and cross-account history stays isolated", async () => {
  const { shared, pc, message } = await fixture();
  const turn = await pc.sendMessage(message);
  await assert.rejects(pc.createProject({ name: " " }), { code: "INVALID_INPUT" });
  await pc.createProject({ name: "已有项目" });
  await assert.rejects(pc.createProject({ name: "已有项目" }), { code: "CONFLICT" });
  await assert.rejects(pc.moveConversation({ conversationId: turn.conversation.id, projectId: "not-a-project" }), { code: "NO_DATA" });
  await assert.rejects(pc.assignTaskProject({ jobId: "not-a-job", projectId: null }), { code: "NO_DATA" });
  await assert.rejects(pc.attachConversationJob({ conversationId: turn.conversation.id, messageId: turn.conversation.messages[1].id, jobId: "not-a-job" }), { code: "NO_DATA" });
  const actor = (await pc.getIdentity())!;
  const store = new MockHistoryStore(shared, () => {}, () => new Date());
  const another: Identity = { ...actor, userId: "another-user" };
  assert.equal(store.read(another).conversations.length, 0);
  assert.throws(() => store.move(another, { conversationId: turn.conversation.id, projectId: null }), { code: "NO_DATA" });
});

test("corrupt or unwritable conversation storage never reports a false save", async () => {
  const { shared, pc, message } = await fixture();
  const actor = (await pc.getIdentity())!;
  shared.setItem(historyKey(actor), "invalid-json");
  await assert.rejects(pc.getHistory(), { code: "INVALID_RESPONSE" });
  assert.equal(shared.getItem(historyKey(actor)), "invalid-json");
  shared.removeItem(historyKey(actor));
  const unwritable = new MockHistoryStore({ ...shared, setItem() { throw new Error("full"); } }, () => {}, () => new Date());
  assert.throws(() => unwritable.send(actor, message, { kind: "UNRECOGNIZED", text: "未连接 AI" }), { code: "CONFLICT" });
  assert.equal(shared.getItem(historyKey(actor)), null);
});

test("history API uses authenticated requests and refuses malformed archived actions", async () => {
  const calls: { url: string; options: RequestInit }[] = [];
  const client = new ApiClient("https://backend.example", (async (url, options) => {
    calls.push({ url: String(url), options: options! });
    return Response.json({ data: { projects: [], conversations: [], taskProjects: {} }, requestId: "test", schemaVersion: "command-job-v1.0" });
  }) as typeof fetch);
  assert.equal((await client.getHistory()).conversations.length, 0);
  assert.equal(calls[0].url, "https://backend.example/v1/workspace/history");
  assert.equal(calls[0].options.credentials, "include");
  const invalid = new ApiClient("https://backend.example", (async () => Response.json({ data: { projects: [], conversations: [{ id: "chat", title: "test", projectId: null, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), messages: [{ id: "msg", role: "assistant", text: "test", createdAt: new Date().toISOString(), interpretation: { kind: "ACTION" } }] }], taskProjects: {} }, requestId: "test", schemaVersion: "command-job-v1.0" })) as typeof fetch);
  await assert.rejects(invalid.getHistory(), { code: "INVALID_RESPONSE" });
});
