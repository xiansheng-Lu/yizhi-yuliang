"use client";
import Link from "next/link";
import { useState, type FormEvent, type ReactNode } from "react";
import { money, type FrontendSnapshot } from "@/lib/domain";
import { parseMoneyInput } from "@/lib/command-job";
import { scopedJobs } from "@/lib/reporting";
import { useWorkspace } from "@/components/provider";
import { Badge, Panel } from "@/components/ui";
import {
  CoreMetrics,
  LiveSummary,
  ScopeControls,
} from "@/components/operating-context";
import { CommandPanel } from "@/components/command-panel";
import { TaskComposer, TaskList } from "@/components/task-controls";

export function GoalsForm({ snapshot }: { snapshot: FrontendSnapshot }) {
  const policy = snapshot.riskPolicies[0];
  const { service, reload, identity } = useWorkspace();
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  if (!policy) return <p>暂无经营目标与风控配置</p>;
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!service || busy) return;
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setMessage("");
    try {
      await service.updatePolicy({
        ...policy,
        dailyBudgetMinor: parseMoneyInput(String(f.get("budget"))),
        targetRoi: Number(f.get("roi")),
        maxChangePercent: Number(f.get("change")),
        cooldownMinutes: Number(f.get("cooldown")),
        mode: f.get("mode") as typeof policy.mode,
      });
      setMessage(
        "经营目标已保存。已有申请需重新核对规则版本；工作模式不会授予审批权限。",
      );
      await reload();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "保存未完成");
    } finally {
      setBusy(false);
    }
  }
  return (
    <form key={policy.version} className="task-form" onSubmit={save}>
      <div className="form-grid">
        <label>
          企业日预算上限（元，CNY）
          <input
            name="budget"
            aria-label="企业日预算上限"
            type="number"
            step="0.01"
            min="0.01"
            required
            defaultValue={policy.dailyBudgetMinor / 100}
          />
        </label>
        <label>
          经营ROI目标（无单位）
          <input
            name="roi"
            type="number"
            step="0.01"
            min="0.01"
            required
            defaultValue={policy.targetRoi}
          />
        </label>
        <label>
          单次预算 / 出价变化上限（%）
          <input
            name="change"
            type="number"
            step="0.1"
            min="1"
            max="100"
            required
            defaultValue={policy.maxChangePercent}
          />
        </label>
        <label>
          同计划成功操作冷却期（分钟）
          <input
            name="cooldown"
            type="number"
            step="1"
            min="0"
            required
            defaultValue={policy.cooldownMinutes}
          />
        </label>
      </div>
      <label>
        工作模式
        <select aria-label="工作模式" name="mode" defaultValue={policy.mode}>
          <option value="MANUAL">人工指令</option>
          <option value="COPILOT">AI 辅助</option>
          <option value="AUTOPILOT">自主投放候选</option>
        </select>
      </label>
      <div className="notice">
        {snapshot.source === "MOCK"
          ? "演示风控预检。正式预算硬上限与白名单授权由后端执行。"
          : "后端风控规则。"}
        上限统计企业全部关联计划的配置预算，包含暂停计划；经营ROI目标不直接修改各计划ROI或出价。
      </div>
      <button
        className="button primary"
        disabled={busy || !identity?.permissions.includes("policy.edit")}
      >
        {busy ? "保存中…" : "保存经营目标"}
      </button>
      <small>
        {" "}
        当前版本 v{policy.version} · {money(policy.dailyBudgetMinor)}
      </small>
      {message && (
        <p className="feedback" role="status">
          {message}
        </p>
      )}
    </form>
  );
}
export function ControlWorkspace({
  pageId,
  snapshot,
  plans,
  liveEvidence,
}: {
  pageId: string;
  snapshot: FrontendSnapshot;
  plans: ReactNode;
  liveEvidence: ReactNode;
}) {
  const jobs = scopedJobs(snapshot);
  return (
    <>
      <ScopeControls snapshot={snapshot} />
      {pageId === "overview" && (
        <>
          <CoreMetrics snapshot={snapshot} />
          <div className="two-columns">
            <Panel
              title="AI运行状态"
              detail="候选、审批、执行、核验分别记录"
              action={<Badge tone="blue">{snapshot.source} · 观察中</Badge>}
            >
              <div className="workflow-path">
                {["标准任务", "对象与风控确认", "人工审批", "执行与核验"].map(
                  (s, i) => (
                    <div key={s}>
                      <span>0{i + 1}</span>
                      <b>{s}</b>
                      <small>
                        {i === 3 ? "回读匹配后完成" : "共享 command_job"}
                      </small>
                    </div>
                  ),
                )}
              </div>
              <p>
                当前工作模式：{snapshot.riskPolicies[0]?.mode}
                。自主投放产生候选任务，沿用相同审批机制。
              </p>
              <div className="todo-links">
                <Link className="button secondary" href="/app/tasks">
                  待办与审批（
                  {jobs.filter((x) => x.status === "PENDING_APPROVAL").length}）
                </Link>
                <Link className="text-link" href="/app/goals">
                  管理经营目标
                </Link>
                <Link className="text-link" href="/app/monitor">
                  进入直播作战室
                </Link>
              </div>
              <p className="notice">
                Mock 使用本浏览器同源存储演示共享状态；未连接真实 AI
                或广告平台。批准不等于执行成功。
              </p>
            </Panel>
            <Panel title="待关注的任务">
              <TaskList snapshot={snapshot} limit={2} />
            </Panel>
          </div>
          <Panel title="当前投放计划" detail="与上方三个KPI使用相同统计范围">
            {plans}
          </Panel>
        </>
      )}
      {pageId === "goals" && (
        <Panel
          title="经营目标与风控参数"
          detail="金额按 CNY 最小单位保存，规则版本参与审批校验"
        >
          <GoalsForm snapshot={snapshot} />
        </Panel>
      )}
      {pageId === "tasks" && (
        <div className="two-columns">
          <Panel
            title="AI任务与审批"
            detail="PC / 手机 / 自主投放使用同一 command_job"
          >
            <TaskList snapshot={snapshot} />
          </Panel>
          <Panel title="PC创建操作任务" detail="确认对象与风险后进入审批">
            <TaskComposer snapshot={snapshot} origin="PC" />
          </Panel>
        </div>
      )}
      {pageId === "monitor" && (
        <>
          <div className="two-columns">
            <Panel title="直播数据与异常观察">
              <LiveSummary snapshot={snapshot} />
            </Panel>
            <Panel title="AI操作入口">
              <CommandPanel snapshot={snapshot} source="PC_MANUAL" />
            </Panel>
          </div>
          <Panel title="直播关联投放情况">{plans}</Panel>
          {liveEvidence}
        </>
      )}
    </>
  );
}
