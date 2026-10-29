import { money, type AdPlan } from "./domain";

export const commandActions = [
  "CHANGE_DAILY_BUDGET",
  "CHANGE_BID",
  "SET_ROI_TARGET",
  "PAUSE_AD_PLAN",
  "RESUME_AD_PLAN",
  "REQUEST_LIVE_START",
  "REQUEST_LIVE_END",
] as const;
export type CommandAction = (typeof commandActions)[number];
export type JobSource = "AUTOPILOT" | "PC_MANUAL" | "MOBILE_COMMAND";
export type JobValue =
  | { kind: "MONEY"; currency: "CNY"; amount_minor: number }
  | { kind: "RATIO"; value: number }
  | { kind: "BOOLEAN"; value: boolean }
  | { kind: "HANDOFF"; value: "START" | "END" };
export const jobStatuses = [
  "DRAFT",
  "PENDING_APPROVAL",
  "APPROVED",
  "EXECUTING",
  "VERIFYING",
  "SUCCEEDED",
  "FAILED",
  "REJECTED",
  "CANCELLED",
  "EXPIRED",
  "BLOCKED",
  "MANUAL_REQUIRED",
] as const;
export type JobStatus = (typeof jobStatuses)[number];
export const jobStatusNames: Record<JobStatus, string> = {
  DRAFT: "待对象与风险确认",
  PENDING_APPROVAL: "待审批",
  APPROVED: "已批准 · 未执行",
  EXECUTING: "执行中",
  VERIFYING: "待核验",
  SUCCEEDED: "已核验完成",
  FAILED: "失败",
  REJECTED: "已驳回",
  CANCELLED: "已取消",
  EXPIRED: "已过期",
  BLOCKED: "风控阻断",
  MANUAL_REQUIRED: "待人工交接",
};
export const actionNames: Record<CommandAction, string> = {
  CHANGE_DAILY_BUDGET: "广告日预算调整",
  CHANGE_BID: "广告出价调整",
  SET_ROI_TARGET: "投放ROI目标调整",
  PAUSE_AD_PLAN: "暂停广告计划",
  RESUME_AD_PLAN: "恢复广告计划",
  REQUEST_LIVE_START: "直播开播人工交接",
  REQUEST_LIVE_END: "直播关播人工交接",
};
export type MockScenario =
  "SUCCESS" | "REQUEST_FAILURE" | "TIMEOUT" | "VERIFY_FAILURE";
export interface RiskSummary {
  source: "MOCK" | "SERVER";
  result: "REVIEW_REQUIRED" | "BLOCKED" | "MANUAL_ONLY";
  level: "LOW" | "MEDIUM" | "HIGH";
  checks: { code: string; passed: boolean; message: string }[];
  policy_version: number;
  checked_at: string;
}
export interface JobApproval {
  state: "NOT_REQUESTED" | "PENDING" | "APPROVED" | "REJECTED";
  actor_id: string | null;
  decided_at: string | null;
  method: "MANUAL" | "SERVER_POLICY" | null;
  approved_version: number | null;
}
export interface JobReceipt {
  receipt_id: string;
  source: "MOCK" | "PLATFORM";
  platform_receipt_id: string | null;
  accepted: boolean | null;
  received_at: string | null;
  verification: "PENDING" | "MATCHED" | "MISMATCH" | "UNKNOWN";
  observed_value: JobValue | null;
  verified_at: string | null;
}
export interface JobError {
  code: string;
  message: string;
  retryable: boolean;
  at: string;
}
export interface CommandJob {
  schema_version: "1.0";
  job_id: string;
  source: JobSource;
  tenant_id: string;
  shop_id: string;
  account_id: string | null;
  live_session_id: string | null;
  target_type: "AD_PLAN" | "LIVE_SESSION";
  target_id: string;
  action_type: CommandAction;
  old_value: JobValue | null;
  new_value: JobValue;
  intent: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  expires_at: string;
  risk_summary: RiskSummary;
  approval: JobApproval;
  execution_receipt: JobReceipt | null;
  error: JobError | null;
  version: number;
  idempotency_key: string;
  mode: "MOCK" | "API";
  history: { status: JobStatus; at: string; detail: string }[];
  mock_scenario?: MockScenario;
}
export interface ReportingScope {
  shop_id: string | "all";
  live_session_id: string | "all";
  connector_id: string | "all";
  date: string;
  timezone: "Asia/Shanghai";
}
export interface CreateJobRequest {
  source: JobSource;
  shop_id: string;
  target_type: CommandJob["target_type"];
  target_id: string;
  action_type: CommandAction;
  new_value: JobValue;
  intent: string;
  idempotency_key: string;
}
export interface JobVersionRequest {
  job_id: string;
  expected_version: number;
}
export type CommandInterpretation =
  | { kind: "QUERY"; text: string; live_session_id: string | null }
  | {
      kind: "ACTION";
      text: string;
      action_type: CommandAction;
      new_value: JobValue;
      target_type: CommandJob["target_type"];
      candidate_ids: string[];
    }
  | { kind: "UNRECOGNIZED"; text: string };
