import type { FrontendSnapshot, Identity, RiskPolicy } from "@/lib/domain";
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
} from "./contracts";

/** Draft client only. These endpoints are to be supplied by the backend owner. */
export class ApiClient implements FrontendService {
  readonly mode = "api" as const;
  constructor(
    private readonly baseUrl: string,
    private readonly fetcher: typeof fetch = fetch,
  ) {}
  private async request<T>(
    path: string,
    method = "GET",
    body?: unknown,
  ): Promise<T> {
    if (!this.baseUrl)
      throw new ServiceError(
        "API_NOT_CONFIGURED",
        "API 模式未配置后端地址。不会回退为 Mock。",
      );
    let response: Response;
    try {
      response = await this.fetcher(
        `${this.baseUrl.replace(/\/$/, "")}${path}`,
        {
          method,
          credentials: "include",
          cache: "no-store",
          headers: {
            Accept: "application/json",
            ...(body === undefined
              ? {}
              : { "Content-Type": "application/json" }),
          },
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
          signal: AbortSignal.timeout(15000),
        },
      );
    } catch (error) {
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
      const code =
        response.status === 401
          ? "UNAUTHENTICATED"
          : response.status === 403
            ? "FORBIDDEN"
            : response.status === 404
              ? "NO_DATA"
              : response.status === 409
                ? "CONFLICT"
                : response.status === 400 || response.status === 422
                  ? "INVALID_INPUT"
                  : "NETWORK_ERROR";
      throw new ServiceError(
        code,
        `后端请求未完成（${response.status}），请核对接口契约与权限。`,
      );
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
    try {
      const data = await this.request<Identity>("/v1/auth/session");
      if (
        data?.mode !== "API" ||
        !data.tenantId ||
        !Array.isArray(data.permissions)
      )
        throw new ServiceError("INVALID_RESPONSE", "服务端会话结构不正确。");
      return data;
    } catch (error) {
      if (error instanceof ServiceError && error.code === "UNAUTHENTICATED")
        return null;
      throw error;
    }
  }
  login(input: LoginRequest) {
    return this.request<Identity>("/v1/auth/login", "POST", input);
  }
  async logout() {
    await this.request<null>("/v1/auth/logout", "POST");
  }
  async getSnapshot() {
    const data = await this.request<FrontendSnapshot>("/v1/frontend/snapshot");
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
  subscribe() {
    return () => {};
  }
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
