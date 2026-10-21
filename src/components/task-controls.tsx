"use client";
import { useState, type FormEvent } from "react";
import { Check, PaperPlaneTilt, X } from "@phosphor-icons/react";
import {
  actionName,
  money,
  taskStatusNames,
  type FrontendSnapshot,
  type TaskAction,
} from "@/lib/domain";
import { useWorkspace } from "./provider";
import { Badge, EmptyState } from "./ui";

export function TaskComposer({
  snapshot,
  origin,
}: {
  snapshot: FrontendSnapshot;
  origin: "PC" | "MOBILE";
}) {
  const { service, reload } = useWorkspace();
  const [type, setType] = useState<TaskAction["type"]>("AD_SET_BUDGET"),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const handoff = type === "REQUEST_LIVE_START" || type === "REQUEST_LIVE_END";
  const numeric = ["AD_SET_BUDGET", "AD_SET_BID", "AD_SET_ROI_TARGET"].includes(
    type,
  );
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!service || busy) return;
    const fields = new FormData(e.currentTarget),
      value = Number(fields.get("value"));
    const action: TaskAction =
      type === "AD_SET_BUDGET"
        ? { type, budgetMinor: Math.round(value * 100) }
        : type === "AD_SET_BID"
          ? { type, bidMinor: Math.round(value * 100) }
          : type === "AD_SET_ROI_TARGET"
            ? { type, targetRoi: value }
            : { type };
    setBusy(true);
    setMessage("");
    try {
      const task = await service.createTask({
        origin,
        intent: String(fields.get("intent") ?? ""),
        planId: handoff ? undefined : String(fields.get("plan")),
        liveSessionId: handoff ? String(fields.get("live")) : undefined,
        action,
        idempotencyKey: crypto.randomUUID(),
      });
      setMessage(
        `${service.mode === "mock" ? "Mock 申请已保存" : "申请已提交"}：${taskStatusNames[task.status]}。尚未执行。`,
      );
      await reload();
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "申请未保存，请重试。",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="task-form" onSubmit={submit}>
      <label>
        运营意图
        <input
          name="intent"
          required
          maxLength={500}
          placeholder="例如：检查秋茶直播计划的预算调整申请"
        />
      </label>
      <div className="form-grid">
        <label>
          操作类型
          <select
            value={type}
            onChange={(e) => setType(e.target.value as TaskAction["type"])}
          >
            <option value="AD_SET_BUDGET">广告预算调整（元）</option>
            <option value="AD_SET_BID">广告出价调整（元）</option>
            <option value="AD_SET_ROI_TARGET">投放 ROI 目标调整（比值）</option>
            <option value="AD_PAUSE">暂停广告计划</option>
            <option value="AD_RESUME">恢复广告计划</option>
            <option value="REQUEST_LIVE_START">直播开播人工交接</option>
            <option value="REQUEST_LIVE_END">直播关播人工交接</option>
          </select>
        </label>
        {handoff ? (
          <label>
            直播场次
            <select name="live">
              {snapshot.liveSessions.map((live) => (
                <option key={live.id} value={live.id}>
                  {live.name}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label>
            广告计划
            <select name="plan">
              {snapshot.plans.map((plan) => (
                <option key={plan.id} value={plan.id}>
                  {plan.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {numeric && (
        <label>
          {type === "AD_SET_ROI_TARGET"
            ? "目标 ROI（无货币单位）"
            : "目标金额（元）"}
          <input
            key={type}
            name="value"
            type="number"
            min={type === "AD_SET_ROI_TARGET" ? "0.01" : "0.01"}
            step="0.01"
            required
            placeholder={
              type === "AD_SET_ROI_TARGET"
                ? "5.00"
                : type === "AD_SET_BUDGET"
                  ? "5000.00"
                  : "3.20"
            }
          />
        </label>
      )}
      <div className="notice">
        {handoff
          ? "仅建立直播负责人交接事项，不控制推流，也不改变广告状态。"
          : "本阶段只演示申请与审批。金额、权限与实际执行必须由服务端校验。"}
      </div>
      <button className="button primary" disabled={busy}>
        <PaperPlaneTilt size={17} />
        {busy
          ? "正在保存…"
          : service?.mode === "mock"
            ? "创建 Mock 申请"
            : "提交操作申请"}
      </button>
      {message && (
        <p role="status" className="feedback">
          {message}
        </p>
      )}
    </form>
  );
}
export function TaskList({
  snapshot,
  limit,
}: {
  snapshot: FrontendSnapshot;
  limit?: number;
}) {
  const { service, reload } = useWorkspace();
  const [busy, setBusy] = useState<string | null>(null),
    [message, setMessage] = useState("");
  const tasks = limit ? snapshot.tasks.slice(0, limit) : snapshot.tasks;
  async function decide(
    taskId: string,
    version: number,
    decision: "APPROVE" | "REJECT",
  ) {
    if (!service || busy) return;
    setBusy(taskId);
    setMessage("");
    try {
      await service.approveTask({ taskId, expectedVersion: version, decision });
      await reload();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "审批未完成");
    } finally {
      setBusy(null);
    }
  }
  if (!tasks.length)
    return (
      <EmptyState
        title="暂无指令任务"
        description="PC、自主投放与手机创建的任务会显示在同一列表。"
      />
    );
  return (
    <div className="task-list">
      {tasks.map((task) => {
        const plan = snapshot.plans.find((plan) => plan.id === task.planId);
        const action = task.action;
        const target =
          action.type === "AD_SET_BUDGET"
            ? money(action.budgetMinor)
            : action.type === "AD_SET_BID"
              ? money(action.bidMinor)
              : action.type === "AD_SET_ROI_TARGET"
                ? `${action.targetRoi}（ROI）`
                : "人工核对对象与状态";
        return (
          <article key={task.id} className="task-item">
            <div className="row between">
              <Badge
                tone={task.status === "AWAITING_APPROVAL" ? "warning" : "blue"}
              >
                {taskStatusNames[task.status]}
              </Badge>
              <small>
                {task.origin} · {task.mode}
              </small>
            </div>
            <h3>{actionName(action)}</h3>
            <p>{task.intent}</p>
            <div className="task-context">
              <span>{plan?.name ?? "直播负责人交接"}</span>
              <strong>{target}</strong>
            </div>
            <small>
              申请 {task.id.slice(0, 12)} · 风控与平台执行尚待服务端
            </small>
            {task.status === "AWAITING_APPROVAL" && (
              <div className="row task-actions">
                <button
                  disabled={busy !== null}
                  className="button secondary small"
                  onClick={() => void decide(task.id, task.version, "REJECT")}
                >
                  <X size={14} />
                  模拟驳回
                </button>
                <button
                  disabled={busy !== null}
                  className="button primary small"
                  onClick={() => void decide(task.id, task.version, "APPROVE")}
                >
                  <Check size={14} />
                  {service?.mode === "mock" ? "模拟批准" : "批准申请"}
                </button>
              </div>
            )}
          </article>
        );
      })}
      {message && (
        <p className="feedback" role="alert">
          {message}
        </p>
      )}
    </div>
  );
}
