import type { Identity } from "../lib/domain";
import { ApiClient } from "./api-client";
import { ServiceError, type FrontendService } from "./contracts";
import {
  integrationScopeKey,
  platformApiCatalog,
  productCatalog,
  providerOf,
  queryBlock,
  resourceNames,
  safeMetricKeys,
  verificationStageNames,
  type IntegrationContext,
  type IntegrationScope,
  type PlatformResource,
  type PlatformResourcePage,
  type PlatformResourceRow,
  type ProductLine,
} from "../lib/platform-catalog";
const invalid = () =>
  new ServiceError(
    "INVALID_RESPONSE",
    "多平台接口返回了不匹配的范围、权限证明或数据结构，已停止使用该响应。",
  );
const obj = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown): v is string => typeof v === "string";
export interface PlatformChange {
  catalogId: string;
  targetId: string;
  nativeId: string;
  expectedVersion: number;
  changes: Partial<Record<"name" | "budget" | "bid" | "status", string>>;
  reason: string;
}
export interface PlatformChangePreview {
  contractVersion: "platform-workspace-v9.0";
  source: "API";
  scopeKey: string;
  token: string;
  expiresAt: string;
  before: Record<string, string | number | null>;
  after: Record<string, string | number | null>;
  checks: { passed: boolean; message: string }[];
  requiresApproval: boolean;
}
export interface PlatformCommandResult {
  contractVersion: "platform-workspace-v9.0";
  source: "API";
  scopeKey: string;
  idempotencyKey: string;
  taskId: string;
  businessId: string | null;
  approvalId: string | null;
  state: string;
  verification: "PENDING" | "MATCHED" | "UNKNOWN" | "MISMATCH";
  version: number;
  cancellable: boolean;
  message: string;
}
/** Unified backend contract. Never invokes platform URLs, interprets GET suffixes,
 * uses a different product's adapter, or falls back to mock business data. */
