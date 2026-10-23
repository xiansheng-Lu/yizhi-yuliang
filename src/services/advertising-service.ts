import type { Identity } from "../lib/domain";
import {
  advertisingProducts,
  configurationContext,
  configurationContextKey,
  fieldNames,
  fieldStateNames,
  validateConfiguration,
  type AdvertisingDraft,
  type AdvertisingProduct,
  type ConfigurationCapability,
  type ConfigurationCatalog,
  type ConfigurationContext,
  type LookupKind,
  type LookupPage,
  type SubmissionTicket,
  type SelectedResource,
} from "../lib/advertising";
import { ApiClient } from "./api-client";
import { ServiceError, type FrontendService } from "./contracts";
import { hasCreationEvidence, productCatalog } from "../lib/platform-catalog";

export interface ConfigurationValidation {
  contractVersion: "advertising-v9.0";
  source: "API";
  tenantId: string;
  contextKey: string;
  capabilityVersion: string;
  validationToken: string | null;
  expiresAt: string;
  checks: {
    field: string;
    passed: boolean;
    severity: "ERROR" | "WARNING";
    message: string;
  }[];
  requiresApproval: boolean;
}
export interface ConfigurationTask {
  contractVersion: "advertising-v9.0";
  source: "API";
  tenantId: string;
  contextKey: string;
  taskId: string;
  idempotencyKey: string;
  state:
    | "PENDING"
    | "AWAITING_APPROVAL"
    | "PAYMENT_REQUIRED"
    | "PROCESSING"
    | "SUCCEEDED"
    | "FAILED"
    | "REJECTED" | "CANCELLED";
  businessId: string | null;
  platformStatus: string | null;
  verification: "PENDING" | "MATCHED" | "UNKNOWN" | "MISMATCH";
  approvalId:string|null;version:number;cancellable:boolean;
  message: string;
}
export interface AudienceEstimate {
  contractVersion: "advertising-v9.0";
  source: "API";
  tenantId: string;
  contextKey: string;
  lower: number;
  upper: number;
  estimatedAt: string;
  description: string;
}
const invalid = (
  message = "配置接口响应不符合 advertising-v9.0 契约，不能用于正式投放。",
) => new ServiceError("INVALID_RESPONSE", message);
const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === "string";
const authorization = (v: unknown) =>
  ["AUTHORIZED", "UNAUTHORIZED", "FORBIDDEN", "NOT_INTEGRATED"].includes(
    String(v),
  );
/** Reuses cookie transport. There is intentionally no mock catalogue, mock tag DB,
 * mock capability grant or successful-write fallback in this adapter. */
