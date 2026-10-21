import type { FrontendSnapshot, RiskPolicy } from "@/lib/domain";
import {
  commandActions,
  currentPlanValue,
  isHandoff,
  validateJobValue,
  type CommandJob,
  type CreateJobRequest,
  type JobVersionRequest,
  type MockScenario,
  type ReportingScope,
} from "@/lib/command-job";
import { normalizeScope } from "@/lib/reporting";
import {
  ServiceError,
  type FrontendService,
  type LoginRequest,
} from "./contracts";
import { createMockSnapshot } from "./mock-data";
import { createMockJob, interpretMockCommand, mockRisk } from "./mock-command";
import {
  MockIdentityAdapter,
  type MockStorage,
  identityKey,
} from "./mock-identity";
import { migrateG1a } from "./mock-migration";
export const snapshotKey = "yuliang:command-jobs:v1";
export const legacySnapshotKey = "yuliang:g1a:mock-snapshot";
const changedEvent = "yuliang:g1a:changed";
const terminal = new Set([
  "SUCCEEDED",
  "FAILED",
  "REJECTED",
  "CANCELLED",
  "EXPIRED",
  "BLOCKED",
  "MANUAL_REQUIRED",
]);

export class MockAdapter implements FrontendService {
  readonly mode = "mock" as const;
  private identity: MockIdentityAdapter;
  constructor(
    private storage: MockStorage,
    private notify = () => {},
    private clock = () => new Date(),
  ) {
    this.identity = new MockIdentityAdapter(storage);
  }
  getIdentity() {
    return this.identity.getIdentity();
  }
  async login(input: LoginRequest) {
    const actor = await this.identity.login(input);
    this.notify();
    return actor;
  }
  async logout() {
    await this.identity.logout();
    this.notify();
  }
  async resetDemo() {
    const data = this.seed();
    this.storage.setItem(snapshotKey, JSON.stringify(data));
    this.notify();
  }
  private at() {
    return this.clock().toISOString();
  }
  private seed() {
    const data = createMockSnapshot();
    const job = createMockJob(
      {
        source: "AUTOPILOT",
        shop_id: "shop-tea",
        target_type: "AD_PLAN",
        target_id: "plan-qc",
        action_type: "CHANGE_DAILY_BUDGET",
        new_value: { kind: "MONEY", currency: "CNY", amount_minor: 500000 },
        intent: "自主投放演示：提出千川A计划预算申请",
        idempotency_key: "mock-autopilot-seed",
      },
      data,
      this.at(),
    );
    job.job_id = "job-seed-autopilot";
    job.status = "PENDING_APPROVAL";
    job.approval.state = "PENDING";
    job.history.push({
      status: "PENDING_APPROVAL",
      at: this.at(),
      detail: "预置自主投放 Mock 示例，仍需人工审批",
    });
    data.tasks.push(job);
    return data;
  }
  private async requirePermission(permission: string) {
    const actor = await this.getIdentity();
    if (!actor)
      throw new ServiceError("UNAUTHENTICATED", "请先进入 Mock 演示。");
    if (!actor.permissions.includes(permission))
      throw new ServiceError("FORBIDDEN", "当前身份没有此项演示权限。");
    return actor;
  }
  async getSnapshot(): Promise<FrontendSnapshot> {
    await this.requirePermission("snapshot.read");
    const value = this.storage.getItem(snapshotKey);
    let data: FrontendSnapshot;
    try {
      if (value) {
        data = JSON.parse(value);
        if (
          data.schemaVersion !== "command-job-v1.0" ||
          data.source !== "MOCK" ||
          !Array.isArray(data.tasks) ||
          !data.scope
        )
          throw new Error();
      } else {
        data = this.seed();
        const legacy = this.storage.getItem(legacySnapshotKey);
        if (legacy) data = migrateG1a(legacy, data, this.at());
        this.storage.setItem(snapshotKey, JSON.stringify(data));
      }
    } catch {
      throw new ServiceError(
        "INVALID_RESPONSE",
        "演示存储损坏或版本不兼容。G1-A 原存储未被覆盖，可恢复演示数据后重试。",
      );
    }
    const revision = data.revision;
    let changed = false;
    for (const job of data.tasks) {
      if (
        !terminal.has(job.status) &&
        job.status !== "EXECUTING" &&
        job.status !== "VERIFYING" &&
        Date.parse(job.expires_at) <= this.clock().getTime()
      ) {
        this.transition(job, "EXPIRED", "审批/申请超过有效期");
        job.error = {
          code: "EXPIRED",
          message: "申请已过期，不能继续审批或执行。",
          retryable: false,
          at: this.at(),
        };
        changed = true;
      }
    }
    if (changed) this.save(data, revision);
    return data;
  }
  private save(data: FrontendSnapshot, expected: number) {
    const current = this.storage.getItem(snapshotKey);
    if (current && JSON.parse(current).revision !== expected)
      throw new ServiceError(
        "CONFLICT",
        "演示状态已在另一个页面变化，请刷新重试。",
      );
    data.revision = expected + 1;
    try {
      this.storage.setItem(snapshotKey, JSON.stringify(data));
    } catch {
      throw new ServiceError("CONFLICT", "浏览器无法保存演示状态。");
    }
    this.notify();
  }
  private transition(
    job: CommandJob,
    status: CommandJob["status"],
    detail: string,
  ) {
    job.status = status;
    job.updated_at = this.at();
    job.version += 1;
    job.history.push({ status, at: this.at(), detail });
  }
  private async editable(input: JobVersionRequest, permission: string) {
    const actor = await this.requirePermission(permission),
      data = await this.getSnapshot();
    const job = data.tasks.find(
      (x) =>
        x.job_id === input.job_id &&
        x.tenant_id === actor.tenantId &&
        actor.shopIds.includes(x.shop_id),
    );
    if (!job || job.version !== input.expected_version)
      throw new ServiceError(
        "CONFLICT",
        "任务不存在、无权访问或版本已变化，请刷新后检查。",
      );
    return { actor, data, job, revision: data.revision };
  }
  private fresh(job: CommandJob, data: FrontendSnapshot): boolean {
    const plan = data.plans.find((plan) => plan.id === job.target_id),
      policy = data.riskPolicies[0];
    const stale =
      !isHandoff(job.action_type) &&
      (!plan ||
        JSON.stringify(currentPlanValue(plan, job.action_type)) !==
          JSON.stringify(job.old_value));
    const risk = mockRisk(job, data, plan, policy, this.at());
    if (
      stale ||
      policy.version !== job.risk_summary.policy_version ||
      risk.result === "BLOCKED"
    ) {
      const inFlight = job.status === "EXECUTING" || job.status === "VERIFYING";
      this.transition(
        job,
        inFlight ? "FAILED" : "BLOCKED",
        "对象、策略或风险变化，停止推进",
      );
      job.error = {
        code: stale
          ? "STALE_TARGET"
          : policy.version !== job.risk_summary.policy_version
            ? "POLICY_CHANGED"
            : (risk.checks.find((check) => !check.passed)?.code ??
              "RISK_BLOCKED"),
        message:
          "Mock：对象旧值、策略版本或风险条件已变化，请重新提出申请。正式风控由后端执行。",
        retryable: false,
        at: this.at(),
      };
      job.risk_summary = risk;
      return false;
    }
    job.risk_summary = risk;
    return true;
  }
  async interpretCommand(input: { text: string; scope: ReportingScope }) {
    await this.requirePermission("task.create");
    return interpretMockCommand(
      input.text,
      await this.getSnapshot(),
      input.scope,
    );
  }
  async createJob(
    input: CreateJobRequest,
    scenario: MockScenario = "SUCCESS",
  ): Promise<CommandJob> {
    const actor = await this.requirePermission("task.create"),
      data = await this.getSnapshot(),
      revision = data.revision;
    if (
      !input.intent.trim() ||
      !input.idempotency_key ||
      !commandActions.includes(input.action_type) ||
      !validateJobValue(input.action_type, input.new_value)
    )
      throw new ServiceError(
        "INVALID_INPUT",
        "请提供明确对象及有效目标值；金额必须使用 CNY 整数分。",
      );
    const existing = data.tasks.find(
      (x) => x.idempotency_key === input.idempotency_key,
    );
    if (existing) {
      if (
        existing.action_type !== input.action_type ||
        existing.target_id !== input.target_id ||
        JSON.stringify(existing.new_value) !==
          JSON.stringify(input.new_value) ||
        existing.source !== input.source ||
        existing.shop_id !== input.shop_id ||
        existing.target_type !== input.target_type ||
        existing.intent !== input.intent.trim()
      )
        throw new ServiceError("CONFLICT", "同一幂等键不能用于不同申请。");
      return existing;
    }
    const handoff = isHandoff(input.action_type),
      target = handoff
        ? data.liveSessions.find((x) => x.id === input.target_id)
        : data.plans.find((x) => x.id === input.target_id);
    if (
      !target ||
      target.shopId !== input.shop_id ||
      target.tenantId !== actor.tenantId ||
      !actor.shopIds.includes(input.shop_id) ||
      input.target_type !== (handoff ? "LIVE_SESSION" : "AD_PLAN")
    )
      throw new ServiceError(
        "INVALID_INPUT",
        "请明确授权范围内的店铺、广告计划或直播场次。",
      );
    if (!["PC_MANUAL", "MOBILE_COMMAND", "AUTOPILOT"].includes(input.source))
      throw new ServiceError("INVALID_INPUT", "任务来源不正确。");
    const job = createMockJob(input, data, this.at(), scenario);
    data.tasks.unshift(job);
    this.save(data, revision);
    return job;
  }
  async confirmJob(input: JobVersionRequest) {
    const { data, job, revision } = await this.editable(input, "task.create");
    if (job.status !== "DRAFT")
      throw new ServiceError("CONFLICT", "只有待确认草稿可以提交审批。");
    if (this.fresh(job, data)) {
      this.transition(
        job,
        isHandoff(job.action_type) ? "MANUAL_REQUIRED" : "PENDING_APPROVAL",
        "用户确认对象、变更差异和 Mock 风险信息",
      );
      job.approval.state = isHandoff(job.action_type)
        ? "NOT_REQUESTED"
        : "PENDING";
    }
    this.save(data, revision);
    return job;
  }
  async reviewJob(
    input: JobVersionRequest & { decision: "APPROVE" | "REJECT" },
  ) {
    const { data, job, actor, revision } = await this.editable(
      input,
      "task.approve",
    );
    if (job.status !== "PENDING_APPROVAL")
      throw new ServiceError("CONFLICT", "当前任务不处于待审批状态。");
    if (input.decision === "REJECT" || this.fresh(job, data)) {
      job.approval = {
        state: input.decision === "APPROVE" ? "APPROVED" : "REJECTED",
        actor_id: actor.userId,
        decided_at: this.at(),
        method: "MANUAL",
        approved_version: job.version,
      };
      this.transition(
        job,
        input.decision === "APPROVE" ? "APPROVED" : "REJECTED",
        "人工 Mock 审批，不代表平台已执行",
      );
    }
    this.save(data, revision);
    return job;
  }
  async cancelJob(input: JobVersionRequest) {
    const { data, job, revision } = await this.editable(input, "task.create");
    if (!["DRAFT", "PENDING_APPROVAL", "APPROVED"].includes(job.status))
      throw new ServiceError(
        "CONFLICT",
        "当前状态不能取消。执行中结果需等待核验。",
      );
    this.transition(job, "CANCELLED", "用户取消尚未执行的申请");
    this.save(data, revision);
    return job;
  }
  async expireMockJob(input: JobVersionRequest) {
    const { data, job, revision } = await this.editable(input, "task.approve");
    if (!["DRAFT", "PENDING_APPROVAL", "APPROVED"].includes(job.status))
      throw new ServiceError("CONFLICT", "仅可模拟尚未执行任务的过期。");
    this.transition(job, "EXPIRED", "手动触发 Mock 过期场景");
    job.error = {
      code: "EXPIRED",
      message: "Mock 申请过期，未执行。",
      retryable: false,
      at: this.at(),
    };
    this.save(data, revision);
    return job;
  }
  async advanceMockJob(input: JobVersionRequest) {
    const { data, job, revision } = await this.editable(input, "task.execute");
    if (
      !["APPROVED", "EXECUTING", "VERIFYING"].includes(job.status) ||
      job.approval.state !== "APPROVED"
    )
      throw new ServiceError(
        "CONFLICT",
        "必须先审批；只允许按阶段推进模拟执行。",
      );
    if (!this.fresh(job, data)) {
      this.save(data, revision);
      return job;
    }
    if (job.status === "APPROVED") {
      this.transition(job, "EXECUTING", "开始 Mock 请求，尚无平台回执");
      job.execution_receipt = {
        receipt_id: crypto.randomUUID(),
        source: "MOCK",
        platform_receipt_id: null,
        accepted: null,
        received_at: null,
        verification: "PENDING",
        observed_value: null,
        verified_at: null,
      };
    } else if (job.status === "EXECUTING") {
      const failed = job.mock_scenario === "REQUEST_FAILURE",
        timeout = job.mock_scenario === "TIMEOUT";
      if (failed || timeout) {
        this.transition(
          job,
          "FAILED",
          timeout ? "Mock 请求超时，结果未知，不自动重试" : "Mock 请求失败",
        );
        job.error = {
          code: timeout ? "MOCK_TIMEOUT" : "MOCK_REQUEST_FAILED",
          message: timeout
            ? "模拟请求超时；无真实平台调用，结果显示未知，不会自动重试。"
            : "模拟请求失败，未改变计划。",
          retryable: false,
          at: this.at(),
        };
        job.execution_receipt!.accepted = timeout ? null : false;
        job.execution_receipt!.verification = "UNKNOWN";
        job.execution_receipt!.received_at = this.at();
      } else {
        this.transition(
          job,
          "VERIFYING",
          "收到 Mock 受理回执，等待模拟回读核验",
        );
        job.execution_receipt!.accepted = true;
        job.execution_receipt!.received_at = this.at();
      }
    } else {
      const receipt = job.execution_receipt!;
      receipt.verified_at = this.at();
      if (job.mock_scenario === "VERIFY_FAILURE") {
        receipt.verification = "MISMATCH";
        receipt.observed_value = job.old_value;
        this.transition(job, "FAILED", "Mock 核验不一致，不能显示成功");
        job.error = {
          code: "MOCK_VERIFY_MISMATCH",
          message: "模拟回读与目标值不一致，未投影目标参数。",
          retryable: false,
          at: this.at(),
        };
      } else {
        receipt.verification = "MATCHED";
        receipt.observed_value = job.new_value;
        const plan = data.plans.find((x) => x.id === job.target_id)!;
        if (
          job.action_type === "CHANGE_DAILY_BUDGET" &&
          job.new_value.kind === "MONEY"
        )
          plan.budgetMinor = job.new_value.amount_minor;
        else if (
          job.action_type === "CHANGE_BID" &&
          job.new_value.kind === "MONEY"
        )
          plan.bidMinor = job.new_value.amount_minor;
        else if (
          job.action_type === "SET_ROI_TARGET" &&
          job.new_value.kind === "RATIO"
        )
          plan.targetRoi = job.new_value.value;
        else if (job.new_value.kind === "BOOLEAN")
          plan.enabled = job.new_value.value;
        this.transition(
          job,
          "SUCCEEDED",
          "Mock 回读匹配，演示参数已投影；不是平台成功",
        );
      }
    }
    this.save(data, revision);
    return job;
  }
  async updatePolicy(input: RiskPolicy) {
    await this.requirePermission("policy.edit");
    const data = await this.getSnapshot(),
      revision = data.revision,
      policy = data.riskPolicies[0];
    if (input.version !== policy.version)
      throw new ServiceError("CONFLICT", "目标或风控参数已在其他页面变化。");
    if (
      input.id !== policy.id ||
      input.tenantId !== policy.tenantId ||
      !Number.isSafeInteger(input.dailyBudgetMinor) ||
      input.dailyBudgetMinor <= 0 ||
      !Number.isFinite(input.targetRoi) ||
      input.targetRoi <= 0 ||
      !Number.isFinite(input.maxChangePercent) ||
      input.maxChangePercent <= 0 ||
      input.maxChangePercent > 100 ||
      !Number.isSafeInteger(input.cooldownMinutes) ||
      input.cooldownMinutes < 0 ||
      !["MANUAL", "COPILOT", "AUTOPILOT"].includes(input.mode)
    )
      throw new ServiceError(
        "INVALID_INPUT",
        "请填写有效预算、ROI、动作幅度及冷却期。",
      );
    data.riskPolicies[0] = { ...input, version: policy.version + 1 };
    this.save(data, revision);
    return data.riskPolicies[0];
  }
  async setScope(input: ReportingScope) {
    const actor = await this.requirePermission("snapshot.read"),
      data = await this.getSnapshot(),
      revision = data.revision;
    if (
      (input.shop_id !== "all" && !actor.shopIds.includes(input.shop_id)) ||
      !/^\d{4}-\d{2}-\d{2}$/.test(input.date) ||
      input.date !== data.scope.date ||
      input.timezone !== "Asia/Shanghai" ||
      (input.connector_id !== "all" &&
        !data.connectors.some((x) => x.id === input.connector_id))
    )
      throw new ServiceError("INVALID_INPUT", "统计范围无效或无权访问。");
    data.scope = normalizeScope(data, input);
    this.save(data, revision);
  }
  subscribe(onChange: () => void) {
    if (typeof window === "undefined") return () => {};
    const listener = (event: StorageEvent) => {
      if ([snapshotKey, identityKey, null].includes(event.key)) onChange();
    };
    window.addEventListener("storage", listener);
    window.addEventListener(changedEvent, onChange);
    return () => {
      window.removeEventListener("storage", listener);
      window.removeEventListener(changedEvent, onChange);
    };
  }
}
export function browserMockAdapter() {
  return new MockAdapter(window.localStorage, () =>
    window.dispatchEvent(new Event(changedEvent)),
  );
}
