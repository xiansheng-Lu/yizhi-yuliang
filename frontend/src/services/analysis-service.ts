import type { FrontendSnapshot, Identity } from "../lib/domain";
import { analysisKey, dimensions, validateAnalysisQuery, type AnalysisQuery, type AnalysisResult, type AnalysisTask } from "../lib/business-analysis";
import { validateDraft, type PublishedReport, type ReportDraft } from "../lib/analysis-draft";
import { ApiClient } from "./api-client";
import type { FrontendService } from "./contracts";
import { buildMockAnalysis, type AnalysisScenario } from "./analysis-mock";

export interface AnalysisAdapter {
  createTask(query: AnalysisQuery, idempotencyKey: string, signal: AbortSignal, scenario?: AnalysisScenario): Promise<AnalysisTask>;
  getTask(id: string, signal: AbortSignal): Promise<AnalysisTask>;
  getResult(id: string, signal: AbortSignal): Promise<AnalysisResult>;
  listResults(signal: AbortSignal): Promise<AnalysisResult[]>;
  listDrafts(signal: AbortSignal): Promise<ReportDraft[]>;
  getDraft(id: string, signal: AbortSignal): Promise<ReportDraft>;
  saveDraft(draft: ReportDraft, signal: AbortSignal): Promise<ReportDraft>;
  publishDraft(id: string, version: number, signal: AbortSignal): Promise<PublishedReport>;
  getPublished(id: string, signal: AbortSignal): Promise<PublishedReport>;
  listPublished(signal: AbortSignal): Promise<PublishedReport[]>;
}
type Store = {
  tasks: Record<string, AnalysisTask & { scenario: AnalysisScenario; idempotencyKey: string }>;
  results: Record<string, AnalysisResult>;
  drafts: Record<string, ReportDraft>;
  published: Record<string, PublishedReport>;
};
export function authorizedAnalysisSnapshot(snapshot: FrontendSnapshot, identity: Identity): FrontendSnapshot {
  const shops = snapshot.shops.filter(shop => shop.tenantId === identity.tenantId && identity.shopIds.includes(shop.id));
  const allowed = new Set(shops.map(shop => shop.id));
  return { ...snapshot, shops,
    adAccounts: snapshot.adAccounts.filter(account => account.tenantId === identity.tenantId && allowed.has(account.shopId)),
    products: snapshot.products.filter(product => product.tenantId === identity.tenantId && allowed.has(product.shopId)),
    plans: snapshot.plans.filter(plan => plan.tenantId === identity.tenantId && allowed.has(plan.shopId)),
    liveSessions: snapshot.liveSessions.filter(live => live.tenantId === identity.tenantId && allowed.has(live.shopId)),
  };
}
export class MockAnalysisAdapter implements AnalysisAdapter {
  private key: string;
  constructor(private snapshot: FrontendSnapshot, identity: Identity, private storage: Pick<Storage, "getItem" | "setItem">) {
    this.snapshot = authorizedAnalysisSnapshot(snapshot, identity);
    this.key = `yuliang:analysis:v1:${identity.tenantId}:${identity.userId}`;
  }
  private read(): Store {
    const raw = this.storage.getItem(this.key);
    if (!raw) return { tasks: {}, results: {}, drafts: {}, published: {} };
    const state = JSON.parse(raw) as Store;
    if (!state.tasks || !state.results || !state.drafts || !state.published) throw new Error("本地分析记录损坏，请检查浏览器存储。");
    return state;
  }
  private write(state: Store) { this.storage.setItem(this.key, JSON.stringify(state)); }
  async createTask(query: AnalysisQuery, idempotencyKey: string, signal: AbortSignal, scenario: AnalysisScenario = "normal") {
    signal.throwIfAborted(); validateAnalysisQuery(query, this.snapshot);
    const state = this.read();
    const existing = Object.values(state.tasks).find(task => task.idempotencyKey === idempotencyKey);
    if (existing) {
      if (analysisKey(existing.query) !== analysisKey(query)) throw new Error("重复请求标识与原分析条件不一致。");
      return structuredClone(existing);
    }
    const id = crypto.randomUUID();
    const task = { id, query: structuredClone(query), status: "queued" as const, progress: 0, createdAt: new Date().toISOString(), scenario, idempotencyKey };
    state.tasks[id] = task; this.write(state); return structuredClone(task);
  }
  async getTask(id: string, signal: AbortSignal) {
    signal.throwIfAborted(); const state = this.read(), task = state.tasks[id];
    if (!task) throw new Error("未找到本次分析任务，可能不属于当前用户。");
    if (task.status === "queued") { task.status = "running"; task.progress = 45; }
    else if (task.status === "running") {
      if (task.scenario === "failure") { task.status = "failed"; task.error = "演示失败样本：平台报表读取超时，请重新分析。"; }
      else {
        task.resultId = `result-${task.id}`;
        state.results[task.resultId] = buildMockAnalysis(this.snapshot, task.query, task.resultId, id, task.scenario);
        task.status = "succeeded"; task.progress = 100;
      }
    }
    this.write(state); return structuredClone(task);
  }
  async getResult(id: string, signal: AbortSignal) {
    signal.throwIfAborted(); const result = this.read().results[id];
    if (!result) throw new Error("未找到分析结果，请返回经营分析重新执行。");
    return structuredClone(result);
  }
  async listResults(signal: AbortSignal) { signal.throwIfAborted(); return Object.values(this.read().results).filter(result => this.snapshot.shops.some(shop => shop.id === result.query.shopId)).sort((a, b) => b.createdAt.localeCompare(a.createdAt)); }
  async listDrafts(signal: AbortSignal) { signal.throwIfAborted(); return Object.values(this.read().drafts).filter(draft => this.snapshot.shops.some(shop => shop.id === draft.query.shopId)).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)); }
  async getDraft(id: string, signal: AbortSignal) {
    signal.throwIfAborted(); const draft = this.read().drafts[id];
    if (!draft) throw new Error("未找到复盘草稿，请从分析报告生成。");
    return structuredClone(draft);
  }
  async saveDraft(draft: ReportDraft, signal: AbortSignal) {
    signal.throwIfAborted(); validateDraft(draft);
    const state = this.read(), saved = state.drafts[draft.id];
    if ((saved?.version ?? 0) !== draft.version) throw new Error("草稿已在其他页面修改，请重新打开最新版本后编辑。");
    const next = structuredClone({ ...draft, version: draft.version + 1, updatedAt: new Date().toISOString() });
    state.drafts[draft.id] = next; this.write(state); return structuredClone(next);
  }
  async publishDraft(id: string, version: number, signal: AbortSignal) {
    const draft = await this.getDraft(id, signal);
    if (draft.version !== version) throw new Error("草稿版本已变化，请保存最新版本后发布。");
    validateDraft(draft);
    const existing = Object.values(this.read().published).find(item => item.draftId === id && item.snapshot?.version === version);
    if (existing) return structuredClone(existing);
    const publication: PublishedReport = { id: crypto.randomUUID(), draftId: id, url: null, source: "MOCK", publishedAt: new Date().toISOString(), snapshot: draft, title: draft.title, period: draft.period, query: draft.query, analysisId: draft.analysisId };
    const state = this.read(); state.published[publication.id] = structuredClone(publication); this.write(state); return publication;
  }
  async getPublished(id: string, signal: AbortSignal) {
    signal.throwIfAborted(); const publication = this.read().published[id];
    if (!publication) throw new Error("未找到此发布版本。");
    return structuredClone(publication);
  }
  async listPublished(signal: AbortSignal) { signal.throwIfAborted(); return Object.values(this.read().published).sort((a, b) => b.publishedAt.localeCompare(a.publishedAt)); }
}

