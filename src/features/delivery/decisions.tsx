"use client";
import { useState, type FormEvent } from "react";
import Link from "next/link";
import { ArrowRight, Gear, Robot, ShieldCheck } from "@phosphor-icons/react";
import {
  actionAvailability,
  decisionTypes,
  platforms,
  redact,
  time,
  type AIDecision,
} from "@/lib/delivery";
import type {
  DecisionReviewRequest,
  PolicyRequest,
} from "@/services/delivery-service";
import { useDelivery } from "./context";
import { Gate, Json, Metrics, Missing, Modal, Section, Status } from "./ui";
import { AnalyzeButton } from "./shared";
const statusNames = {
  OPEN: "待处理",
  DEFERRED: "已暂缓",
  PENDING_APPROVAL: "待审批",
  ACCEPTED: "已采纳",
  REJECTED: "已驳回",
  EXECUTED: "已执行",
};
const modeNames = {
  OBSERVE: "观察模式",
  APPROVAL: "审批模式",
  CONTROLLED: "受控自动模式",
};
export function Decisions() {
  const d = useDelivery();
  const [status, setStatus] = useState("all"),
    [type, setType] = useState("all"),
    [risk, setRisk] = useState("all");
  const [review, setReview] = useState<AIDecision | null>(null);
  const planId = d.query.get("planId"),
    decisionId = d.query.get("decisionId");
  const decisions = d.data.decisions.filter(
    (x) =>
      (!planId || x.planId === planId) &&
      (status === "all" || x.status === status) &&
      (type === "all" || x.type === type) &&
      (risk === "all" || x.risk === risk),
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected =
    decisions.find((x) => x.id === (selectedId ?? decisionId)) ??
    (!decisionId ? decisions[0] : undefined);
  const cap = d.data.capabilities.find(
    (x) => x.accountId === d.scope.accountId,
  );
  const mode = cap?.automationMode ?? "OBSERVE";
  const ids =
    planId && d.data.plans.some((p) => p.id === planId)
      ? [planId]
      : d.data.plans.map((p) => p.id);
  return (
    <>
      <Metrics
        items={[
          {
            name: "待处理决策",
            value: decisions.filter((x) => x.status === "OPEN").length,
            note: "具备数据依据的诊断队列",
          },
          {
            name: "高风险预警",
            value: decisions.filter(
              (x) => x.risk === "HIGH" && x.status === "OPEN",
            ).length,
            note: "需要授权人员判断",
          },
          {
            name: "已处理建议",
            value: decisions.filter((x) =>
              ["ACCEPTED", "REJECTED", "EXECUTED"].includes(x.status),
            ).length,
            note: "采纳和执行分别追踪",
          },
          {
            name: "待审批任务",
            value: decisions.filter((x) => x.status === "PENDING_APPROVAL")
              .length,
            note: "审批不等于平台结果生效",
          },
        ]}
      />
      <div className="dw-page-tools">
        <div className="dw-mode">
          <ShieldCheck size={16} />
          <b>{modeNames[mode]}</b>
          <span>
            {cap ? "由账户后端策略声明" : "默认只观察，策略接口待接入"}
          </span>
        </div>
        <div className="dw-actions">
          <AnalyzeButton
            kind={planId ? "PLAN" : "DECISION"}
            ids={ids}
            label={planId ? "创建此计划分析任务" : "创建分析任务"}
            accountId={
              planId
                ? d.data.plans.find((p) => p.id === planId)?.accountId
                : undefined
            }
          />
        </div>
      </div>
      {planId && (
        <p className="dw-notice">
          已限定投放对象{" "}
          {d.data.plans.find((p) => p.id === planId)?.name ?? planId} ·{" "}
          <Link href={d.href("plans", { planId })}>返回投放详情</Link>
        </p>
      )}
      <div className="dw-filters">
        <select
          aria-label="决策处理状态"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="all">全部状态</option>
          {Object.entries(statusNames).map(([v, n]) => (
            <option key={v} value={v}>
              {n}
            </option>
          ))}
        </select>
        <select
          aria-label="策略类型"
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          <option value="all">全部策略类型</option>
          {Object.entries(decisionTypes).map(([v, n]) => (
            <option key={v} value={v}>
              {n}
            </option>
          ))}
        </select>
        <select
          aria-label="风险等级"
          value={risk}
          onChange={(e) => setRisk(e.target.value)}
        >
          <option value="all">全部风险</option>
          <option value="HIGH">高风险</option>
          <option value="MEDIUM">中风险</option>
          <option value="LOW">低风险</option>
        </select>
      </div>
      <div className="dw-decision-layout">
        <Section
          title="AI决策队列"
          detail="先检查样本、观察周期和同步延迟，再判断是否调整"
        >
          {decisions.map((x) => (
            <button
              key={x.id}
              className={`dw-decision-card ${x.id === selected?.id ? "selected" : ""}`}
              onClick={() => setSelectedId(x.id)}
            >
              <div>
                <Status tone={x.risk === "HIGH" ? "red" : "blue"}>
                  {x.risk === "HIGH"
                    ? "高风险"
                    : x.risk === "MEDIUM"
                      ? "中风险"
                      : "低风险"}
                </Status>
                <span>{decisionTypes[x.type]}</span>
                <small>{time(x.discoveredAt)}</small>
              </div>
              <h3>{x.title}</h3>
              <p>{x.explanation}</p>
              <div className="dw-evidence-mini">
                {x.evidence.slice(0, 3).map((e) => (
                  <span key={`${e.label}:${e.sourceId}`}>
                    {e.label}
                    <b>{e.value}</b>
                  </span>
                ))}
              </div>
              <footer>
                <span>
                  {platforms[x.platform].shortName} ·{" "}
                  {d.data.accounts.find((a) => a.id === x.accountId)?.name ??
                    x.accountId}
                </span>
                <Status>{statusNames[x.status]}</Status>
              </footer>
            </button>
          ))}
          {!decisions.length && (
            <Missing
              title="暂无真实AI决策"
              detail="现有人工 command_job 不会被改称为AI诊断。分析接口及足够的周期样本接入后，在此展示证据与建议。"
            />
          )}
          <div className="dw-strategy-catalog">
            <h3>分析类型与平台能力</h3>
            {Object.entries(decisionTypes).map(([id, name]) => (
              <div key={id}>
                <Robot size={13} />
                <span>{name}</span>
                <small>
                  {d.scope.platform === "beans" && ["ROI", "CPA"].includes(id)
                    ? "以账户实际归因指标为准"
                    : "由后端诊断规则提供"}
                </small>
              </div>
            ))}
          </div>
        </Section>
        <Section
          title="决策证据与处置"
          detail={
            selected
              ? `决策 ${selected.id} · v${selected.version}`
              : "选择一条决策查看完整依据"
          }
        >
          {selected ? (
            <>
              <div className="dw-subheading">
                <h3>{selected.title}</h3>
                <Status>{statusNames[selected.status]}</Status>
              </div>
              <p>{selected.explanation}</p>
              <dl className="dw-definition">
                <dt>关联计划 / 订单</dt>
                <dd>
                  <Link href={d.href("plans", { planId: selected.planId })}>
                    {d.data.plans.find((p) => p.id === selected.planId)?.name ??
                      selected.planId}
                  </Link>
                </dd>
                <dt>分析周期</dt>
                <dd>
                  {time(selected.observation.start)} 至{" "}
                  {time(selected.observation.end)}
                </dd>
                <dt>样本量 / 数据延迟</dt>
                <dd>
                  {selected.observation.sampleSize ?? "未提供"} /{" "}
                  {selected.observation.delayMinutes === null
                    ? "未提供"
                    : `${selected.observation.delayMinutes} 分钟`}
                </dd>
                <dt>可信度</dt>
                <dd>{selected.observation.confidence}</dd>
              </dl>
              <h3>趋势与指标证据</h3>
              {selected.evidence.map((e) => (
                <div className="dw-list-row" key={`${e.sourceId}:${e.label}`}>
                  <span>
                    {e.label}
                    <small>证据ID {e.sourceId}</small>
                  </span>
                  <b>{e.value}</b>
                </div>
              ))}
              <h3>推荐动作</h3>
              <p>{selected.recommendation}</p>
              <p className="dw-notice">
                预计影响：{selected.impact ?? "后端未提供，不推算优化收益"}
              </p>
              {selected.observation.limitations.map((limit, i) => (
                <p key={i} className="dw-notice">
                  限制：{limit}
                </p>
              ))}
              <div className="dw-actions">
                <button
                  className="button primary"
                  disabled={!["OPEN", "DEFERRED"].includes(selected.status)}
                  onClick={() => setReview(selected)}
                >
                  处理建议
                </button>
                {selected.proposedAction === "CHANGE_DAILY_BUDGET" && (
                  <Link
                    className="button secondary"
                    href={d.href("budgets", {
                      section: "rules",
                      decisionId: selected.id,
                      planId: selected.planId,
                    })}
                  >
                    检查预算安全边界
                  </Link>
                )}
              </div>
              <div className="dw-related">
                <h3>采纳、执行与效果复盘</h3>
                <p>审批ID {selected.approvalId ?? "尚未发起"}</p>
                {selected.executionId && (
                  <Link
                    className="dw-link"
                    href={d.href("executions", {
                      taskId: selected.executionId,
                    })}
                  >
                    追踪执行与核验 <ArrowRight size={14} />
                  </Link>
                )}
                <p>{selected.outcome ?? "尚无执行后的实际效果记录"}</p>
                <small>执行后的自然波动不直接归因为AI优化贡献。</small>
                <details>
                  <summary>策略处置历史</summary>
                  {selected.history?.length ? (
                    selected.history.map((h, i) => (
                      <article className="dw-audit-line" key={`${h.at}-${i}`}>
                        <span>{time(h.at)}</span>
                        <div>
                          <b>
                            {h.actorId} · {h.status}
                          </b>
                          <p>{String(redact(h.comment))}</p>
                        </div>
                      </article>
                    ))
                  ) : (
                    <p>尚无后端返回的处置历史。</p>
                  )}
                </details>
              </div>
            </>
          ) : (
            <Missing
              title={decisionId ? "此范围内未找到指定决策" : "暂无决策证据"}
            />
          )}
        </Section>
      </div>
      {review && <Review decision={review} close={() => setReview(null)} />}
    </>
  );
}
export function DecisionPolicyPanel() {
  const d = useDelivery();
  const [rules, setRules] = useState(false);
  const cap = d.data.capabilities.find(c => c.accountId === d.scope.accountId);
  return <Section title="账户策略与自动化规则" detail="账户策略由后端确认，配置变更沿用原有预览和审批流程。">
    <div className="dw-page-tools">
      <div className="dw-mode"><ShieldCheck size={16}/><b>{modeNames[cap?.automationMode ?? "OBSERVE"]}</b><span>{cap ? "由账户后端策略声明" : "默认只观察，策略接口待接入"}</span></div>
      <button className="button secondary" onClick={() => setRules(true)}><Gear size={15}/>配置策略规则</button>
    </div>
    <p className="dw-muted">核对预算上限、调整频率、观察周期和审批阈值。自动化模式的变更需要账户能力与成员权限校验。</p>
    {rules && <Rules close={() => setRules(false)}/>}
  </Section>;
}
function Review({
  decision: x,
  close,
}: {
  decision: AIDecision;
  close: () => void;
}) {
  const d = useDelivery();
  const saved = d.draft as {
    kind?: string;
    decisionId?: string;
    choice?: DecisionReviewRequest["decision"];
    comment?: string;
    value?: string;
  } | null;
  const restore = saved?.kind === "REVIEW" && saved.decisionId === x.id;
  const [choice, setChoice] = useState<DecisionReviewRequest["decision"]>(
      restore ? saved!.choice! : "DEFER",
    ),
    [comment, setComment] = useState(restore ? (saved!.comment ?? "") : ""),
    [value, setValue] = useState(
      restore ? (saved!.value ?? "") : String(x.proposedValue ?? ""),
    ),
    [preview, setPreview] = useState(false);
  const rule = actionAvailability(d.data, d.identity, x.accountId, "review");
  const approved = choice === "APPROVE";
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!comment.trim()) return;
    if (!preview) {
      setPreview(true);
      return;
    }
    await d.mutate(
      x.accountId,
      "review",
      () =>
        d.api.review(x.id, {
          decision: choice,
          comment,
          expectedVersion: x.version,
          ...(choice === "MODIFY"
            ? {
                proposedValue:
                  typeof x.proposedValue === "boolean"
                    ? value === "true"
                    : Number(value),
              }
            : {}),
          idempotencyKey: crypto.randomUUID(),
        }),
      close,
    );
  }
  return (
    <Modal title="策略处置与审批" close={() => d.guard(close)}>
      <form className="dw-form" onSubmit={submit}>
        <p>
          {x.title} · {x.id} · v{x.version}
        </p>
        <label>
          处理方式
          <select
            value={choice}
            onChange={(e) => {
              setChoice(e.target.value as DecisionReviewRequest["decision"]);
              setPreview(false);
              d.markDirty(true);
              d.saveDraft({
                kind: "REVIEW",
                decisionId: x.id,
                choice: e.target.value,
                comment,
                value,
              });
            }}
          >
            <option value="DEFER">暂缓，继续观察</option>
            <option value="REJECT">驳回建议</option>
            <option value="MODIFY">修改建议后重新评估</option>
            <option value="APPROVE">同意并提交后端审批链</option>
          </select>
        </label>
        {choice === "MODIFY" && (
          <label>
            修订值（平台原生最小单位）
            {typeof x.proposedValue === "boolean" ? (
              <select
                aria-label="修订计划状态"
                value={value}
                onChange={(e) => {
                  setValue(e.target.value);
                  setPreview(false);
                  d.markDirty(true);
                  d.saveDraft({
                    kind: "REVIEW",
                    decisionId: x.id,
                    choice,
                    comment,
                    value: e.target.value,
                  });
                }}
              >
                <option value="true">开启</option>
                <option value="false">暂停</option>
              </select>
            ) : (
              <input
                type="number"
                min="1"
                step="1"
                required
                value={value}
                onChange={(e) => {
                  setValue(e.target.value);
                  setPreview(false);
                  d.markDirty(true);
                  d.saveDraft({
                    kind: "REVIEW",
                    decisionId: x.id,
                    choice,
                    comment,
                    value: e.target.value,
                  });
                }}
              />
            )}
          </label>
        )}
        <label>
          审批意见
          <textarea
            required
            value={comment}
            onChange={(e) => {
              setComment(e.target.value);
              setPreview(false);
              d.markDirty(true);
              d.saveDraft({
                kind: "REVIEW",
                decisionId: x.id,
                choice,
                comment: e.target.value,
                value,
              });
            }}
          />
        </label>
        {preview && (
          <>
            <Json
              value={{
                decisionId: x.id,
                expectedVersion: x.version,
                decision: choice,
                proposedValue: choice === "MODIFY" ? value : x.proposedValue,
                comment,
              }}
            />
            <p className="dw-notice">
              {approved
                ? "同意建议不会在前端执行投放。预算增加等动作继续由后端校验安全边界和审批权限。"
                : "处置结果由后端保存，建议历史及关联任务同步刷新。"}
            </p>
          </>
        )}
        <Gate capability={rule} />
        <button
          className="button primary"
          disabled={
            d.busy || d.uncertain || (preview && rule.status !== "AUTHORIZED")
          }
        >
          {preview ? "确认处置" : "预览处置结果"}
        </button>
      </form>
    </Modal>
  );
}
function Rules({ close }: { close: () => void }) {
  const d = useDelivery(),
    cap = d.data.capabilities.find((c) => c.accountId === d.scope.accountId),
    boundary = d.data.boundaries.find((b) => b.accountId === d.scope.accountId);
  const saved = d.draft as {
    kind?: string;
    mode?: PolicyRequest["mode"];
    values?: {
      roi: string;
      pacing: string;
      budget: string;
      observation: string;
      change: string;
      frequency: string;
    };
  } | null;
  const restore = saved?.kind === "RULES";
  const [mode, setMode] = useState<PolicyRequest["mode"]>(
      restore ? saved!.mode! : (cap?.automationMode ?? "OBSERVE"),
    ),
    [preview, setPreview] = useState(false);
  const [values, setValues] = useState(
    restore
      ? saved!.values!
      : {
          roi: "",
          pacing: "",
          budget: boundary?.dailyLimit.toString() ?? "",
          observation: boundary?.minObservationMinutes.toString() ?? "",
          change: boundary?.singleChangeLimit.toString() ?? "",
          frequency: boundary?.maxChangesPerDay.toString() ?? "",
        },
  );
  const rule = actionAvailability(
    d.data,
    d.identity,
    d.scope.accountId,
    "rules",
  );
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!preview) {
      setPreview(true);
      return;
    }
    await d.mutate(
      d.scope.accountId,
      "rules",
      () =>
        d.api.policy({
          accountId: d.scope.accountId,
          expectedVersion: boundary?.version ?? 0,
          mode,
          roiFloor: Number(values.roi),
          pacingThreshold: Number(values.pacing),
          budgetLimit: Number(values.budget),
          minObservationMinutes: Number(values.observation),
          singleChangeLimit: Number(values.change),
          maxChangesPerDay: Number(values.frequency),
          idempotencyKey: crypto.randomUUID(),
        }),
      close,
    );
  }
  return (
    <Modal title="账户策略规则与自动化权限" close={() => d.guard(close)}>
      <form className="dw-form" onSubmit={submit}>
        <label>
          工作模式
          <select
            value={mode}
            onChange={(e) => {
              setMode(e.target.value as PolicyRequest["mode"]);
              d.markDirty(true);
              setPreview(false);
              d.saveDraft({ kind: "RULES", mode: e.target.value, values });
            }}
          >
            {Object.entries(modeNames).map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <div className="dw-mode-descriptions">
          <p>
            <b>观察模式</b>：仅分析，不执行写入。
          </p>
          <p>
            <b>审批模式</b>：建议需授权人员批准后才可执行。
          </p>
          <p>
            <b>受控自动模式</b>：后端明确授权的低风险动作，满足规则后才可执行。
          </p>
        </div>
        <div className="dw-form-grid">
          {[
            ["roi", "ROI预警阈值"],
            ["pacing", "消耗异常阈值（%）"],
            ["budget", "日预算安全边界（原生最小单位）"],
            ["observation", "最小观察周期（分钟）"],
            ["change", "单次修改上限（原生最小单位）"],
            ["frequency", "最大调整频率（次/日）"],
          ].map(([key, label]) => (
            <label key={key}>
              {label}
              <input
                type="number"
                min="0"
                step={key === "roi" ? "0.01" : "1"}
                required
                value={values[key as keyof typeof values]}
                onChange={(e) => {
                  setValues({ ...values, [key]: e.target.value });
                  setPreview(false);
                  d.markDirty(true);
                  d.saveDraft({
                    kind: "RULES",
                    mode,
                    values: { ...values, [key]: e.target.value },
                  });
                }}
              />
            </label>
          ))}
        </div>
        <Gate capability={rule} />
        {preview && (
          <Json
            value={{
              accountId: d.scope.accountId,
              mode,
              ...values,
              requiresServerAuthorization: true,
            }}
          />
        )}
        <p className="dw-notice">
          增加消耗、调高预算和关闭重要计划默认需人工审批。前端切换模式不会授予平台权限。
        </p>
        <button
          className="button primary"
          disabled={
            d.busy || d.uncertain || (preview && rule.status !== "AUTHORIZED")
          }
        >
          {preview ? "提交规则审批" : "预览规则"}
        </button>
      </form>
    </Modal>
  );
}
