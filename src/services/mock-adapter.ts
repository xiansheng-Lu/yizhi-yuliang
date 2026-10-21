import {
  type Approval,
  type CommandTask,
  type CreateTaskRequest,
  type FrontendSnapshot,
} from "@/lib/domain";
import {
  ServiceError,
  type ApproveTaskRequest,
  type FrontendService,
  type LoginRequest,
} from "./contracts";
import { createMockSnapshot } from "./mock-data";
import {
  MockIdentityAdapter,
  type MockStorage,
  identityKey,
} from "./mock-identity";
export const snapshotKey = "yuliang:g1a:mock-snapshot";
const changedEvent = "yuliang:g1a:changed";

export class MockAdapter implements FrontendService {
  readonly mode = "mock" as const;
  private identity: MockIdentityAdapter;
  constructor(
    private storage: MockStorage,
    private notify = () => {},
  ) {
    this.identity = new MockIdentityAdapter(storage);
  }
  getIdentity() {
    return this.identity.getIdentity();
  }
  async login(input: LoginRequest) {
    const identity = await this.identity.login(input);
    this.notify();
    return identity;
  }
  async logout() {
    await this.identity.logout();
    this.notify();
  }
  async resetDemo() {
    this.storage.removeItem(snapshotKey);
    this.notify();
  }
  private async requirePermission(permission: string) {
    const identity = await this.getIdentity();
    if (!identity)
      throw new ServiceError("UNAUTHENTICATED", "请先进入 Mock 演示。");
    if (!identity.permissions.includes(permission))
      throw new ServiceError("FORBIDDEN", "当前身份没有此项演示权限。");
    return identity;
  }
  async getSnapshot(): Promise<FrontendSnapshot> {
    await this.requirePermission("snapshot.read");
    const value = this.storage.getItem(snapshotKey);
    if (!value) return createMockSnapshot();
    try {
      const data = JSON.parse(value) as FrontendSnapshot;
      if (
        data.schemaVersion !== "g1a-draft-1" ||
        data.source !== "MOCK" ||
        !Array.isArray(data.tasks) ||
        !Array.isArray(data.plans) ||
        !Array.isArray(data.approvals)
      )
        throw new Error();
      return data;
    } catch {
      throw new ServiceError(
        "INVALID_RESPONSE",
        "演示存储版本不兼容或已损坏。请使用恢复演示数据入口。",
      );
    }
  }
  private save(data: FrontendSnapshot) {
    data.revision += 1;
    try {
      this.storage.setItem(snapshotKey, JSON.stringify(data));
    } catch {
      throw new ServiceError(
        "CONFLICT",
        "浏览器无法保存演示状态，请检查存储权限或可用空间。",
      );
    }
    this.notify();
  }
  async createTask(input: CreateTaskRequest): Promise<CommandTask> {
    const identity = await this.requirePermission("task.create");
    const data = await this.getSnapshot();
    const existing = data.tasks.find(
      (task) => task.idempotencyKey === input.idempotencyKey,
    );
    if (existing) return existing;
    if (!input.idempotencyKey || !input.intent.trim())
      throw new ServiceError("INVALID_INPUT", "请填写操作意图。");
    const action = input.action;
    const numeric =
      action.type === "AD_SET_BUDGET"
        ? action.budgetMinor
        : action.type === "AD_SET_BID"
          ? action.bidMinor
          : action.type === "AD_SET_ROI_TARGET"
            ? action.targetRoi
            : null;
    if (
      numeric !== null &&
      (!Number.isFinite(numeric) ||
        numeric <= 0 ||
        (action.type !== "AD_SET_ROI_TARGET" && !Number.isSafeInteger(numeric)))
    )
      throw new ServiceError(
        "INVALID_INPUT",
        "请输入有效正数；金额以整数分传递。",
      );
    const isHandoff =
      action.type === "REQUEST_LIVE_START" ||
      action.type === "REQUEST_LIVE_END";
    const plan = data.plans.find(
      (plan) =>
        plan.id === input.planId &&
        plan.tenantId === identity.tenantId &&
        identity.shopIds.includes(plan.shopId),
    );
    const live = data.liveSessions.find(
      (live) =>
        live.id === input.liveSessionId &&
        live.tenantId === identity.tenantId &&
        identity.shopIds.includes(live.shopId),
    );
    if (isHandoff ? !live : !plan)
      throw new ServiceError(
        "INVALID_INPUT",
        "请明确当前企业下的广告计划或直播场次。",
      );
    const task: CommandTask = {
      id: crypto.randomUUID(),
      tenantId: identity.tenantId,
      shopId: isHandoff ? live!.shopId : plan!.shopId,
      liveSessionId: isHandoff ? live!.id : plan!.liveSessionId,
      planId: isHandoff ? undefined : plan!.id,
      adAccountId: isHandoff ? undefined : plan!.adAccountId,
      action,
      origin: input.origin,
      intent: input.intent.trim(),
      status: isHandoff ? "MANUAL_REQUIRED" : "AWAITING_APPROVAL",
      createdAt: new Date().toISOString(),
      idempotencyKey: input.idempotencyKey,
      version: 1,
      mode: "MOCK",
    };
    data.tasks.unshift(task);
    data.riskChecks.push({
      id: crypto.randomUUID(),
      taskId: task.id,
      policyId: "policy-demo",
      status: "MOCK_NOT_EVALUATED",
      reason: "Mock 审批演示，未进行服务端风控，不会提交平台操作。",
    });
    this.save(data);
    return task;
  }
  async approveTask(input: ApproveTaskRequest): Promise<Approval> {
    const identity = await this.requirePermission("task.approve");
    const data = await this.getSnapshot();
    const task = data.tasks.find(
      (task) =>
        task.id === input.taskId &&
        task.tenantId === identity.tenantId &&
        identity.shopIds.includes(task.shopId),
    );
    if (
      !task ||
      task.version !== input.expectedVersion ||
      task.status !== "AWAITING_APPROVAL"
    )
      throw new ServiceError("CONFLICT", "任务状态已变化，请刷新后重新检查。");
    const approval: Approval = {
      id: crypto.randomUUID(),
      taskId: task.id,
      actorId: identity.userId,
      decision: input.decision,
      createdAt: new Date().toISOString(),
      taskVersion: task.version,
      mode: "MOCK",
    };
    task.status = input.decision === "APPROVE" ? "APPROVED" : "REJECTED";
    task.version += 1;
    data.approvals.push(approval);
    this.save(data);
    return approval;
  }
  subscribe(onChange: () => void) {
    if (typeof window === "undefined") return () => {};
    const listener = (e: StorageEvent) => {
      if ([snapshotKey, identityKey, null].includes(e.key)) onChange();
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