export function isHandoff(action: CommandAction) {
  return action === "REQUEST_LIVE_START" || action === "REQUEST_LIVE_END";
}
export function currentPlanValue(
  plan: AdPlan,
  action: CommandAction,
): JobValue | null {
  if (action === "CHANGE_DAILY_BUDGET") {
    if (plan.budgetMinor === null) return null;
    return { kind: "MONEY", currency: "CNY", amount_minor: plan.budgetMinor };
  }
  if (action === "CHANGE_BID") {
    if (plan.bidMinor === null) return null;
    return { kind: "MONEY", currency: "CNY", amount_minor: plan.bidMinor };
  }
  if (action === "SET_ROI_TARGET") {
    if (plan.targetRoi === null) return null;
    return { kind: "RATIO", value: plan.targetRoi };
  }
  // PAUSE / RESUME 使用当前启停状态；未知时不记录旧值
  if (plan.enabled === null) return null;
  return { kind: "BOOLEAN", value: plan.enabled };
}
export function formatJobValue(value: JobValue | null) {
  return !value
    ? "不适用"
    : value.kind === "MONEY"
      ? `${money(value.amount_minor)} CNY`
      : value.kind === "RATIO"
        ? `${value.value}（ROI，无单位）`
        : value.kind === "BOOLEAN"
          ? value.value
            ? "开启"
            : "暂停"
          : value.value === "START"
            ? "请求开播"
            : "请求关播";
}
export function jobDelta(job: Pick<CommandJob, "old_value" | "new_value">) {
  const before = job.old_value,
    after = job.new_value;
  if (before?.kind === "MONEY" && after.kind === "MONEY") {
    const difference = after.amount_minor - before.amount_minor;
    return `${difference > 0 ? "+" : difference < 0 ? "−" : ""}${money(Math.abs(difference))} CNY`;
  }
  if (before?.kind === "RATIO" && after.kind === "RATIO") {
    const difference = Number((after.value - before.value).toFixed(4));
    return `${difference > 0 ? "+" : ""}${difference}（ROI）`;
  }
  return "状态 / 人工交接变更";
}
export function parseMoneyInput(value: string): number {
  if (!/^\d+(\.\d{1,2})?$/.test(value))
    throw new Error("金额必须为正数，最多两位小数，单位为元。");
  const [whole, decimal = ""] = value.split(".");
  const minor = Number(whole) * 100 + Number(decimal.padEnd(2, "0"));
  if (!Number.isSafeInteger(minor) || minor <= 0)
    throw new Error("金额超出有效范围。");
  return minor;
}
export function validateJobValue(
  action: CommandAction,
  value: JobValue,
): boolean {
  if (!value || typeof value !== "object") return false;
  if (action === "CHANGE_DAILY_BUDGET" || action === "CHANGE_BID")
    return (
      value.kind === "MONEY" &&
      value.currency === "CNY" &&
      Number.isSafeInteger(value.amount_minor) &&
      value.amount_minor > 0
    );
  if (action === "SET_ROI_TARGET")
    return (
      value.kind === "RATIO" && Number.isFinite(value.value) && value.value > 0
    );
  if (action === "PAUSE_AD_PLAN" || action === "RESUME_AD_PLAN")
    return (
      value.kind === "BOOLEAN" && value.value === (action === "RESUME_AD_PLAN")
    );
  return (
    value.kind === "HANDOFF" &&
    value.value === (action === "REQUEST_LIVE_START" ? "START" : "END")
  );
}
export function displayTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
