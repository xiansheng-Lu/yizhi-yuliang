import type { FrontendSnapshot, Identity, RiskPolicy } from "@/lib/domain";
import { customerPageIds } from "@/lib/routes";
import type { Conversation, ConversationTurn, HistorySnapshot, SendMessageRequest, WorkProject } from "@/lib/workspace-history";
import {
  commandActions,
  jobStatuses,
  validateJobValue,
  type CommandJob,
  type CreateJobRequest,
  type JobVersionRequest,
  type CommandInterpretation,
  type ReportingScope,
} from "@/lib/command-job";
import {
  ServiceError,
  type ApiSuccess,
  type FrontendService,
  type LoginRequest,
  type EmailLoginChallenge,
} from "./contracts";

/** Cookie-based API client. Authentication and mail delivery run on the backend. */
export class ApiClient implements FrontendService {
  readonly mode = "api" as const;
  private listeners = new Set<() => void>();
  private authInvalidated = false;
  private csrfGrant?: { token: string; expiresAt: number };
  private csrfPending?: Promise<string>;
  private authEpoch = 0;
  private sessionOwner: string | null = null;
  constructor(
    private readonly baseUrl: string,
    private readonly fetcher: typeof fetch = (...args) => fetch(...args),
  ) {}
  private clearCsrf() {
    this.csrfGrant = undefined;
    this.csrfPending = undefined;
    this.authEpoch++;
  }
  private getCsrf(): Promise<string> {
    if (this.csrfGrant && this.csrfGrant.expiresAt > Date.now() + 1000)
      return Promise.resolve(this.csrfGrant.token);
    if (this.csrfPending) return this.csrfPending;
    const epoch = this.authEpoch;
    const pending: Promise<string> = (async () => {
      let data: { token: string; expiresAt: string };
      try {
        data = await this.request<{ token: string; expiresAt: string }>("/v1/auth/csrf");
      } catch (error) {
        // Session renewal may race the CSRF CAS. Retry only this safe GET once.
        if (!(error instanceof ServiceError) || error.code !== "CONFLICT" || epoch !== this.authEpoch)
          throw error;
        data = await this.request<{ token: string; expiresAt: string }>("/v1/auth/csrf");
      }
      if (epoch !== this.authEpoch)
        throw new ServiceError("FORBIDDEN", "登录状态已变化，请重新核对后操作。");
      const expiresAt = Date.parse(data?.expiresAt);
      if (!data || typeof data.token !== "string" || !data.token || data.token.length > 512 || /\s/.test(data.token) || typeof data.expiresAt !== "string" || !Number.isFinite(expiresAt) || expiresAt <= Date.now())
        throw new ServiceError("INVALID_RESPONSE", "后端安全凭证无效，请稍后重试。");
      this.csrfGrant = { token: data.token, expiresAt };
      return data.token;
    })().finally(() => {
      if (this.csrfPending === pending) this.csrfPending = undefined;
    });
    this.csrfPending = pending;
    return pending;
  }
  private waitForCsrf(signal: AbortSignal): Promise<string> {
    signal.throwIfAborted();
    const pending = this.getCsrf();
    return new Promise((resolve, reject) => {
      const abort = () => { signal.removeEventListener("abort", abort); reject(signal.reason); };
      signal.addEventListener("abort", abort, { once: true });
      pending.then(
        (value) => { signal.removeEventListener("abort", abort); resolve(value); },
        (error) => { signal.removeEventListener("abort", abort); reject(error); },
      );
      if (signal.aborted) abort();
    });
  }
  private async request<T>(
    path: string,
    method = "GET",
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<T> {
    if (!this.baseUrl)
      throw new ServiceError(
        "API_NOT_CONFIGURED",
        "API 模式未配置后端地址。不会回退为 Mock。",
      );
    method = method.toUpperCase();
    const mutation = !["GET", "HEAD", "OPTIONS"].includes(method);
    const epoch = this.authEpoch;
    const requestSignal = signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000);
    let response: Response;
    try {
      const token = mutation ? await this.waitForCsrf(requestSignal) : undefined;
      requestSignal.throwIfAborted();
      if (mutation && epoch !== this.authEpoch)
        throw new ServiceError("FORBIDDEN", "登录状态已变化，请核对后重新发起操作。");
      response = await this.fetcher(
        `${this.baseUrl.replace(/\/$/, "")}${path}`,
        {
          method,
          credentials: "include",
          cache: "no-store",
          headers: {
            Accept: "application/json",
            ...(mutation ? { "X-Yuliang-Client": "web", "X-CSRF-Token": token! } : {}),
            ...(body === undefined || body instanceof FormData
              ? {}
              : { "Content-Type": "application/json" }),
          },
          ...(body === undefined ? {} : { body: body instanceof FormData ? body : JSON.stringify(body) }),
          signal: requestSignal,
        },
      );
    } catch (error) {
      if (error instanceof ServiceError) throw error;
      if (signal?.aborted) throw signal.reason;
      if (
        error instanceof Error &&
        (error.name === "TimeoutError" || error.name === "AbortError")
      )
        throw new ServiceError(
          "TIMEOUT",
          "后端请求超时。执行结果未知，请刷新核对，勿重复提交。",
        );
      throw new ServiceError("NETWORK_ERROR", "后端暂时无法访问，请稍后重试。");
    }
    if (!response.ok) {
      const currentAuth = epoch === this.authEpoch;
      const credentialFailure = path === "/v1/auth/login" && response.status === 401;
      // A wrong password/code does not consume the preauth challenge. Preserve it
      // so correcting credentials does not silently replace the email binding.
      if (currentAuth && response.status === 401 && !credentialFailure) this.sessionOwner = null;
      if (currentAuth && ((response.status === 401 && !credentialFailure) || (mutation && response.status === 403)))
        this.clearCsrf();
      if (currentAuth && response.status === 401 && !credentialFailure && path !== "/v1/auth/session" && !this.authInvalidated) {
        this.authInvalidated = true;
        this.listeners.forEach((listener) => listener());
      }
      // 读取受控错误信封：页面需要展示后端中文业务原因与字段级 details。
      let envelope: { message?: unknown; details?: unknown } | undefined;
      try {
        const payload: unknown = await response.json();
        const error = payload && typeof payload === "object" ? (payload as { error?: unknown }).error : undefined;
        if (error && typeof error === "object") envelope = error as { message?: unknown; details?: unknown };
      } catch {
        envelope = undefined;
      }
      const reason =
        envelope && typeof envelope.message === "string" && envelope.message.trim() && envelope.message.length <= 300
          ? envelope.message
          : undefined;
      const rawDetails = envelope?.details;
      const details =
        Array.isArray(rawDetails) && rawDetails.length <= 50 && rawDetails.every((item) => item !== null && typeof item === "object" && !Array.isArray(item))
          ? (rawDetails as Record<string, string>[])
          : undefined;
      const code =
        response.status === 401
          ? "UNAUTHENTICATED"
          : response.status === 403
            ? "FORBIDDEN"
            : response.status === 404
              ? "NO_DATA"
                : response.status === 429
                  ? "RATE_LIMITED"
                : response.status === 423
                ? "LOCKED"
                : response.status === 409
                ? "CONFLICT"
                : response.status === 400 || response.status === 422
                  ? "INVALID_INPUT"
                  : "NETWORK_ERROR";
      const messages = {
        UNAUTHENTICATED: "登录已失效或凭证不正确，请重新登录。",
        FORBIDDEN: "当前账号没有此项操作权限。",
        RATE_LIMITED: "操作过于频繁，请稍后重试。",
        LOCKED: "资源正在处理中，请稍后核对结果，勿重复提交。",
        INVALID_INPUT: "提交的信息不正确，请检查后重试。",
        NO_DATA: "暂时没有找到相关数据。",
        CONFLICT: "信息已更新，请刷新后重试。",
        NETWORK_ERROR: "服务暂时不可用，请稍后重试。",
      };
      const retryAfter = Number(response.headers.get("Retry-After"));
      throw new ServiceError(code, reason ?? messages[code], Number.isFinite(retryAfter) && retryAfter > 0 ? Math.min(Math.ceil(retryAfter), 3600) : undefined, details);
    }
    let payload: ApiSuccess<T>;
    try {
      payload = await response.json();
    } catch {
      throw new ServiceError("INVALID_RESPONSE", "后端没有返回有效 JSON。");
    }
    if (
      !payload ||
      payload.schemaVersion !== "command-job-v1.0" ||
      typeof payload.requestId !== "string" ||
      !("data" in payload)
    )
      throw new ServiceError(
        "INVALID_RESPONSE",
        "后端响应与 command-job-v1.0 契约不一致。",
      );
    return payload.data;
  }
  async getIdentity() {
    const epoch = this.authEpoch;
    try {
      const data = await this.request<Identity | null>("/v1/auth/session");
      if (data === null) {
        // A focus/session check before login must preserve an issued email challenge.
        if (epoch === this.authEpoch && this.sessionOwner !== null) {
          this.sessionOwner = null;
          this.clearCsrf();
        }
        return null;
      }
      validateIdentity(data);
      if (epoch === this.authEpoch) {
        const owner = `${data.mode}:${data.tenantId}:${data.userId}`;
        if (owner !== this.sessionOwner) this.clearCsrf();
        this.sessionOwner = owner;
        this.authInvalidated = false;
      }
      return data;
    } catch (error) {
      if (error instanceof ServiceError && error.code === "UNAUTHENTICATED")
        return null;
      throw error;
    }
  }
  /** Domain modules share authenticated transport within their own namespace. */
  analysisRequest<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
    if (!path.startsWith("/v1/business-analysis/")) throw new ServiceError("INVALID_INPUT", "经营分析接口路径不正确。");
    return this.request<T>(path, method, body, signal);
  }
  deliveryRequest<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
    if (!path.startsWith("/v1/delivery/")) throw new ServiceError("INVALID_INPUT", "投放接口路径不正确。");
    return this.request<T>(path, method, body, signal);
  }
  async login(input: LoginRequest) {
    const email = validEmail(input.email);
    if (input.method === "email_code" && (!/^\d{6}$/.test(input.code) || !input.challengeId))
      throw new ServiceError("INVALID_INPUT", "请输入邮件中的 6 位验证码。");
    if (input.method !== "email_code" && !input.password)
      throw new ServiceError("INVALID_INPUT", "请输入密码。");
    const identity = await this.request<Identity>("/v1/auth/login", "POST", { ...input, email });
    this.clearCsrf();
    validateIdentity(identity);
    this.sessionOwner = `${identity.mode}:${identity.tenantId}:${identity.userId}`;
    this.authInvalidated = false;
    return identity;
  }
  async requestEmailCode(input: { email: string }) {
    const data = await this.request<EmailLoginChallenge>("/v1/auth/email-codes", "POST", { email: validEmail(input.email), purpose: "LOGIN" });
    if (!data || typeof data.challengeId !== "string" || !data.challengeId || !Number.isFinite(Date.parse(data.expiresAt)) || Date.parse(data.expiresAt) <= Date.now() || !Number.isInteger(data.retryAfterSeconds) || data.retryAfterSeconds < 1 || data.retryAfterSeconds > 3600)
      throw new ServiceError("INVALID_RESPONSE", "验证码发送信息不完整，请稍后重试。");
    return data;
  }
  async updateProfile(input: { displayName: string }) {
    const displayName = input.displayName.trim();
    if (!displayName || displayName.length > 40) throw new ServiceError("INVALID_INPUT", "昵称需要 1–40 个字符。");
    const identity = await this.request<Identity>("/v1/users/me", "PATCH", { displayName });
    validateIdentity(identity);
    return identity;
  }
  async logout() {
    await this.request<null>("/v1/auth/logout", "POST");
    this.clearCsrf();
    this.sessionOwner = null;
  }
  async getHistory() {
    const state = await this.request<HistorySnapshot>("/v1/workspace/history");
    if (!state || !Array.isArray(state.projects) || !Array.isArray(state.conversations) || !state.taskProjects || typeof state.taskProjects !== "object" || Array.isArray(state.taskProjects) || Object.values(state.taskProjects).some((value) => value !== null && typeof value !== "string")) throw new ServiceError("INVALID_RESPONSE", "项目与对话记录结构不正确。");
    state.projects.forEach(validateProject); state.conversations.forEach(validateConversation); return state;
  }
  async createProject(input: { name: string }) {
    const name = input.name.trim();
    if (!name || name.length > 40) throw new ServiceError("INVALID_INPUT", "项目名称需要 1 至 40 个字符。");
    const project = await this.request<WorkProject>("/v1/projects", "POST", { name });
    validateProject(project); return project;
  }
  async moveConversation(input: { conversationId: string; projectId: string | null }) {
    const conversation = await this.request<Conversation>(`/v1/conversations/${encodeURIComponent(input.conversationId)}`, "PATCH", { projectId: input.projectId });
    validateConversation(conversation); return conversation;
  }
  async sendMessage(input: SendMessageRequest) {
    if (!input.text.trim() || input.text.trim().length > 500 || !input.idempotencyKey || input.source === "AUTOPILOT") throw new ServiceError("INVALID_INPUT", "对话内容或来源不正确。");
    const turn = await this.request<ConversationTurn>("/v1/conversation-messages", "POST", { ...input, text: input.text.trim() });
    if (!turn) throw new ServiceError("INVALID_RESPONSE", "对话没有返回有效处理结果。");
    validateInterpretation(turn.interpretation);
    validateConversation(turn.conversation);
    if (typeof turn.messageId !== "string" || !turn.conversation.messages.some((message) => message.id === turn.messageId && message.role === "assistant" && message.interpretation && message.scope && JSON.stringify(message.interpretation) === JSON.stringify(turn.interpretation))) throw new ServiceError("INVALID_RESPONSE", "对话缺少已保存的处理结果。");
    return turn;
  }
  async attachConversationJob(input: { conversationId: string; messageId: string; jobId: string }) {
    const conversation = await this.request<Conversation>(`/v1/conversations/${encodeURIComponent(input.conversationId)}/jobs`, "POST", { messageId: input.messageId, jobId: input.jobId });
    validateConversation(conversation); return conversation;
  }
  async assignTaskProject(input: { jobId: string; projectId: string | null }) {
    await this.request<null>(`/v1/command-jobs/${encodeURIComponent(input.jobId)}/project`, "PUT", { projectId: input.projectId });
  }
  async getSnapshot(options?: { signal?: AbortSignal }) {
    const data = await this.request<FrontendSnapshot>("/v1/frontend/snapshot", "GET", undefined, options?.signal);
    const arrays = [
      "tenants",
      "shops",
      "adAccounts",
      "liveSessions",
      "plans",
      "products",
      "materials",
      "tasks",
      "riskPolicies",
      "replaySegments",
      "replayReports",
      "connectors",
    ] as const;
    if (
      !data ||
      data.schemaVersion !== "command-job-v1.0" ||
      data.source !== "API" ||
      !data.scope ||
      data.scope.timezone !== "Asia/Shanghai" ||
      arrays.some((key) => !Array.isArray(data[key]))
    )
      throw new ServiceError(
        "INVALID_RESPONSE",
        "快照结构不正确，API 数据不能伪装为 Mock。",
      );
    data.tasks.forEach(validateApiJob);
    return data;
  }
  interpretCommand(input: { text: string; scope: ReportingScope }) {
    return this.request<CommandInterpretation>(
      "/v1/command-interpretations",
      "POST",
      input,
    );
  }
  private async job(path: string, input: unknown) {
    const data = await this.request<CommandJob>(path, "POST", input);
    validateApiJob(data);
    return data;
  }
  createJob(input: CreateJobRequest) {
    if (input.source === "AUTOPILOT")
      throw new ServiceError(
        "FORBIDDEN",
        "自主投放任务只能由后端策略授权创建。",
      );
    if (!validateJobValue(input.action_type, input.new_value))
      throw new ServiceError("INVALID_INPUT", "操作类型与数值不匹配。");
    return this.job("/v1/command-jobs", input);
  }
  confirmJob(input: JobVersionRequest) {
    return this.job(
      `/v1/command-jobs/${encodeURIComponent(input.job_id)}/confirm`,
      input,
    );
  }
  reviewJob(input: JobVersionRequest & { decision: "APPROVE" | "REJECT" }) {
    return this.job(
      `/v1/command-jobs/${encodeURIComponent(input.job_id)}/approvals`,
      input,
    );
  }
  cancelJob(input: JobVersionRequest) {
    return this.job(
      `/v1/command-jobs/${encodeURIComponent(input.job_id)}/cancel`,
      input,
    );
  }
  async advanceMockJob(_input: JobVersionRequest): Promise<CommandJob> {
    void _input;
    throw new ServiceError(
      "FORBIDDEN",
      "API 模式由后端执行和核验，前端不得推进执行状态。",
    );
  }
  async expireMockJob(_input: JobVersionRequest): Promise<CommandJob> {
    void _input;
    throw new ServiceError("FORBIDDEN", "正式任务过期由后端判定。");
  }
  updatePolicy(input: RiskPolicy) {
    return this.request<RiskPolicy>(
      `/v1/risk-policies/${encodeURIComponent(input.id)}`,
      "PUT",
      input,
    );
  }
  async setScope(input: ReportingScope) {
    await this.request<null>("/v1/frontend/reporting-scope", "PUT", input);
  }
  subscribe(onChange: () => void) {
    this.listeners.add(onChange);
    if (typeof window !== "undefined") window.addEventListener("focus", onChange);
    return () => {
      this.listeners.delete(onChange);
      if (typeof window !== "undefined") window.removeEventListener("focus", onChange);
    };
  }
}
function validEmail(value: string) {
  const email = value.trim().toLowerCase();
  if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
    throw new ServiceError("INVALID_INPUT", "请输入有效的邮箱地址。");
  return email;
}
function validateProject(project: WorkProject) {
  if (!project || typeof project.id !== "string" || !project.id || typeof project.name !== "string" || !project.name.trim() || !Number.isFinite(Date.parse(project.createdAt))) throw new ServiceError("INVALID_RESPONSE", "项目结构不正确。");
}
function validateConversation(conversation: Conversation) {
  if (!conversation || typeof conversation.id !== "string" || !conversation.id || typeof conversation.title !== "string" || (conversation.projectId !== null && typeof conversation.projectId !== "string") || !Number.isFinite(Date.parse(conversation.createdAt)) || !Number.isFinite(Date.parse(conversation.updatedAt)) || !Array.isArray(conversation.messages) || conversation.messages.some((message) => !message || typeof message.id !== "string" || !["user", "assistant"].includes(message.role) || typeof message.text !== "string" || !Number.isFinite(Date.parse(message.createdAt)))) throw new ServiceError("INVALID_RESPONSE", "对话记录结构不正确。");
  conversation.messages.forEach((message) => {
    if (message.interpretation) {
      validateInterpretation(message.interpretation);
      if (!message.scope || message.scope.timezone !== "Asia/Shanghai") throw new ServiceError("INVALID_RESPONSE", "历史消息缺少有效的数据范围。");
    }
  });
}
function validateInterpretation(value: CommandInterpretation) {
  if (!value || typeof value.text !== "string" || !["ACTION", "QUERY", "UNRECOGNIZED"].includes(value.kind) || (value.kind === "QUERY" && value.live_session_id !== null && typeof value.live_session_id !== "string") || (value.kind === "ACTION" && (!commandActions.includes(value.action_type) || !validateJobValue(value.action_type, value.new_value) || !["AD_PLAN", "LIVE_SESSION"].includes(value.target_type) || !Array.isArray(value.candidate_ids) || value.candidate_ids.some((id) => typeof id !== "string")))) throw new ServiceError("INVALID_RESPONSE", "指令处理结果结构不正确。");
}
function validateIdentity(identity: Identity) {
  const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");
  if (!identity || identity.mode !== "API" || typeof identity.userId !== "string" || !identity.userId || typeof identity.displayName !== "string" || !identity.displayName.trim() || typeof identity.email !== "string" || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(identity.email) || typeof identity.tenantId !== "string" || !identity.tenantId || !strings(identity.shopIds) || !strings(identity.permissions) || !strings(identity.workspacePages) || identity.workspacePages.some((id) => !customerPageIds.some((page) => page === id)))
    throw new ServiceError("INVALID_RESPONSE", "账号或工作区授权信息不完整。");
}
function validateApiJob(job: CommandJob) {
  if (
    !job ||
    job.schema_version !== "1.0" ||
    job.mode !== "API" ||
    !job.job_id ||
    !commandActions.includes(job.action_type) ||
    !jobStatuses.includes(job.status) ||
    !job.risk_summary ||
    job.risk_summary.source !== "SERVER" ||
    !job.approval ||
    !Array.isArray(job.history) ||
    (job.execution_receipt && job.execution_receipt.source !== "PLATFORM") ||
    (job.status === "SUCCEEDED" &&
      (!job.execution_receipt?.accepted ||
        job.execution_receipt.verification !== "MATCHED"))
  )
    throw new ServiceError(
      "INVALID_RESPONSE",
      "任务缺少服务端风险、状态或已核验平台回执。",
    );
  if (!validateJobValue(job.action_type, job.new_value))
    throw new ServiceError("INVALID_RESPONSE", "任务变更值与操作类型不匹配。");
}
