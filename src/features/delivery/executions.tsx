"use client";
import { useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  ClockCounterClockwise,
  MagnifyingGlass,
  ShieldCheck,
} from "@phosphor-icons/react";
import { useWorkspace } from "@/components/provider";
import {
  actionAvailability,
  executionStates,
  platforms,
  redact,
  time,
  type ExecutionTask,
} from "@/lib/delivery";
import { useDelivery } from "./context";
import { Gate, Json, Metrics, Missing, Modal, Section, Status } from "./ui";
import { AnalyzeButton } from "./shared";
import { ApprovalReview } from "./approval";

export function Executions() {
  const d = useDelivery();
  const [filter, setFilter] = useState({
    search: "",
    state: "all",
    source: "all",
    action: "all",
    page: 1,
  });
  const [selectedId, setSelectedId] = useState<string | null>(null),
    [operation, setOperation] = useState<{
      task: ExecutionTask;
      action: "verify" | "retry" | "approve" | "reject" | "confirm" | "cancel";
    } | null>(null);
  const planId = d.query.get("planId"),
    taskId = d.query.get("taskId");
  const tasks = d.data.executions
    .filter(
      (t) =>
        (!planId || t.planId === planId) &&
        (filter.state === "all" || t.status === filter.state) &&
        (filter.source === "all" || t.initiator === filter.source) &&
        (filter.action === "all" || t.action === filter.action) &&
        `${t.id} ${t.action} ${t.planId}`
          .toLowerCase()
          .includes(filter.search.toLowerCase()),
    )
    .sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt));
  const selected =
    tasks.find((t) => t.id === (selectedId ?? taskId)) ??
    (!taskId ? tasks[0] : undefined);
  const patch = (value: Partial<typeof filter>) =>
    setFilter({ ...filter, page: 1, ...value });
  const pages = Math.max(1, Math.ceil(tasks.length / 10)),
    page = Math.min(pages, filter.page),
    rows = tasks.slice((page - 1) * 10, page * 10);
  const today = (t: ExecutionTask) =>
    new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(
      new Date(t.createdAt),
    ) === d.scope.end;
  return (
    <>
      <Metrics
        items={[
          {
            name: "当日执行申请",
            value: tasks.filter(today).length,
            note: `${d.scope.end} · 按发起时间`,
          },
          {
            name: "待执行",
            value: tasks.filter((t) => t.status === "PENDING").length,
            note: "已获批且等待执行",
          },
          {
            name: "已核验生效",
            value: tasks.filter((t) => t.status === "EFFECTIVE").length,
            note: "具备回读匹配证明",
          },
          {
            name: "失败或异常",
            value: tasks.filter((t) =>
              ["FAILED", "UNKNOWN", "PARTIAL", "MANUAL"].includes(t.status),
            ).length,
            note: "结果未知需核查，勿重复提交",
          },
        ]}
      />
      <div className="dw-page-tools">
        <p className="dw-muted">
          <ShieldCheck size={15} />
          每次变更保留版本、审批和平台回执。
        </p>
        <AnalyzeButton
          kind="EXECUTION"
          ids={selected ? [selected.id] : []}
          label="AI分析执行异常"
          accountId={selected?.accountId}
        />
      </div>
      <div className="dw-execution-layout">
        <Section title="执行任务流水" detail="人工、AI和规则来源分别记录">
          <div className="dw-filters">
            <label className="dw-search">
              <MagnifyingGlass size={15} />
              <input
                aria-label="搜索执行任务"
                placeholder="任务ID、计划ID或操作"
                value={filter.search}
                onChange={(e) => patch({ search: e.target.value })}
              />
            </label>
            <select
              aria-label="执行状态"
              value={filter.state}
              onChange={(e) => patch({ state: e.target.value })}
            >
              <option value="all">全部状态</option>
              {Object.entries(executionStates).map(([v, n]) => (
                <option key={v} value={v}>
                  {n}
                </option>
              ))}
            </select>
            <select
              aria-label="发起主体"
              value={filter.source}
              onChange={(e) => patch({ source: e.target.value })}
            >
              <option value="all">全部主体</option>
              <option value="MANUAL">人工</option>
              <option value="AI">AI</option>
              <option value="RULE">系统规则</option>
            </select>
            <select
              aria-label="操作类型"
              value={filter.action}
              onChange={(e) => patch({ action: e.target.value })}
            >
              <option value="all">全部操作</option>
              {[...new Set(d.data.executions.map((t) => t.action))].map(
                (action) => (
                  <option key={action}>{action}</option>
                ),
              )}
            </select>
          </div>
          <div className="dw-task-list">
            {rows.map((t) => (
              <button
                key={t.id}
                className={`dw-task-item ${selected?.id === t.id ? "selected" : ""}`}
                onClick={() => setSelectedId(t.id)}
              >
                <span className={`dw-task-dot ${t.status.toLowerCase()}`} />
                <div>
                  <div>
                    <b>{t.action}</b>
                    <Status
                      tone={
                        t.status === "EFFECTIVE"
                          ? "green"
                          : ["FAILED", "UNKNOWN"].includes(t.status)
                            ? "red"
                            : "neutral"
                      }
                    >
                      {executionStates[t.status]}
                    </Status>
                  </div>
                  <small>
                    {t.id} · {platforms[t.platform].shortName}
                  </small>
                  <p>
                    {d.data.plans.find((p) => p.id === t.planId)?.name ??
                      t.planId}
                  </p>
                  <footer>
                    <span>
                      {t.initiator} ·{" "}
                      {d.data.accounts.find((a) => a.id === t.accountId)
                        ?.name ?? t.accountId}
                    </span>
                    <time>{time(t.updatedAt)}</time>
                  </footer>
                </div>
              </button>
            ))}
          </div>
          {!tasks.length && (
            <Missing
              title="暂无当前范围的执行记录"
              detail="API请求发出或审批通过均不会被标为成功。平台回读核验、失败和结果未知分别记录。"
            />
          )}
          <div className="dw-pagination">
            <span>
              {tasks.length} 条 · {page}/{pages} 页
            </span>
            <button
              disabled={page <= 1}
              onClick={() => patch({ page: page - 1 })}
            >
              上一页
            </button>
            <button
              disabled={page >= pages}
              onClick={() => patch({ page: page + 1 })}
            >
              下一页
            </button>
          </div>
        </Section>
        <Section
          title="执行详情与核验时间线"
          detail={
            selected
              ? `任务 ${selected.id} · v${selected.version}`
              : "选择任务查看参数和平台响应"
          }
        >
          {selected ? (
            <TaskDetail
              task={selected}
              operate={(action) => setOperation({ task: selected, action })}
            />
          ) : (
            <Missing
              title={taskId ? "此范围内未找到指定任务" : "暂无任务详情"}
            />
          )}
        </Section>
      </div>
      {operation && (
        <TaskOperation
          task={operation.task}
          action={operation.action}
          close={() => setOperation(null)}
        />
      )}
    </>
  );
}
function TaskDetail({
  task: t,
  operate,
}: {
  task: ExecutionTask;
  operate: (
    action: "verify" | "retry" | "approve" | "reject" | "confirm" | "cancel",
  ) => void;
}) {
  const d = useDelivery();
  const { data } = useWorkspace();
  const legacy =
    data.status === "ready"
      ? data.data.tasks.find((j) => j.job_id === t.legacyJobId)
      : null;
  return (
    <>
      <div className="dw-subheading">
        <h3>{t.action}</h3>
        <Status>{executionStates[t.status]}</Status>
      </div>
      <dl className="dw-definition">
        <dt>平台 / 账户</dt>
        <dd>
          {platforms[t.platform].name} / {t.accountId}
        </dd>
        <dt>关联投放对象</dt>
        <dd>
          <Link href={d.href("plans", { planId: t.planId })}>{t.planId}</Link>
        </dd>
        <dt>发起人 / 审批人</dt>
        <dd>
          {t.actorId ?? "现有记录未提供"} / {t.approverId ?? "尚无"}
        </dd>
        <dt>发起时间</dt>
        <dd>{time(t.createdAt)}</dd>
        <dt>请求状态</dt>
        <dd>{t.requestStatus}</dd>
        <dt>平台原始回执ID</dt>
        <dd>{t.nativeId ?? "未提供"}</dd>
        <dt>回读核验</dt>
        <dd>{t.verification}</dd>
        <dt>审批关联</dt>
        <dd>{t.approvalId ?? "未发起"}</dd>
      </dl>
      <div className="dw-param-comparison">
        <div>
          <h3>调整前参数</h3>
          <Json value={t.before} />
        </div>
        <div>
          <h3>调整后参数</h3>
          <Json value={t.after} />
        </div>
      </div>
      <details>
        <summary>平台响应（敏感字段已脱敏）</summary>
        <Json value={t.response} />
      </details>
      <div className="dw-timeline">
        {t.history.map((h, i) => (
          <article key={`${h.at}-${i}`}>
            <ClockCounterClockwise size={15} />
            <div>
              <b>{h.status}</b>
              <p>{String(redact(h.detail))}</p>
              <time>{time(h.at)}</time>
            </div>
          </article>
        ))}
      </div>
      {t.failure && (
        <p className="dw-notice danger">
          失败原因：{String(redact(t.failure))}
        </p>
      )}
      {["UNKNOWN", "MANUAL", "PARTIAL"].includes(t.status) && (
        <p className="dw-notice">
          请核对账户授权、平台响应和当前配置；超时及未知结果不能自动重放。
        </p>
      )}
      <div className="dw-actions">
        {legacy?.status === "DRAFT" && (
          <button
            className="button secondary"
            onClick={() => operate("confirm")}
          >
            确认风险并送审
          </button>
        )}
        {t.status === "PENDING_APPROVAL" &&
          (!t.legacyJobId || legacy?.status === "PENDING_APPROVAL") && (
            <>
              <button
                className="button primary"
                onClick={() => operate("approve")}
              >
                审批通过
              </button>
              <button
                className="button secondary"
                onClick={() => operate("reject")}
              >
                驳回
              </button>
            </>
          )}
        {["VERIFYING", "UNKNOWN", "PARTIAL", "MANUAL"].includes(t.status) && (
          <button
            className="button secondary"
            onClick={() => operate("verify")}
          >
            请求回读核验
          </button>
        )}
        {t.safeRetry && t.status === "FAILED" && (
          <button className="button secondary" onClick={() => operate("retry")}>
            安全重试预览
          </button>
        )}
        {legacy &&
          ["DRAFT", "PENDING_APPROVAL", "APPROVED"].includes(legacy.status) && (
            <button
              className="button secondary"
              onClick={() => operate("cancel")}
            >
              取消申请
            </button>
          )}
        <Link className="dw-link" href={d.href("plans", { planId: t.planId })}>
          返回计划查看实际效果 <ArrowRight size={14} />
        </Link>
      </div>
      <div className="dw-related">
        <h3>复盘关联</h3>
        <p>核验生效仅代表配置生效，实际效果需观察后续统计周期。</p>
        <Link
          href={`/app/analytics?${new URLSearchParams({ shopId: t.shopId, accountId: t.accountId, platform: t.platform, planId: t.planId, taskId: t.id, start: d.scope.start, end: d.scope.end })}`}
        >
          进入现有经营分析
        </Link>
        {t.decisionId && (
          <Link href={d.href("decisions", { decisionId: t.decisionId })}>
            查看来源决策
          </Link>
        )}
        {t.budgetId && (
          <Link
            href={d.href("budgets", { planId: t.planId, budgetId: t.budgetId })}
          >
            查看关联预算
          </Link>
        )}
      </div>
    </>
  );
}
function TaskOperation({
  task: t,
  action,
  close,
}: {
  task: ExecutionTask;
  action: "verify" | "retry" | "approve" | "reject" | "confirm" | "cancel";
  close: () => void;
}) {
  const d = useDelivery(),
    { service } = useWorkspace();
  if (!t.legacyJobId && (action === "approve" || action === "reject"))
    return (
      <ApprovalReview
        approvalId={t.approvalId}
        accountId={t.accountId}
        executionId={t.id}
        initial={action === "approve" ? "APPROVE" : "REJECT"}
        close={close}
      />
    );
  const capability = actionAvailability(
    d.data,
    d.identity,
    t.accountId,
    action === "retry" ? "retry" : action === "verify" ? "verify" : "review",
  );
  const permission =
    action === "approve" || action === "reject"
      ? "task.approve"
      : action === "confirm" || action === "cancel"
        ? "task.create"
        : capability.permission;
  const allowed =
    capability.status === "AUTHORIZED" &&
    d.identity.permissions.includes(permission) &&
    (action !== "retry" ||
      (t.safeRetry && t.status === "FAILED" && !!t.idempotencyKey));
  const names = {
    verify: "请求执行结果核验",
    retry: "安全重试",
    approve: "批准操作申请",
    reject: "驳回操作申请",
    confirm: "确认风险并送审",
    cancel: "取消操作申请",
  };
  async function submit() {
    if (!allowed || !service) return;
    const gate =
      action === "retry" ? "retry" : action === "verify" ? "verify" : "review";
    await d.mutate(
      t.accountId,
      gate,
      async () => {
        if (action === "verify") return d.api.verify(t.id, t.version);
        if (action === "retry")
          return d.api.retry(t.id, t.version, t.idempotencyKey!);
        const input = { job_id: t.legacyJobId!, expected_version: t.version };
        if (!t.legacyJobId) throw new Error("此任务审批接口尚未接入");
        if (action === "confirm") return service.confirmJob(input);
        if (action === "cancel") return service.cancelJob(input);
        return service.reviewJob({
          ...input,
          decision: action === "approve" ? "APPROVE" : "REJECT",
        });
      },
      close,
    );
  }
  return (
    <Modal title={names[action]} close={close}>
      <p>
        任务 {t.id} · 版本 v{t.version}
      </p>
      <div className="dw-param-comparison">
        <Json value={t.before} />
        <Json value={t.after} />
      </div>
      <p className="dw-notice">
        {action === "retry"
          ? "仅当后端明确返回安全重试资格且结果为失败时，使用原幂等键请求后端重新评估。不会直接重放平台请求。"
          : action === "approve"
            ? "审批通过后等待后端执行；平台受理与结果回读完成前不会显示已生效。"
            : action === "verify"
              ? "只请求后端查询原任务的实际结果，不重复提交投放修改。"
              : "后端校验任务当前状态、权限和版本冲突。"}
      </p>
      <Gate capability={capability} />
      {!d.identity.permissions.includes(permission) && (
        <p>缺少权限 {permission}</p>
      )}
      <button
        className="button primary"
        disabled={!allowed || d.busy || (d.uncertain && action !== "verify")}
        onClick={() => void submit()}
      >
        确认{names[action]}
      </button>
    </Modal>
  );
}