function validateTask(task: AnalysisTask) {
  if (!task || !task.id || !["queued", "running", "succeeded", "failed"].includes(task.status) || !task.query || !Number.isFinite(task.progress) || task.progress < 0 || task.progress > 100 || (task.status === "succeeded" && !task.resultId)) throw new Error("分析任务响应不符合契约。");
  return task;
}
export function validateAnalysisResult(result: AnalysisResult, snapshot: FrontendSnapshot) {
  if (!result || result.schemaVersion !== "business-analysis-v1" || result.source !== "API" || !result.id || !result.taskId || !["complete", "partial", "empty"].includes(result.completeness) || !Array.isArray(result.metrics) || !Array.isArray(result.points) || !Array.isArray(result.previousPoints) || !Array.isArray(result.yearAgoPoints) || !Array.isArray(result.sections) || !Array.isArray(result.insights) || !result.comparison || !result.rules) throw new Error("经营分析结果不符合 business-analysis-v1 契约。");
  validateAnalysisQuery(result.query, snapshot);
  const selected = new Set(result.query.dimensions);
  const metricIds = new Set(result.metrics.map(m => m.id));
  if (metricIds.size !== result.metrics.length || result.metrics.some(m => !selected.has(m.dimension) || !["ready", "empty", "not_integrated", "unsupported"].includes(m.status) || (m.value !== null && !Number.isFinite(m.value)) || (m.status !== "ready" && m.value !== null) || !m.definition || !["minor", "count", "rate", "ratio", "hours"].includes(m.unit))) throw new Error("指标范围、数值或统计口径不正确。");
  if (result.points.some(p => p.date < result.query.startDate || p.date > result.query.endDate || Object.entries(p.values).some(([key, v]) => !dimensions.some(d => selected.has(d) && key.startsWith(`${d}.`)) || (v !== null && !Number.isFinite(v))))) throw new Error("时间序列与本次查询范围不一致。");
  if (result.sections.some(s => (s.dimension !== "cross" && !selected.has(s.dimension)) || (s.dimension === "cross" && selected.size < 2) || s.metricIds.some(id => !metricIds.has(id)) || !Array.isArray(s.rows))) throw new Error("报告模块超出本次选择范围。");
  return result;
}
export class ApiAnalysisAdapter implements AnalysisAdapter {
  constructor(private client: ApiClient, private snapshot: FrontendSnapshot) {}
  private request<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal) { return this.client.analysisRequest<T>(`/v1/business-analysis${path}`, method, body, signal); }
  async createTask(query: AnalysisQuery, idempotencyKey: string, signal: AbortSignal) {
    validateAnalysisQuery(query, this.snapshot);
    const task = validateTask(await this.request<AnalysisTask>("/tasks", "POST", { query, idempotencyKey }, signal));
    if (analysisKey(task.query) !== analysisKey(query)) throw new Error("任务响应条件与请求不一致。");
    return task;
  }
  async getTask(id: string, signal: AbortSignal) { const task = validateTask(await this.request<AnalysisTask>(`/tasks/${encodeURIComponent(id)}`, "GET", undefined, signal)); if (task.id !== id) throw new Error("任务标识不一致。"); return task; }
  async getResult(id: string, signal: AbortSignal) { const result = validateAnalysisResult(await this.request<AnalysisResult>(`/results/${encodeURIComponent(id)}`, "GET", undefined, signal), this.snapshot); if (result.id !== id) throw new Error("结果标识不一致。"); return result; }
  async listResults(signal: AbortSignal) { const list = await this.request<AnalysisResult[]>("/results", "GET", undefined, signal); if (!Array.isArray(list)) throw new Error("分析历史响应不正确。"); return list.map(result => validateAnalysisResult(result, this.snapshot)); }
  async listDrafts(signal: AbortSignal) { const list = await this.request<ReportDraft[]>("/drafts", "GET", undefined, signal); if (!Array.isArray(list)) throw new Error("草稿列表响应不正确。"); list.forEach(draft => validateDraft(draft)); return list; }
  async getDraft(id: string, signal: AbortSignal) { const draft = await this.request<ReportDraft>(`/drafts/${encodeURIComponent(id)}`, "GET", undefined, signal); validateDraft(draft); if (draft.id !== id || draft.source !== "API") throw new Error("复盘标识或数据源不一致。"); return draft; }
  async saveDraft(draft: ReportDraft, signal: AbortSignal) { validateDraft(draft); const saved = await this.request<ReportDraft>(`/drafts/${encodeURIComponent(draft.id)}`, "PUT", draft, signal); validateDraft(saved); if (saved.id !== draft.id || saved.source !== "API" || saved.version <= draft.version) throw new Error("草稿保存回执版本不正确。"); return saved; }
  async publishDraft(id: string, version: number, signal: AbortSignal) { return this.validatePublication(await this.request<PublishedReport>(`/drafts/${encodeURIComponent(id)}/publications`, "POST", { version, idempotencyKey: `${id}:${version}` }, signal)); }
  async getPublished(id: string, signal: AbortSignal) { const publication = this.validatePublication(await this.request<PublishedReport>(`/publications/${encodeURIComponent(id)}`, "GET", undefined, signal)); if (publication.id !== id) throw new Error("发布版本不一致。"); return publication; }
  private validatePublication(publication: PublishedReport) {
    if (!publication || !publication.id || publication.source !== "API" || !publication.url || !/^https?:\/\//.test(publication.url)) throw new Error("发布接口没有返回有效客户网页地址。");
    return publication;
  }
  async listPublished(signal: AbortSignal) { const rows = await this.request<PublishedReport[]>("/publications", "GET", undefined, signal); if (!Array.isArray(rows)) throw new Error("发布历史格式不正确。"); return rows.map(row => this.validatePublication(row)); }
}
export function createAnalysisAdapter(service: FrontendService, snapshot: FrontendSnapshot, identity: Identity): AnalysisAdapter {
  snapshot = authorizedAnalysisSnapshot(snapshot, identity);
  if (service.mode === "mock" && snapshot.source === "MOCK" && identity.mode === "MOCK") return new MockAnalysisAdapter(snapshot, identity, window.localStorage);
  if (service instanceof ApiClient && snapshot.source === "API" && identity.mode === "API") return new ApiAnalysisAdapter(service, snapshot);
  throw new Error("分析数据源与当前账号不一致，请刷新后重试。");
}
export function abortablePause(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    if (signal.aborted) abort(); else signal.addEventListener("abort", abort, { once: true });
  });
}