export class PlatformService {
  private previews = new Map<string, string>();
  constructor(
    private service: FrontendService,
    private identity: Identity,
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
        "演示模式只展示接口依据；不生成正式账户、标签、余额或投放结果。",
      );
    return this.service.deliveryRequest<T>(
      `/v1/delivery/integrations/${path}`,
      method,
      body,
      signal,
    );
  }
  private envelope(v: unknown, scope?: IntegrationScope) {
    if (
      !obj(v) ||
      v.contractVersion !== "platform-workspace-v9.0" ||
      v.source !== "API" ||
      (scope && v.scopeKey !== integrationScopeKey(scope))
    )
      throw invalid();
  }
  async context(
    productLine: ProductLine,
    signal?: AbortSignal,
  ): Promise<IntegrationContext> {
    const value = await this.request<IntegrationContext>(
      `context?productLine=${encodeURIComponent(productLine)}`,
      "GET",
      undefined,
      signal,
    );
    this.envelope(value);
    if (
      value.tenantId !== this.identity.tenantId ||
      value.productLine !== productLine ||
      !Number.isFinite(Date.parse(value.checkedAt)) ||
      !Number.isFinite(Date.parse(value.expiresAt)) ||
      Date.parse(value.expiresAt) <= Date.now() ||
      ![
        value.entities,
        value.shops,
        value.accounts,
        value.links,
        value.operations,
      ].every(Array.isArray)
    )
      throw invalid();
    if (
      value.entities.some((e) => !e || !str(e.id) || !e.id || !str(e.name)) ||
      value.shops.some(
        (s) =>
          !s ||
          !this.identity.shopIds.includes(s.id) ||
          !str(s.name) ||
          !value.entities.some((e) => e.id === s.businessEntityId),
      ) ||
      value.accounts.some(
        (a) =>
          !a ||
          !str(a.id) ||
          !str(a.nativeId) ||
          !str(a.name) ||
          a.provider !== providerOf(productLine) ||
          a.productLine !== productLine ||
          !value.entities.some((e) => e.id === a.businessEntityId) ||
          ![
            "AUTHORIZED",
            "UNAUTHORIZED",
            "FORBIDDEN",
            "NOT_INTEGRATED",
          ].includes(a.authorization),
      ) ||
      value.links.some(
        (l) =>
          !l ||
          !value.shops.some((s) => s.id === l.shopId) ||
          !value.accounts.some((a) => a.id === l.accountId) ||
          !["BOUND", "AUTHORIZED"].includes(l.state),
      )
    )
      throw invalid();
    const records = productCatalog(productLine);
    for (const operation of value.operations) {
      if (
        !operation ||
        !records.some((r) => r.id === operation.catalogId) ||
        !Object.hasOwn(resourceNames, operation.resource) ||
        !["READ", "WRITE"].includes(operation.kind) ||
        !str(operation.permission) ||
        !Array.isArray(operation.accountIds) ||
        operation.accountIds.some(
          (id) => !value.accounts.some((a) => a.id === id),
        ) ||
        !Array.isArray(operation.editableFields) ||
        operation.editableFields.some(
          (id) => !["name", "budget", "bid", "status"].includes(id),
        ) ||
        !obj(operation.evidence)
      )
        throw invalid();
      for (const stage of Object.keys(verificationStageNames)) {
        const proof =
          operation.evidence[stage as keyof typeof verificationStageNames];
        if (
          !proof ||
          typeof proof.verified !== "boolean" ||
          !str(proof.reason) ||
          (proof.verified && (!str(proof.proofId) || !proof.proofId))
        )
          throw invalid();
      }
      // A directory record without a path cannot be promoted into an execution route.
      if (
        operation.evidence.path.verified &&
        !records.find((r) => r.id === operation.catalogId)?.normalizedPath
      )
        throw invalid();
    }
    return value;
  }
  private assertScope(context: IntegrationContext, scope: IntegrationScope) {
    if (
      scope.tenantId !== this.identity.tenantId ||
      scope.productLine !== context.productLine ||
      scope.provider !== providerOf(scope.productLine) ||
      !context.entities.some((e) => e.id === scope.businessEntityId) ||
      !context.shops.some(
        (s) =>
          s.id === scope.shopId &&
          s.businessEntityId === scope.businessEntityId,
      ) ||
      !this.identity.shopIds.includes(scope.shopId)
    )
      throw new ServiceError("FORBIDDEN", "经营主体或店铺不在当前授权范围。");
    if (
      scope.productLine !== "wxshop" &&
      (!context.accounts.some(
        (a) =>
          a.id === scope.accountId &&
          a.businessEntityId === scope.businessEntityId &&
          a.authorization === "AUTHORIZED",
      ) ||
        !context.links.some(
          (l) =>
            l.accountId === scope.accountId &&
            l.shopId === scope.shopId &&
            l.state === "AUTHORIZED",
        ))
    )
      throw new ServiceError("FORBIDDEN", "账户、产品线与店铺缺少授权关联。");
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(scope.start) ||
      !/^\d{4}-\d{2}-\d{2}$/.test(scope.end) ||
      scope.start > scope.end
    )
      throw new ServiceError("INVALID_INPUT", "请选择有效的日期范围。");
  }
  async query(
    context: IntegrationContext,
    scope: IntegrationScope,
    resource: PlatformResource,
    filters: { search?: string; cursor?: string; nativeParentId?: string } = {},
    signal?: AbortSignal,
  ): Promise<PlatformResourcePage> {
    this.assertScope(context, scope);
    const block = queryBlock(
      context,
      scope,
      resource,
      this.identity.permissions,
    );
    if (block) throw new ServiceError("FORBIDDEN", block);
    const value = await this.request<PlatformResourcePage>(
      "queries",
      "POST",
      { scope, resource, filters },
      signal,
    );
    this.envelope(value, scope);
    if (
      value.resource !== resource ||
      !Array.isArray(value.rows) ||
      !(value.nextCursor === null || str(value.nextCursor)) ||
      !Number.isFinite(Date.parse(value.syncedAt)) ||
      value.timezone !== "Asia/Shanghai" ||
      !str(value.granularity) ||
      !str(value.refundHandling) ||
      !str(value.deduplication) ||
      !Array.isArray(value.warnings) ||
      value.warnings.some((w) => !str(w))
    )
      throw invalid();
    const rows: PlatformResourceRow[] = value.rows.map((row) => {
      if (
        !row ||
        row.scopeKey !== integrationScopeKey(scope) ||
        ![row.id, row.nativeId, row.name, row.status, row.updatedAt].every(
          str,
        ) ||
        !(row.version === null || Number.isInteger(row.version)) ||
        !obj(row.metrics) ||
        !Array.isArray(row.related) ||
        row.related.some(
          (r) =>
            !r || !["PRODUCT", "LIVE", "PLAN"].includes(r.type) || !str(r.id),
        )
      )
        throw invalid();
      const metrics = Object.fromEntries(
        safeMetricKeys
          .filter((k) => row.metrics[k] !== undefined)
          .map((k) => {
            const n = row.metrics[k];
            if (
              n !== null &&
              (typeof n !== "number" || !Number.isFinite(n) || n < 0)
            )
              throw invalid();
            return [k, n];
          }),
      );
      if (
        scope.productLine === "wxshop" &&
        row.moneyType !== null &&
        row.moneyType !== "MERCHANT_SETTLEMENT"
      )
        throw invalid();
      if (
        scope.productLine !== "wxshop" &&
        row.moneyType === "MERCHANT_SETTLEMENT"
      )
        throw invalid();
      // Deliberately strips unknown/raw fields, including buyer/phone/address/bank data.
      return {
        scopeKey: row.scopeKey,
        id: row.id,
        nativeId: row.nativeId,
        name: row.name,
        status: row.status,
        version: row.version,
        updatedAt: row.updatedAt,
        metrics,
        attributionWindow: row.attributionWindow ?? null,
        moneyType: row.moneyType,
        related: row.related.map((r) => ({ type: r.type, id: r.id })),
      };
    });
    return {
      contractVersion: value.contractVersion,
      source: value.source,
      scopeKey: value.scopeKey,
      resource: value.resource,
      rows,
      nextCursor: value.nextCursor,
      syncedAt: value.syncedAt,
      timezone: value.timezone,
      granularity: value.granularity,
      attributionWindow: value.attributionWindow,
      refundHandling: value.refundHandling,
      deduplication: value.deduplication,
      warnings: value.warnings,
    };
  }
  private writeRule(
    context: IntegrationContext,
    scope: IntegrationScope,
    change: PlatformChange,
  ) {
    this.assertScope(context, scope);
    const operation = context.operations.find(
      (o) =>
        o.catalogId === change.catalogId &&
        o.kind === "WRITE" &&
        (scope.productLine === "wxshop" ||
          o.accountIds.includes(scope.accountId)),
    );
    const record = platformApiCatalog.find((r) => r.id === change.catalogId);
    if (
      Date.parse(context.expiresAt) <= Date.now() ||
      !operation ||
      !record?.normalizedPath ||
      !Object.values(operation.evidence).every((p) => p.verified) ||
      !this.identity.permissions.includes(operation.permission) ||
      !Number.isInteger(change.expectedVersion) ||
      change.expectedVersion < 1 ||
      !change.reason.trim() ||
      !change.targetId ||
      !change.nativeId ||
      !Object.keys(change.changes).length ||
      Object.keys(change.changes).some(
        (k) =>
          !operation.editableFields.includes(
            k as keyof PlatformChange["changes"],
          ),
      )
    )
      throw new ServiceError(
        "FORBIDDEN",
        "操作的目录、路径、scope、授权、后端、参数或执行核验未全部通过。",
      );
    return operation;
  }
  async preview(
    context: IntegrationContext,
    scope: IntegrationScope,
    change: PlatformChange,
  ): Promise<PlatformChangePreview> {
    this.writeRule(context, scope, change);
    const value = await this.request<PlatformChangePreview>(
      "previews",
      "POST",
      { scope, change },
    );
    this.envelope(value, scope);
    if (
      !str(value.token) ||
      !value.token ||
      !Number.isFinite(Date.parse(value.expiresAt)) ||
      Date.parse(value.expiresAt) <= Date.now() ||
      !obj(value.before) ||
      !obj(value.after) ||
      !Array.isArray(value.checks) ||
      !value.checks.length ||
      value.checks.some(
        (c) => !c || typeof c.passed !== "boolean" || !str(c.message),
      ) ||
      typeof value.requiresApproval !== "boolean"
    )
      throw invalid();
    this.previews.set(value.token, JSON.stringify({ scope, change }));
    return value;
  }
  async command(
    context: IntegrationContext,
    scope: IntegrationScope,
    change: PlatformChange,
    preview: PlatformChangePreview,
    idempotencyKey: string,
  ): Promise<PlatformCommandResult> {
    this.writeRule(context, scope, change);
    if (
      this.previews.get(preview.token) !== JSON.stringify({ scope, change }) ||
      Date.parse(preview.expiresAt) <= Date.now() ||
      preview.checks.some((c) => !c.passed) ||
      !idempotencyKey
    )
      throw new ServiceError(
        "FORBIDDEN",
        "配置已变化或预检未通过，请重新预览。",
      );
    return this.resultEnvelope(
      await this.request<PlatformCommandResult>("commands", "POST", {
        scope,
        change,
        previewToken: preview.token,
        idempotencyKey,
      }),
      scope,
      idempotencyKey,
    );
  }
  async result(scope: IntegrationScope, idempotencyKey: string) {
    return this.resultEnvelope(
      await this.request<PlatformCommandResult>(
        `commands?${new URLSearchParams({ scopeKey: integrationScopeKey(scope), idempotencyKey })}`,
      ),
      scope,
      idempotencyKey,
    );
  }
  private resultEnvelope(
    value: PlatformCommandResult,
    scope: IntegrationScope,
    key: string,
  ) {
    this.envelope(value, scope);
    if (
      value.idempotencyKey !== key ||
      !str(value.taskId) ||
      !value.taskId ||
      !str(value.state) ||
      !str(value.message) ||
      !Number.isInteger(value.version) ||
      typeof value.cancellable !== "boolean" ||
      !["PENDING", "MATCHED", "UNKNOWN", "MISMATCH"].includes(
        value.verification,
      ) ||
      (value.state === "SUCCEEDED" &&
        (!value.businessId || value.verification !== "MATCHED"))
    )
      throw invalid();
    return value;
  }
  async cancel(
    scope: IntegrationScope,
    result: PlatformCommandResult,
    idempotencyKey: string,
  ) {
    if (
      !result.cancellable ||
      ["SUCCEEDED", "EXECUTING", "VERIFYING"].includes(result.state)
    )
      throw new ServiceError("FORBIDDEN", "该任务当前不允许撤回。");
    return this.resultEnvelope(
      await this.request<PlatformCommandResult>(
        `commands/${encodeURIComponent(result.taskId)}/cancellations`,
        "POST",
        { scope, expectedVersion: result.version, idempotencyKey },
      ),
      scope,
      idempotencyKey,
    );
  }
}
