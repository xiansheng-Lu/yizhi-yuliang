import type { FrontendSnapshot } from "@/lib/domain";
import type {
  CreateJobRequest,
  JobValue,
  CommandAction,
} from "@/lib/command-job";
import { validateJobValue } from "@/lib/command-job";
import { createMockJob } from "./mock-command";

/** Read G1-A data without overwriting its storage key or carrying approvals into V1. */
export function migrateG1a(
  raw: string,
  target: FrontendSnapshot,
  at: string,
): FrontendSnapshot {
  const old = JSON.parse(raw);
  if (
    old?.schemaVersion !== "g1a-draft-1" ||
    !Array.isArray(old.tasks) ||
    !Array.isArray(old.plans)
  )
    throw new Error("Unsupported G1-A state");
  for (const oldPlan of old.plans) {
    const plan = target.plans.find((plan) => plan.id === oldPlan.id);
    if (plan)
      for (const field of ["budgetMinor", "bidMinor", "targetRoi"] as const) {
        if (
          typeof oldPlan[field] === "number" &&
          Number.isFinite(oldPlan[field]) &&
          oldPlan[field] > 0
        )
          plan[field] = oldPlan[field];
      }
  }
  for (const task of old.tasks) {
    const map: Record<string, CommandAction> = {
      AD_SET_BUDGET: "CHANGE_DAILY_BUDGET",
      AD_SET_BID: "CHANGE_BID",
      AD_SET_ROI_TARGET: "SET_ROI_TARGET",
      AD_PAUSE: "PAUSE_AD_PLAN",
      AD_RESUME: "RESUME_AD_PLAN",
      REQUEST_LIVE_START: "REQUEST_LIVE_START",
      REQUEST_LIVE_END: "REQUEST_LIVE_END",
    };
    const action = map[task.action?.type];
    if (!action) continue;
    const liveAction =
        action === "REQUEST_LIVE_START" || action === "REQUEST_LIVE_END",
      plan = target.plans.find((plan) => plan.id === task.planId),
      live = target.liveSessions.find((live) => live.id === task.liveSessionId);
    if (liveAction ? !live : !plan) continue;
    const value: JobValue =
      action === "CHANGE_DAILY_BUDGET"
        ? {
            kind: "MONEY",
            currency: "CNY",
            amount_minor: task.action.budgetMinor,
          }
        : action === "CHANGE_BID"
          ? {
              kind: "MONEY",
              currency: "CNY",
              amount_minor: task.action.bidMinor,
            }
          : action === "SET_ROI_TARGET"
            ? { kind: "RATIO", value: task.action.targetRoi }
            : liveAction
              ? {
                  kind: "HANDOFF",
                  value: action === "REQUEST_LIVE_START" ? "START" : "END",
                }
              : { kind: "BOOLEAN", value: action === "RESUME_AD_PLAN" };
    if (!validateJobValue(action, value)) continue;
    const request: CreateJobRequest = {
      source:
        task.origin === "AUTOPILOT"
          ? "AUTOPILOT"
          : task.origin === "MOBILE"
            ? "MOBILE_COMMAND"
            : "PC_MANUAL",
      shop_id: liveAction ? live!.shopId : plan!.shopId,
      target_type: liveAction ? "LIVE_SESSION" : "AD_PLAN",
      target_id: liveAction ? live!.id : plan!.id,
      action_type: action,
      new_value: value,
      intent: task.intent || "G1-A 迁移任务",
      idempotency_key: `g1a:${task.id}`,
    };
    const job = createMockJob(request, target, at);
    job.job_id = `g1a:${task.id}`;
    job.history.push({
      status: job.status,
      at,
      detail: "从 G1-A 只读迁移；旧审批未自动继承，需按 V1 重新确认。",
    });
    target.tasks.push(job);
  }
  return target;
}
