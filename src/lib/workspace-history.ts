import type { CommandInterpretation, JobSource, ReportingScope } from "./command-job";

export interface WorkProject { id: string; name: string; createdAt: string; }
export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  createdAt: string;
  interpretation?: CommandInterpretation;
  scope?: ReportingScope;
  jobId?: string;
}
export interface Conversation {
  id: string;
  title: string;
  projectId: string | null;
  createdAt: string;
  updatedAt: string;
  messages: ConversationMessage[];
}
export interface HistorySnapshot {
  projects: WorkProject[];
  conversations: Conversation[];
  taskProjects: Record<string, string | null>;
}
export interface SendMessageRequest {
  conversationId?: string;
  projectId?: string | null;
  text: string;
  source: JobSource;
  scope: ReportingScope;
  idempotencyKey: string;
}
export interface ConversationTurn { conversation: Conversation; interpretation: CommandInterpretation; messageId: string; }
export const emptyHistory = (): HistorySnapshot => ({ projects: [], conversations: [], taskProjects: {} });
export function scopeFingerprint(scope: ReportingScope) { return JSON.stringify([scope.shop_id, scope.live_session_id, scope.connector_id, scope.date, scope.timezone]); }
export function taskProjectId(history: HistorySnapshot, jobId: string): string | null {
  if (Object.hasOwn(history.taskProjects, jobId)) return history.taskProjects[jobId];
  return history.conversations.find((conversation) => conversation.messages.some((message) => message.jobId === jobId))?.projectId ?? null;
}
export function conversationHref(id: string) { return `/app/command?conversation=${encodeURIComponent(id)}`; }
export function projectHref(id: string) { return `/app/command?project=${encodeURIComponent(id)}`; }
