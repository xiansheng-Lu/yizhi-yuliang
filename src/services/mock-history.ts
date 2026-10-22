import type { Identity } from "@/lib/domain";
import type { CommandInterpretation } from "@/lib/command-job";
import { emptyHistory, scopeFingerprint, type Conversation, type HistorySnapshot, type SendMessageRequest } from "@/lib/workspace-history";
import type { MockStorage } from "./mock-identity";
import { ServiceError } from "./contracts";

interface StoredHistory extends HistorySnapshot { requests: Record<string, { conversationId: string; messageId: string; signature: string }>; }
export const historyKey = (actor: Identity) => `yuliang:history:v1:${actor.tenantId}:${actor.userId}`;

export class MockHistoryStore {
  constructor(private storage: MockStorage, private notify: () => void, private clock: () => Date) {}
  read(actor: Identity): StoredHistory {
    const raw = this.storage.getItem(historyKey(actor));
    if (!raw) return { ...emptyHistory(), requests: {} };
    try {
      const state = JSON.parse(raw) as StoredHistory;
      if (!Array.isArray(state.projects) || !Array.isArray(state.conversations) || !state.taskProjects || !state.requests) throw new Error();
      return state;
    } catch { throw new ServiceError("INVALID_RESPONSE", "对话记录读取失败，原始记录已保留。"); }
  }
  private save(actor: Identity, state: StoredHistory) {
    try { this.storage.setItem(historyKey(actor), JSON.stringify(state)); }
    catch { throw new ServiceError("CONFLICT", "浏览器无法保存对话，请检查本地存储空间。"); }
    this.notify();
  }
  private project(state: HistorySnapshot, projectId: string | null) {
    if (projectId !== null && !state.projects.some((project) => project.id === projectId)) throw new ServiceError("NO_DATA", "没有找到这个项目。");
  }
  createProject(actor: Identity, input: { name: string }) {
    const name = input.name.trim();
    if (!name || name.length > 40) throw new ServiceError("INVALID_INPUT", "项目名称需要 1 至 40 个字符。");
    const state = this.read(actor);
    if (state.projects.some((project) => project.name === name)) throw new ServiceError("CONFLICT", "已有同名项目，请换一个名称。");
    const project = { id: crypto.randomUUID(), name, createdAt: this.clock().toISOString() };
    state.projects.unshift(project); this.save(actor, state); return project;
  }
  send(actor: Identity, input: SendMessageRequest, interpretation: CommandInterpretation) {
    const state = this.read(actor);
    const signature = JSON.stringify([input.conversationId, input.projectId, input.text.trim(), scopeFingerprint(input.scope), input.source]);
    const existing = state.requests[input.idempotencyKey];
    if (existing) {
      if (existing.signature !== signature) throw new ServiceError("CONFLICT", "此发送标识已用于其他内容。");
      const conversation = state.conversations.find((item) => item.id === existing.conversationId)!;
      const prior = conversation.messages.find((message) => message.id === existing.messageId)!;
      return { conversation, interpretation: prior.interpretation!, messageId: prior.id };
    }
    const text = input.text.trim();
    if (!text || text.length > 500 || !input.idempotencyKey) throw new ServiceError("INVALID_INPUT", "请输入 1 至 500 个字符的内容。");
    this.project(state, input.projectId ?? null);
    const at = this.clock().toISOString();
    let conversation: Conversation;
    if (input.conversationId) {
      const found = state.conversations.find((item) => item.id === input.conversationId);
      if (!found) throw new ServiceError("NO_DATA", "没有找到这条对话。");
      conversation = found;
    } else {
      conversation = { id: crypto.randomUUID(), title: text.slice(0, 36), projectId: input.projectId ?? null, createdAt: at, updatedAt: at, messages: [] };
      state.conversations.unshift(conversation);
    }
    const reply = interpretation.kind === "ACTION" ? "已识别需要调整的操作。请核对具体对象，创建任务后查看变更与风险；尚未执行任何投放操作。" : interpretation.kind === "QUERY" ? "已读取当前范围的直播信息。下方可以查看数据，也可以继续追问。" : interpretation.text;
    const messageId = crypto.randomUUID();
    conversation.messages.push({ id: crypto.randomUUID(), role: "user", text, createdAt: at }, { id: messageId, role: "assistant", text: reply, createdAt: at, interpretation, scope: input.scope });
    conversation.updatedAt = at;
    state.requests[input.idempotencyKey] = { conversationId: conversation.id, messageId, signature };
    this.save(actor, state); return { conversation, interpretation, messageId };
  }
  move(actor: Identity, input: { conversationId: string; projectId: string | null }) {
    const state = this.read(actor); this.project(state, input.projectId);
    const conversation = state.conversations.find((item) => item.id === input.conversationId);
    if (!conversation) throw new ServiceError("NO_DATA", "没有找到这条对话。");
    conversation.projectId = input.projectId; conversation.updatedAt = this.clock().toISOString();
    for (const message of conversation.messages) if (message.jobId) delete state.taskProjects[message.jobId];
    this.save(actor, state); return conversation;
  }
  attach(actor: Identity, input: { conversationId: string; messageId: string; jobId: string }) {
    const state = this.read(actor);
    const conversation = state.conversations.find((item) => item.id === input.conversationId);
    const message = conversation?.messages.find((item) => item.id === input.messageId && item.role === "assistant" && item.interpretation?.kind === "ACTION");
    if (!conversation || !message) throw new ServiceError("NO_DATA", "没有找到任务对应的对话消息。");
    if (message.jobId && message.jobId !== input.jobId) throw new ServiceError("CONFLICT", "此消息已关联其他任务。");
    message.jobId = input.jobId; conversation.updatedAt = this.clock().toISOString(); this.save(actor, state); return conversation;
  }
  assign(actor: Identity, input: { jobId: string; projectId: string | null }) {
    const state = this.read(actor); this.project(state, input.projectId);
    state.taskProjects[input.jobId] = input.projectId; this.save(actor, state);
  }
}
