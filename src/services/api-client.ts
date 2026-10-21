import type {
  Approval,
  CommandTask,
  CreateTaskRequest,
  FrontendSnapshot,
  Identity,
} from "@/lib/domain";
import {
  ServiceError,
  type ApiSuccess,
  type ApproveTaskRequest,
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
    } catch {
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
      payload.schemaVersion !== "g1a-draft-1" ||
      typeof payload.requestId !== "string" ||
      !("data" in payload)
    )
      throw new ServiceError(
        "INVALID_RESPONSE",
        "后端响应与 g1a-draft-1 契约不一致。",
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
      "riskChecks",
      "approvals",
      "receipts",
      "replaySegments",
      "replayReports",
      "connectors",
    ] as const;
    if (
      !data ||
      data.schemaVersion !== "g1a-draft-1" ||
      data.source !== "API" ||
      arrays.some((key) => !Array.isArray(data[key]))
    )
      throw new ServiceError(
        "INVALID_RESPONSE",
        "快照结构不正确，API 数据不能伪装为 Mock。",
      );
    return data;
  }
  createTask(input: CreateTaskRequest) {
    return this.request<CommandTask>("/v1/command-jobs", "POST", input);
  }
  approveTask(input: ApproveTaskRequest) {
    return this.request<Approval>(
      `/v1/command-jobs/${encodeURIComponent(input.taskId)}/approvals`,
      "POST",
      input,
    );
  }
  subscribe() {
    return () => {};
  }
}