export class AdvertisingService {
  private validations = new Map<string, string>();
  constructor(
    private readonly service: FrontendService,
    private readonly identity: Identity,
  ) {}
  private request<T>(
    path: string,
    method = "GET",
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<T> {
    if (!(this.service instanceof ApiClient) || this.identity.mode !== "API")
      throw new ServiceError(
        "API_NOT_CONFIGURED",
        "当前为演示模式；配置接口尚未连接，不加载模拟平台选项。",
      );
    return this.service.deliveryRequest<T>(
      `/v1/delivery/configuration/${path}`,
      method,
      body,
      signal,
    );
  }
  private envelope(
    value: unknown,
    context?: ConfigurationContext,
  ): asserts value is Record<string, unknown> {
    if (
      !isObject(value) ||
      value.contractVersion !== "advertising-v9.0" ||
      value.source !== "API" ||
      value.tenantId !== this.identity.tenantId ||
      (context && value.contextKey !== configurationContextKey(context))
    )
      throw invalid();
  }
  private contextQuery(context: ConfigurationContext) {
    if (
      !context.accountId ||
      !context.businessEntityId ||
      !(
        advertisingProducts[context.product].scenarios as readonly string[]
      ).includes(context.scenario)
    )
      throw new ServiceError(
        "INVALID_INPUT",
        "先选择账户、关联店铺和正确产品场景。",
      );
    const query = new URLSearchParams({
      ...context,
      placementIds: context.placementIds.join(","),
      targetIds: context.targetIds.join(","),
    });
    return query;
  }
  async catalog(
    product: AdvertisingProduct,
    signal?: AbortSignal,
  ): Promise<ConfigurationCatalog> {
    const value = await this.request<ConfigurationCatalog>(
      `catalog?product=${product}`,
      "GET",
      undefined,
      signal,
    );
    this.envelope(value);
    if (
      value.product !== product ||
      !Array.isArray(value.accounts) ||
      !Array.isArray(value.shops) ||
      !Array.isArray(value.entities) ||
      !text(value.reason)
    )
      throw invalid();
    if (
      value.entities.some((e) => !e || !text(e.id) || !text(e.name)) ||
      value.shops.some(
        (s) =>
          !s ||
          !text(s.id) ||
          !text(s.name) ||
          !this.identity.shopIds.includes(s.id),
      ) ||
      value.accounts.some(
        (a) =>
          !a ||
          !text(a.id) ||
          !text(a.name) ||
          !authorization(a.authorization) ||
          typeof a.writable !== "boolean" ||
          !Array.isArray(a.shopIds) ||
          !value.entities.some((e) => e.id === a.businessEntityId) ||
          a.shopIds.some((s) => !value.shops.some((shop) => shop.id === s)),
      )
    )
      throw invalid("账户目录包含未授权店铺或无效关联，已拒绝使用。");
    return value;
  }
  async capabilities(
    context: ConfigurationContext,
    signal?: AbortSignal,
  ): Promise<ConfigurationCapability> {
    const value = await this.request<ConfigurationCapability>(
      `capabilities?${this.contextQuery(context)}`,
      "GET",
      undefined,
      signal,
    );
    this.envelope(value, context);
    if (
      !text(value.version) ||
      !value.version ||
      !text(value.checkedAt) ||
      !text(value.expiresAt) ||
      !Number.isFinite(Date.parse(value.checkedAt)) ||
      !Number.isFinite(Date.parse(value.expiresAt)) ||
      Date.parse(value.expiresAt) <= Date.now() ||
      Date.parse(value.checkedAt) > Date.now() + 60_000 ||
      !authorization(value.authorization) ||
      !["API", "MANUAL", "NONE"].includes(value.writeChannel) ||
      !text(value.writePermission) ||
      !["MANUAL", "PLATFORM_SMART"].includes(value.audienceManagement) ||
      typeof value.estimateSupported !== "boolean" ||
      typeof value.uploadSupported !== "boolean" ||
      !["CNY", "BEAN"].includes(value.currency) ||
      !isObject(value.fields)
    )
      throw invalid();
    const proof = value.verification;
    if (
      !text(value.apiVersion) ||
      !value.apiVersion ||
      !isObject(proof) ||
      !Array.isArray(proof.catalogIds) ||
      proof.catalogIds.some(
        (id) =>
          !text(id) ||
          !productCatalog(context.product).some((r) => r.id === id),
      ) ||
      ![
        proof.pathVerified,
        proof.applicationScopeGranted,
        proof.accountAuthorized,
        proof.backendIntegrated,
        proof.requestSchemaVerified,
        proof.executionVerified,
      ].every((v) => typeof v === "boolean") ||
      !text(proof.proofId) ||
      !Number.isFinite(Date.parse(proof.checkedAt))
    )
      throw invalid();
    for (const [key, f] of Object.entries(value.fields)) {
      if (
        !Object.hasOwn(fieldNames, key) ||
        !isObject(f) ||
        !Object.hasOwn(fieldStateNames, String(f.state)) ||
        !text(f.reason)
      )
        throw invalid();
      if (
        f.options !== undefined &&
        (!Array.isArray(f.options) ||
          f.options.some(
            (o) =>
              !isObject(o) ||
              !text(o.id) ||
              !o.id ||
              !text(o.name) ||
              typeof o.available !== "boolean",
          ))
      )
        throw invalid();
      for (const bound of ["min", "max", "step", "precision", "maxSelected"])
        if (
          f[bound] !== undefined &&
          (typeof f[bound] !== "number" || !Number.isFinite(f[bound]))
        )
          throw invalid();
      if (
        f.unit !== undefined &&
        !["CNY_MINOR", "BEAN", "RATIO"].includes(String(f.unit))
      )
        throw invalid();
      for (const bound of ["allowedMin", "allowedMax"])
        if (
          f[bound] !== undefined &&
          (!Array.isArray(f[bound]) ||
            f[bound].some((n) => typeof n !== "number" || !Number.isFinite(n)))
        )
          throw invalid();
    }
    return value;
  }
  async lookup(
    context: ConfigurationContext,
    kind: LookupKind,
    query: {
      q?: string;
      cursor?: string;
      category?: string;
      parentId?: string;
    } = {},
    signal?: AbortSignal,
  ): Promise<LookupPage> {
    const params = this.contextQuery(context);
    params.set("kind", kind);
    for (const [key, value] of Object.entries(query))
      if (value) params.set(key, value);
    const value = await this.request<LookupPage>(
      `options?${params}`,
      "GET",
      undefined,
      signal,
    );
    this.envelope(value, context);
    if (
      value.kind !== kind ||
      !Array.isArray(value.items) ||
      !(value.nextCursor === null || text(value.nextCursor)) ||
      !text(value.reason) ||
      value.items.some(
        (r) =>
          !isObject(r) ||
          !text(r.id) ||
          !r.id ||
          !text(r.platformId) ||
          !r.platformId ||
          !text(r.name) ||
          typeof r.eligible !== "boolean" ||
          (r.previewUrl !== undefined &&
            (!text(r.previewUrl) || !/^https:\/\//.test(r.previewUrl))),
      )
    )
      throw invalid();
    return value;
  }
  async validate(
    draft: AdvertisingDraft,
    capability: ConfigurationCapability,
    signal?: AbortSignal,
  ): Promise<ConfigurationValidation> {
    const context = configurationContext(draft);
    const value = await this.request<ConfigurationValidation>(
      "validations",
      "POST",
      { configuration: draft, capabilityVersion: capability.version },
      signal,
    );
    this.envelope(value, context);
    if (
      value.capabilityVersion !== capability.version ||
      !(value.validationToken === null || text(value.validationToken)) ||
      !Number.isFinite(Date.parse(value.expiresAt)) ||
      typeof value.requiresApproval !== "boolean" ||
      !Array.isArray(value.checks) ||
      !value.checks.length ||
      value.checks.some(
        (c) =>
          !c ||
          !text(c.field) ||
          typeof c.passed !== "boolean" ||
          !["ERROR", "WARNING"].includes(c.severity) ||
          !text(c.message),
      )
    )
      throw invalid();
    if (value.validationToken) {
      this.validations.set(value.validationToken, JSON.stringify(draft));
      if (this.validations.size > 30)
        this.validations.delete(this.validations.keys().next().value!);
    }
    return value;
  }
  async submit(
    draft: AdvertisingDraft,
    cap: ConfigurationCapability,
    validation: ConfigurationValidation,
    ticket: SubmissionTicket,
  ): Promise<ConfigurationTask> {
    if (
      !advertisingProducts[draft.product].writeEnabled ||
      !hasCreationEvidence(draft.product, cap.verification.catalogIds) ||
      ![
        cap.verification.pathVerified,
        cap.verification.applicationScopeGranted,
        cap.verification.accountAuthorized,
        cap.verification.backendIntegrated,
        cap.verification.requestSchemaVerified,
        cap.verification.executionVerified,
      ].every(Boolean) ||
      cap.writeChannel !== "API" ||
      cap.authorization !== "AUTHORIZED" ||
      cap.contextKey !== configurationContextKey(configurationContext(draft)) ||
      !Number.isFinite(Date.parse(cap.expiresAt)) ||
      Date.parse(cap.expiresAt) <= Date.now() ||
      validateConfiguration(draft, cap).length > 0 ||
      !this.identity.permissions.includes(cap.writePermission) ||
      !validation.validationToken ||
      this.validations.get(validation.validationToken) !==
        JSON.stringify(draft) ||
      Date.parse(validation.expiresAt) <= Date.now() ||
      validation.contextKey !==
        configurationContextKey(configurationContext(draft)) ||
      validation.capabilityVersion !== cap.version ||
      validation.checks.some((c) => !c.passed && c.severity === "ERROR") ||
      ticket.accountId !== draft.accountId ||
      ticket.product !== draft.product ||
      ticket.contextKey !== validation.contextKey
    )
      throw new ServiceError(
        "FORBIDDEN",
        "尚未通过账户权限与配置校验，不能提交。",
      );
    const value = await this.request<ConfigurationTask>("tasks", "POST", {
      configuration: draft,
      capabilityVersion: cap.version,
      validationToken: validation.validationToken,
      idempotencyKey: ticket.idempotencyKey,
    });
    return this.task(value, ticket);
  }
  async result(ticket: SubmissionTicket): Promise<ConfigurationTask> {
    const query = new URLSearchParams({
      product: ticket.product,
      accountId: ticket.accountId,
      idempotencyKey: ticket.idempotencyKey,
    });
    return this.task(
      await this.request<ConfigurationTask>(`tasks?${query}`),
      ticket,
    );
  }
  async cancel(ticket: SubmissionTicket, result: ConfigurationTask): Promise<ConfigurationTask> {
    this.task(result, ticket);
    if (!result.cancellable || !["PENDING", "AWAITING_APPROVAL", "PAYMENT_REQUIRED"].includes(result.state))
      throw new ServiceError("FORBIDDEN", "当前任务不允许撤回。");
    return this.task(await this.request<ConfigurationTask>(
      `tasks/${encodeURIComponent(result.taskId)}/cancellations`, "POST", {
        product: ticket.product, accountId: ticket.accountId,
        expectedVersion: result.version, originalIdempotencyKey: ticket.idempotencyKey,
        idempotencyKey: crypto.randomUUID(),
      }), ticket);
  }
  private task(
    value: ConfigurationTask,
    ticket: SubmissionTicket,
  ): ConfigurationTask {
    this.envelope(value);
    if (
      value.contextKey !== ticket.contextKey ||
      value.idempotencyKey !== ticket.idempotencyKey ||
      !text(value.taskId) ||
      !value.taskId ||
      ![
        "PENDING",
        "AWAITING_APPROVAL",
        "PAYMENT_REQUIRED",
        "PROCESSING",
        "SUCCEEDED",
        "FAILED",
        "REJECTED","CANCELLED",
      ].includes(value.state) ||
      !(value.businessId === null || text(value.businessId)) ||
      !(value.platformStatus === null || text(value.platformStatus)) ||
      !text(value.message) ||
      !(value.approvalId===null||text(value.approvalId))||!Number.isInteger(value.version)||value.version<1||typeof value.cancellable!=="boolean"||
      !["PENDING", "MATCHED", "UNKNOWN", "MISMATCH"].includes(
        value.verification,
      ) ||
      (value.state === "SUCCEEDED" &&
        (!value.businessId || value.verification !== "MATCHED"))
    )
      throw invalid(
        "后端任务结果缺少有效业务 ID 或上下文不匹配，保持结果待核查。",
      );
    return value;
  }
  async estimate(
    draft: AdvertisingDraft,
    signal?: AbortSignal,
  ): Promise<AudienceEstimate> {
    const value = await this.request<AudienceEstimate>(
      "audience-estimates",
      "POST",
      { configuration: draft },
      signal,
    );
    this.envelope(value, configurationContext(draft));
    if (
      !Number.isFinite(value.lower) ||
      !Number.isFinite(value.upper) ||
      value.lower < 0 ||
      value.upper < value.lower ||
      !Number.isFinite(Date.parse(value.estimatedAt)) ||
      !text(value.description)
    )
      throw invalid();
    return value;
  }
  /** File bytes stay in the existing content factory. This only requests its
   * authorized upload workflow; the frontend never invents a platform asset ID. */
  async uploadCreative(
    context: ConfigurationContext,
    file: File,
  ): Promise<SelectedResource> {
    const body = new FormData();
    body.set("context", JSON.stringify(context));
    body.set("file", file);
    const value = await this.request<{
      contractVersion: string;
      source: string;
      tenantId: string;
      contextKey: string;
      resource: SelectedResource;
    }>("materials", "POST", body);
    this.envelope(value, context);
    const r = value.resource;
    if (
      !r ||
      !text(r.id) ||
      !r.id ||
      !text(r.platformId) ||
      !r.platformId ||
      !text(r.sourceMaterialId) ||
      !r.sourceMaterialId ||
      !text(r.name) ||
      typeof r.eligible !== "boolean"
    )
      throw invalid();
    return r;
  }
}
export function configurationError(error: unknown): string {
  if (error instanceof ServiceError) {
    if (error.code === "NO_DATA")
      return "接口待接入：后端尚未提供此配置资源，当前没有真实可选数据。";
    if (error.code === "FORBIDDEN" || error.code === "UNAUTHENTICATED")
      return "需要授权：当前账户或成员无权读取此配置，请检查账户绑定与接口权限。";
    return error.message;
  }
  return error instanceof Error ? error.message : "配置数据暂时无法加载。";
}
