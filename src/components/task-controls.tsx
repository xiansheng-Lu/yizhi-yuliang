"use client";
import { useRef, useState, type FormEvent } from "react";
import {
  actionNames,
  commandActions,
  displayTime,
  formatJobValue,
  isHandoff,
  jobDelta,
  jobStatuses,
  jobStatusNames,
  parseMoneyInput,
  type CommandAction,
  type CommandJob,
  type JobSource,
  type JobValue,
  type MockScenario,
} from "@/lib/command-job";
import { type FrontendSnapshot } from "@/lib/domain";
import { scopedJobs, scopedPlans } from "@/lib/reporting";
import { useWorkspace } from "./provider";
import { Badge, EmptyState } from "./ui";
const sourceNames: Record<JobSource, string> = {
  AUTOPILOT: "自主投放",
  PC_MANUAL: "PC 人工指令",
  MOBILE_COMMAND: "手机指令",
};
export function MockScenarioSelect({
  value,
  onChange,
}: {
  value: MockScenario;
  onChange: (v: MockScenario) => void;
}) {
  const { service } = useWorkspace();
  return service?.mode === "mock" ? (
    <label>
      Mock 演示分支
      <select
        aria-label="Mock 演示分支"
        value={value}
        onChange={(e) => onChange(e.target.value as MockScenario)}
      >
        <option value="SUCCESS">受理与核验匹配</option>
        <option value="REQUEST_FAILURE">模拟请求失败</option>
        <option value="TIMEOUT">模拟请求超时（结果未知）</option>
        <option value="VERIFY_FAILURE">模拟核验不匹配</option>
      </select>
    </label>
  ) : null;
}
export function JobDetails({
  job,
  snapshot,
  controls = true,
  review = true,
}: {
  job: CommandJob;
  snapshot: FrontendSnapshot;
  controls?: boolean;
  review?: boolean;
}) {
  const { service, reload, identity } = useWorkspace();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const plan = snapshot.plans.find((p) => p.id === job.target_id);
  const account = snapshot.adAccounts.find((a) => a.id === job.account_id);
  const room = snapshot.liveSessions.find((l) => l.id === job.target_id);
  const canApprove = review && identity?.permissions.includes("task.approve");
  const canExecute = review && identity?.permissions.includes("task.execute");
  const canCreate = identity?.permissions.includes("task.create");
  async function act(
    action: "confirm" | "approve" | "reject" | "advance" | "cancel" | "expire",
  ) {
    if (!service || busy) return;
    setBusy(true);
    setError("");
    const input = { job_id: job.job_id, expected_version: job.version };
    try {
      if (action === "confirm") await service.confirmJob(input);
      if (action === "approve" || action === "reject")
        await service.reviewJob({
          ...input,
          decision: action === "approve" ? "APPROVE" : "REJECT",
        });
      if (action === "advance") await service.advanceMockJob(input);
      if (action === "cancel") await service.cancelJob(input);
      if (action === "expire") await service.expireMockJob(input);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作未完成");
      await reload();
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="job-details">
      <div className="row between">
        <Badge
          tone={
            job.status === "SUCCEEDED"
              ? "green"
              : job.status === "PENDING_APPROVAL" || job.status === "BLOCKED"
                ? "warning"
                : "blue"
          }
        >
          {job.mode === "MOCK" ? "Mock · " : ""}
          {job.status === "EXECUTING" ? "执行中" : jobStatusNames[job.status]}
        </Badge>
        <small>{sourceNames[job.source]}</small>
      </div>
      <h3>{actionNames[job.action_type]}</h3>
      <p>{job.intent}</p>
      <strong>{plan?.name ?? room?.name ?? job.target_id}</strong>
      <dl className="job-facts">
        <div>
          <dt>店铺</dt>
          <dd>
            {snapshot.shops.find((s) => s.id === job.shop_id)?.name ??
              job.shop_id}
          </dd>
        </div>
        <div>
          <dt>目标账户</dt>
          <dd>
            {account
              ? `${account.name} · ${account.id}`
              : "无广告账户 · 仅人工交接"}
          </dd>
        </div>
        <div>
          <dt>目标ID</dt>
          <dd>
            {job.target_id}
            {plan ? ` / ${plan.nativePlanId}` : ""}
          </dd>
        </div>
      </dl>
      <div className="change-diff" aria-label="变更差异">
        <div>
          <span>旧值</span>
          <b>{formatJobValue(job.old_value)}</b>
        </div>
        <div>
          <span>新值</span>
          <b>{formatJobValue(job.new_value)}</b>
        </div>
        <div>
          <span>差额</span>
          <b>{jobDelta(job)}</b>
        </div>
      </div>
      <div className="risk-details">
        <b>
          {job.risk_summary.source === "MOCK" ? "Mock 风控预检" : "服务端风控"}{" "}
          · {job.risk_summary.result} · {job.risk_summary.level}
        </b>
        <ul>
          {job.risk_summary.checks.map((c, i) => (
            <li key={`${c.code}-${i}`}>
              {c.passed ? "通过" : "注意"}：{c.message}
            </li>
          ))}
        </ul>
        <small>
          规则版本 {job.risk_summary.policy_version} ·{" "}
          {displayTime(job.risk_summary.checked_at)}
        </small>
      </div>
      <dl className="job-facts">
        <div>
          <dt>任务ID</dt>
          <dd>{job.job_id}</dd>
        </div>
        <div>
          <dt>任务状态</dt>
          <dd>
            {job.status} · v{job.version}
          </dd>
        </div>
        <div>
          <dt>审批</dt>
          <dd>
            {job.approval.state}
            {job.approval.actor_id ? ` · ${job.approval.actor_id}` : ""}
          </dd>
        </div>
        <div>
          <dt>创建 / 有效至</dt>
          <dd>
            {displayTime(job.created_at)} / {displayTime(job.expires_at)}
          </dd>
        </div>
      </dl>
      {job.mode === "MOCK" && (
        <p className="notice">
          Mock
          演示任务。确认与批准不会调用广告平台；模拟核验匹配后才更新演示计划。正式风控、授权及执行以陆文龙提供的后端为准。
        </p>
      )}
      {job.execution_receipt && (
        <div className="receipt" aria-label="执行与核验回执">
          <b>
            {job.execution_receipt.source === "MOCK"
              ? "模拟回执 · 非真实平台成功"
              : "平台执行回执"}
          </b>
          <p>
            受理：
            {job.execution_receipt.accepted === null
              ? "未知 / 尚未回执"
              : job.execution_receipt.accepted
                ? "已受理"
                : "未受理"}{" "}
            · 核验：{job.execution_receipt.verification}
          </p>
          <p>回读值：{formatJobValue(job.execution_receipt.observed_value)}</p>
          <small>
            平台回执ID：
            {job.execution_receipt.platform_receipt_id ??
              "无（Mock 未调用平台）"}
            {job.execution_receipt.verified_at
              ? ` · 核验时间 ${displayTime(job.execution_receipt.verified_at)}`
              : ""}
          </small>
        </div>
      )}
      {job.error && (
        <p role="alert" className="job-error">
          {job.error.code}：{job.error.message}
        </p>
      )}
      <ol className="job-history" aria-label="任务状态流水">
        {job.history.map((h, i) => (
          <li key={i}>
            <b>{h.status}</b>
            <small>{displayTime(h.at)}</small>
            <span>{h.detail}</span>
          </li>
        ))}
      </ol>
      {controls && (
        <div className="row task-actions">
          {canCreate && job.status === "DRAFT" && (
            <button
              className="button primary small"
              disabled={busy}
              onClick={() => void act("confirm")}
            >
              {isHandoff(job.action_type)
                ? "确认并创建人工交接"
                : "确认对象与风险，提交审批"}
            </button>
          )}
          {canApprove && job.status === "PENDING_APPROVAL" && (
            <>
              <button
                className="button secondary small"
                disabled={busy}
                onClick={() => void act("reject")}
              >
                驳回任务
              </button>
              <button
                className="button primary small"
                disabled={busy}
                onClick={() => void act("approve")}
              >
                {job.mode === "MOCK" ? "Mock 批准" : "批准任务"}
              </button>
            </>
          )}
          {canExecute &&
            service?.mode === "mock" &&
            ["APPROVED", "EXECUTING", "VERIFYING"].includes(job.status) && (
              <button
                className="button primary small"
                disabled={busy}
                onClick={() => void act("advance")}
              >
                {job.status === "APPROVED"
                  ? "开始模拟执行"
                  : job.status === "EXECUTING"
                    ? "接收模拟执行回执"
                    : "运行模拟核验"}
              </button>
            )}
          {canCreate &&
            ["DRAFT", "PENDING_APPROVAL", "APPROVED"].includes(job.status) && (
              <button
                className="button secondary small"
                disabled={busy}
                onClick={() => void act("cancel")}
              >
                取消任务
              </button>
            )}
          {review &&
            service?.mode === "mock" &&
            canCreate &&
            ["DRAFT", "PENDING_APPROVAL", "APPROVED"].includes(job.status) && (
              <button
                className="text-link"
                disabled={busy}
                onClick={() => void act("expire")}
              >
                模拟过期
              </button>
            )}
        </div>
      )}
      {error && (
        <p role="alert" className="feedback">
          {error}
        </p>
      )}
    </div>
  );
}
export function TaskList({
  snapshot,
  limit,
  review = true,
}: {
  snapshot: FrontendSnapshot;
  limit?: number;
  review?: boolean;
}) {
  const [status, setStatus] = useState("ALL"),
    [source, setSource] = useState("ALL");
  const tasks = scopedJobs(snapshot).filter(
    (j) =>
      (status === "ALL" || j.status === status) &&
      (source === "ALL" || j.source === source),
  );
  return (
    <div className="task-list">
      {!limit && (
        <div className="task-filters task-form">
          <label>
            任务状态
            <select
              aria-label="任务状态筛选"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="ALL">全部状态</option>
              {jobStatuses.map((s) => (
                <option key={s} value={s}>
                  {jobStatusNames[s]}
                </option>
              ))}
            </select>
          </label>
          <label>
            任务来源
            <select
              aria-label="任务来源筛选"
              value={source}
              onChange={(e) => setSource(e.target.value)}
            >
              <option value="ALL">全部来源</option>
              {Object.entries(sourceNames).map(([v, n]) => (
                <option key={v} value={v}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      {!tasks.length && (
        <EmptyState
          title="当前范围暂无任务"
          description="PC、手机与自主投放任务在同一列表；检查店铺、直播间及平台筛选。"
        />
      )}
      {(limit ? tasks.slice(0, limit) : tasks).map((job) => (
        <details className="task-item" key={job.job_id}>
          <summary>
            <span>
              <b>{actionNames[job.action_type]}</b>
              <small>{job.intent}</small>
            </span>
            <Badge
              tone={job.status === "PENDING_APPROVAL" ? "warning" : "blue"}
            >
              {job.mode === "MOCK" ? "Mock · " : ""}
              {jobStatusNames[job.status]}
            </Badge>
          </summary>
          <JobDetails job={job} snapshot={snapshot} review={review} />
        </details>
      ))}
    </div>
  );
}
export function TaskComposer({
  snapshot,
  origin,
}: {
  snapshot: FrontendSnapshot;
  origin: "PC" | "MOBILE";
}) {
  const { service, reload } = useWorkspace();
  const submission = useRef<{ signature: string; key: string } | null>(null);
  const [type, setType] = useState<CommandAction>("CHANGE_DAILY_BUDGET"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [created, setCreated] = useState<string | null>(null),
    [scenario, setScenario] = useState<MockScenario>("SUCCESS");
  const handoff = isHandoff(type),
    numeric = ["CHANGE_DAILY_BUDGET", "CHANGE_BID", "SET_ROI_TARGET"].includes(
      type,
    );
  const targets = handoff
    ? snapshot.liveSessions.filter(
        (x) =>
          snapshot.scope.shop_id === "all" ||
          x.shopId === snapshot.scope.shop_id,
      )
    : scopedPlans(snapshot);
  const job = scopedJobs(snapshot).find((j) => j.job_id === created);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!service || busy) return;
    const f = new FormData(e.currentTarget),
      target = targets.find((t) => t.id === f.get("target"));
    if (!target) {
      setError("请明确选择操作对象。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const value: JobValue =
        type === "CHANGE_DAILY_BUDGET" || type === "CHANGE_BID"
          ? {
              kind: "MONEY",
              currency: "CNY",
              amount_minor: parseMoneyInput(String(f.get("value"))),
            }
          : type === "SET_ROI_TARGET"
            ? { kind: "RATIO", value: Number(f.get("value")) }
            : handoff
              ? {
                  kind: "HANDOFF",
                  value: type === "REQUEST_LIVE_START" ? "START" : "END",
                }
              : { kind: "BOOLEAN", value: type === "RESUME_AD_PLAN" };
      const signature = JSON.stringify([
        origin,
        target.id,
        type,
        value,
        f.get("intent"),
        scenario,
      ]);
      if (submission.current?.signature !== signature)
        submission.current = { signature, key: crypto.randomUUID() };
      const result = await service.createJob(
        {
          source: origin === "PC" ? "PC_MANUAL" : "MOBILE_COMMAND",
          shop_id: target.shopId,
          target_type: handoff ? "LIVE_SESSION" : "AD_PLAN",
          target_id: target.id,
          action_type: type,
          new_value: value,
          intent: String(f.get("intent")),
          idempotency_key: submission.current.key,
        },
        scenario,
      );
      setCreated(result.job_id);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "任务未创建");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <form className="task-form" onSubmit={submit}>
        <label>
          运营意图
          <input
            name="intent"
            required
            maxLength={500}
            placeholder="说明本次操作的目的"
          />
        </label>
        <label>
          操作类型
          <select
            aria-label="操作类型"
            value={type}
            onChange={(e) => setType(e.target.value as CommandAction)}
          >
            {commandActions.map((a) => (
              <option key={a} value={a}>
                {actionNames[a]}
              </option>
            ))}
          </select>
        </label>
        <label>
          {handoff ? "直播场次" : "广告计划"}
          <select
            aria-label="操作对象"
            name="target"
            key={`${type}-${snapshot.scope.shop_id}-${snapshot.scope.live_session_id}-${snapshot.scope.connector_id}`}
            required
            defaultValue=""
          >
            <option value="" disabled>
              请选择具体对象
            </option>
            {targets.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        {numeric && (
          <label>
            {type === "SET_ROI_TARGET"
              ? "目标ROI（无单位）"
              : "目标金额（元，CNY）"}
            <input
              aria-label="目标值"
              key={type}
              name="value"
              type="number"
              min="0.01"
              step="0.01"
              required
              placeholder={type === "SET_ROI_TARGET" ? "5.00" : "5000.00"}
            />
          </label>
        )}
        <MockScenarioSelect value={scenario} onChange={setScenario} />
        <button className="button primary" disabled={busy || !targets.length}>
          {busy ? "正在创建…" : "创建任务并核对差异"}
        </button>
        {error && (
          <p role="alert" className="feedback">
            {error}
          </p>
        )}
      </form>
      {job && (
        <div className="created-job" aria-label="新建任务确认">
          <JobDetails job={job} snapshot={snapshot} review={origin === "PC"} />
        </div>
      )}
    </>
  );
}
