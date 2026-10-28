"use client";
import { useState, type FormEvent } from "react";
import Link from "next/link";
import { ArrowLeft, ArrowRight, Plus } from "@phosphor-icons/react";
import {
  actionAvailability,
  amount,
  dailyGoalGaps,
  scopeKey,
  time,
  type BusinessGoal,
  type GoalBreakdown,
} from "@/lib/delivery";
import { useDelivery } from "./context";
import {
  Gate,
  Json,
  Metrics,
  Missing,
  Modal,
  Progress,
  Section,
  Status,
  Trend,
} from "./ui";
import { AnalyzeButton } from "./shared";
const periods = {
  DAY: "日目标",
  WEEK: "周目标",
  MONTH: "月目标",
  LIVE: "直播场次目标",
  SHOP: "店铺目标",
};

export function Goals() {
  const d = useDelivery();
  const [status, setStatus] = useState("all"),
    [period, setPeriod] = useState("all");
  const [editing, setEditing] = useState<BusinessGoal | "new" | null>(null);
  const goals = d.data.goals.filter(
    (g) =>
      (status === "all" || g.status === status) &&
      (period === "all" || g.period === period),
  );
  const id = d.query.get("goalId"),
    selected =
      d.data.goals.find((g) => g.id === id) ?? (!id ? goals[0] : undefined);
  const breakdown = selected?.breakdown ?? [],
    allocated = breakdown.reduce((n, x) => n + x.amountMinor, 0);
  return (
    <>
      {id && (
        <Link className="dw-link" href={d.href("goals")}>
          <ArrowLeft size={15} />
          返回目标工作台
        </Link>
      )}
      <div className="dw-page-tools">
        <div className="dw-filters">
          <label>
            目标周期
            <select value={period} onChange={(e) => setPeriod(e.target.value)}>
              <option value="all">全部周期</option>
              {Object.entries(periods).map(([v, n]) => (
                <option key={v} value={v}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label>
            目标状态
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="all">全部状态</option>
              <option value="ACTIVE">进行中</option>
              <option value="DRAFT">草稿</option>
              <option value="CLOSED">已结束</option>
            </select>
          </label>
        </div>
        <div className="dw-actions">
          <AnalyzeButton
            kind="GOAL_GAP"
            ids={selected ? [selected.id] : []}
            label="AI分析目标差距"
          />
          <button className="button primary" onClick={() => setEditing("new")}>
            <Plus size={15} />
            新建经营目标
          </button>
        </div>
      </div>
      {goals.length > 0 && (
        <div className="dw-goal-selector">
          {goals.map((g) => (
            <Link
              key={g.id}
              href={d.href("goals", { goalId: g.id })}
              aria-current={g.id === selected?.id ? "page" : undefined}
            >
              <b>{g.name}</b>
              <span>
                {periods[g.period]} · {g.start} 至 {g.end}
              </span>
            </Link>
          ))}
        </div>
      )}
      <Metrics
        items={[
          {
            name: "经营支付GMV / 目标",
            value: selected
              ? `${amount(selected.actualBusinessGmvMinor)} / ${amount(selected.gmvTargetMinor)}`
              : "--",
            note: "全店交易口径，区别于广告归因GMV",
          },
          {
            name: "消耗 / 预算上限",
            value: selected
              ? `${amount(selected.spent, selected.currency)} / ${amount(selected.budget, selected.currency)}`
              : "--",
            note:
              selected?.currency === "BEAN"
                ? "微信豆独立预算"
                : "同周期、同范围经营预算",
          },
          {
            name: "投放ROI / 底线",
            value: selected
              ? `${selected.actualRoi?.toFixed(2) ?? "--"} / ${selected.roiFloor?.toFixed(2) ?? "--"}`
              : "--",
            note: "统计口径兼容时由后端提供",
          },
        ]}
      />
      {selected ? (
        <>
          <Section
            title={selected.name}
            detail={`${selected.start} 至 ${selected.end} · 版本 v${selected.version}`}
            action={
              <div className="dw-actions">
                <Status>{selected.status}</Status>
                <button
                  className="button secondary"
                  onClick={() => setEditing(selected)}
                >
                  调整目标与拆解
                </button>
              </div>
            }
          >
            <div className="dw-goal-progress">
              <Progress
                label="支付GMV达成"
                value={selected.actualBusinessGmvMinor}
                max={selected.gmvTargetMinor}
              />
              <Progress
                label="预算使用"
                value={selected.spent}
                max={selected.budget}
              />
              <Progress
                label="目标分配"
                value={allocated}
                max={selected.gmvTargetMinor}
              />
            </div>
            <div className="dw-chart-grid">
              <Trend
                title="实际GMV与目标累计趋势"
                points={selected.trends}
                actual="actualGmvMinor"
                target="targetGmvMinor"
              />
              <Trend
                title="广告消耗与预算进度"
                points={selected.trends}
                actual="spend"
                target="budget"
              />
              <Trend
                title="ROI表现与目标线"
                points={selected.trends}
                actual="roi"
                target="targetRoi"
              />
              <Trend
                title="周期完成预测"
                points={selected.trends}
                actual="actualGmvMinor"
                target="forecastGmvMinor"
                forecast
              />
            </div>
            <div className="dw-gap-strip">
                <h3>每日目标达成差额</h3>
              {selected.trends.length ? (
                dailyGoalGaps(selected.trends, selected.start).map((p) => (
                  <div key={p.date}>
                    <span>{p.date}</span>
                    <strong>
                      {amount(p.gapMinor)}
                    </strong>
                  </div>
                ))
              ) : (
                <p>每日目标与实际序列尚未接入。</p>
              )}
            </div>
          </Section>
          <div className="dw-two-col">
            <Section
              title="目标拆解与关联计划"
              detail={`已分配 ${amount(allocated)} · 未分配 ${amount(Math.max(0, selected.gmvTargetMinor - allocated))}`}
              action={
                <Link
                  className="dw-link"
                  href={d.href("plans", { goalId: selected.id })}
                >
                  查看计划 <ArrowRight size={14} />
                </Link>
              }
            >
              {breakdown.map((b) => (
                <div className="dw-breakdown" key={b.id}>
                  <div>
                    <b>{b.name}</b>
                    <small>
                      {b.type} · {b.targetId}
                    </small>
                  </div>
                  <span>{amount(b.amountMinor)}</span>
                  {b.type === "PLAN" ? (
                    <Link
                      href={d.href("plans", {
                        planId: b.targetId,
                        goalId: selected.id,
                      })}
                    >
                      计划详情
                    </Link>
                  ) : b.type === "LIVE" ? (
                    <Link
                      href={`/app/monitor?${new URLSearchParams({ shopId: selected.shopId, liveSessionId: b.targetId, goalId: selected.id })}`}
                    >
                      直播场次
                    </Link>
                  ) : (
                    <span>已关联</span>
                  )}
                </div>
              ))}
              {!breakdown.length && (
                <Missing
                  title="目标尚未拆解"
                  detail="可按店铺、平台、账户、计划或直播场次分配，保留真实对象ID。"
                />
              )}
            </Section>
            <Section
              title="目标调整记录"
              detail="目标变化后应重新评估关联预算和AI策略"
            >
              {selected.history.map((h, i) => (
                <article className="dw-audit-line" key={`${h.at}-${i}`}>
                  <span>{time(h.at)}</span>
                  <div>
                    <b>
                      {h.actor} · v{h.version}
                    </b>
                    <p>
                      {h.before} → {h.after}
                    </p>
                  </div>
                </article>
              ))}
              {!selected.history.length && <Missing title="暂无调整历史" />}
            </Section>
          </div>
        </>
      ) : (
        <Section title="经营目标趋势与拆解">
          <Missing
            title={id ? "此范围内未找到目标" : "尚无已同步的经营目标"}
            detail="现有风控日预算和ROI规则不等于经营目标，不会用它们伪造目标达成。可打开目标制定界面，配置后等待目标接口接入。"
          />
          <div className="dw-chart-grid">
            <Trend
              title="GMV累计趋势"
              points={[]}
              actual="actualGmvMinor"
              target="targetGmvMinor"
            />
            <Trend
              title="预算与周期预测"
              points={[]}
              actual="spend"
              target="budget"
            />
          </div>
        </Section>
      )}
      {editing && (
        <GoalEditor
          key={editing === "new" ? "new" : editing.id}
          goal={editing === "new" ? undefined : editing}
          close={() => setEditing(null)}
        />
      )}
    </>
  );
}

function GoalEditor({
  goal,
  close,
}: {
  goal?: BusinessGoal;
  close: () => void;
}) {
  const d = useDelivery();
  const initial = d.draft as {
    kind?: string;
    scope?: string;
    values?: Record<string, string>;
    breakdown?: GoalBreakdown[];
  } | null;
  const [values, setValues] = useState(
    initial?.kind === `GOAL:${goal?.id ?? "new"}` &&
      initial.scope === scopeKey(d.scope)
      ? initial.values!
      : {
          name: goal?.name ?? "",
          period: goal?.period ?? "MONTH",
          start: goal?.start ?? d.scope.start,
          end: goal?.end ?? d.scope.end,
          gmv: goal ? String(goal.gmvTargetMinor / 100) : "",
          budget: goal
            ? String(goal.budget / (goal.currency === "CNY" ? 100 : 1))
            : "",
          roi: goal?.roiFloor?.toString() ?? "",
        },
  );
  const [breakdown, setBreakdown] = useState<GoalBreakdown[]>(
      initial?.kind === `GOAL:${goal?.id ?? "new"}` &&
        initial.scope === scopeKey(d.scope)
        ? (initial.breakdown ?? [])
        : (goal?.breakdown ?? []),
    ),
    [preview, setPreview] = useState(false),
    [error, setError] = useState("");
  const currency = d.scope.platform === "beans" ? "BEAN" : "CNY";
  const accountId = goal?.accountId ?? d.scope.accountId;
  const rule = actionAvailability(d.data, d.identity, accountId, "goal");
  const save = (next: typeof values, split = breakdown) => {
    setValues(next);
    setBreakdown(split);
    setPreview(false);
    d.markDirty(true);
    d.saveDraft({
      kind: `GOAL:${goal?.id ?? "new"}`,
      scope: scopeKey(d.scope),
      values: next,
      breakdown: split,
    });
  };
  const gmvMinor = Math.round(Number(values.gmv) * 100),
    budget = Math.round(Number(values.budget) * (currency === "CNY" ? 100 : 1));
  const allocated = breakdown.reduce((sum, b) => sum + b.amountMinor, 0);
  function valid() {
    if (
      !values.name.trim() ||
      !Number.isSafeInteger(gmvMinor) ||
      gmvMinor <= 0 ||
      !Number.isSafeInteger(budget) ||
      budget <= 0 ||
      values.end < values.start ||
      allocated > gmvMinor ||
      breakdown.some((b) => !b.targetId || b.amountMinor <= 0) ||
      new Set(breakdown.map((b) => `${b.type}:${b.targetId}`)).size !==
        breakdown.length
    ) {
      setError(
        "请填写有效目标、周期及预算，拆解金额不得超过总目标，关联对象不得为空或重复。",
      );
      return false;
    }
    setError("");
    return true;
  }
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!valid()) return;
    if (!preview) {
      setPreview(true);
      return;
    }
    await d.mutate(
      accountId,
      "goal",
      () =>
        d.api.goal({
          name: values.name,
          period: values.period as BusinessGoal["period"],
          start: values.start,
          end: values.end,
          gmvTargetMinor: gmvMinor,
          budget,
          currency,
          roiFloor: values.roi ? Number(values.roi) : null,
          breakdown,
          scope: d.scope,
          goalId: goal?.id,
          expectedVersion: goal?.version,
          idempotencyKey: crypto.randomUUID(),
        }),
      close,
    );
  }
  const objects = (type: GoalBreakdown["type"]) =>
    type === "PLAN"
      ? d.data.plans.map((p) => ({ id: p.id, name: p.name }))
      : type === "ACCOUNT"
        ? d.data.accounts.map((a) => ({ id: a.id, name: a.name }))
        : type === "SHOP"
          ? d.data.shops.map((s) => ({ id: s.id, name: s.name }))
          : type === "PLATFORM"
            ? [
                { id: "adq", name: "腾讯广告 ADQ" },
                { id: "beans", name: "微信豆" },
              ]
            : d.data.plans.flatMap((p) =>
                p.linkedObjects.filter((o) => o.type === "LIVE"),
              );
  return (
    <Modal
      wide
      title={goal ? "目标调整与拆解" : "制定经营目标"}
      close={() => d.guard(close)}
    >
      <form className="dw-form" onSubmit={submit}>
        <div className="dw-form-grid">
          {[
            ["name", "目标名称", "text"],
            ["start", "有效期开始", "date"],
            ["end", "有效期结束", "date"],
            ["gmv", "经营支付GMV目标（元）", "number"],
            [
              "budget",
              `预算上限（${currency === "BEAN" ? "微信豆" : "元"}）`,
              "number",
            ],
            ["roi", "ROI底线（选填）", "number"],
          ].map(([key, label, type]) => (
            <label key={key}>
              {label}
              <input
                required={key !== "roi"}
                type={type}
                min={type === "number" ? "0.01" : undefined}
                step={key === "budget" && currency === "BEAN" ? "1" : "0.01"}
                value={values[key]}
                onChange={(e) => save({ ...values, [key]: e.target.value })}
              />
            </label>
          ))}
          <label>
            适用业务 / 周期
            <select
              value={values.period}
              onChange={(e) => save({ ...values, period: e.target.value })}
            >
              {Object.entries(periods).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="dw-subheading">
          <h3>目标拆解</h3>
          <button
            type="button"
            className="button secondary"
            onClick={() =>
              save(values, [
                ...breakdown,
                {
                  id: crypto.randomUUID(),
                  type: "PLAN",
                  targetId: "",
                  name: "",
                  amountMinor: 0,
                },
              ])
            }
          >
            添加分解对象
          </button>
        </div>
        {breakdown.map((b, i) => (
          <div className="dw-split-row" key={b.id}>
            <select
              aria-label={`拆解${i + 1}类型`}
              value={b.type}
              onChange={(e) =>
                save(
                  values,
                  breakdown.map((x) =>
                    x.id === b.id
                      ? {
                          ...x,
                          type: e.target.value as GoalBreakdown["type"],
                          targetId: "",
                          name: "",
                        }
                      : x,
                  ),
                )
              }
            >
              {[
                ["SHOP", "店铺"],
                ["PLATFORM", "平台"],
                ["ACCOUNT", "账户"],
                ["PLAN", "计划"],
                ["LIVE", "直播场次"],
              ].map(([v, n]) => (
                <option key={v} value={v}>
                  {n}
                </option>
              ))}
            </select>
            <select
              aria-label={`拆解${i + 1}对象`}
              value={b.targetId}
              onChange={(e) =>
                save(
                  values,
                  breakdown.map((x) =>
                    x.id === b.id
                      ? {
                          ...x,
                          targetId: e.target.value,
                          name:
                            objects(b.type).find((o) => o.id === e.target.value)
                              ?.name ?? "",
                        }
                      : x,
                  ),
                )
              }
            >
              <option value="">选择实际对象</option>
              {objects(b.type)
                .filter(
                  (o, j, all) => all.findIndex((x) => x.id === o.id) === j,
                )
                .map((o) => (
                  <option value={o.id} key={o.id}>
                    {o.name}
                  </option>
                ))}
            </select>
            <input
              aria-label={`拆解${i + 1}金额（元）`}
              type="number"
              min="0.01"
              step="0.01"
              value={b.amountMinor / 100 || ""}
              onChange={(e) =>
                save(
                  values,
                  breakdown.map((x) =>
                    x.id === b.id
                      ? {
                          ...x,
                          amountMinor: Math.round(Number(e.target.value) * 100),
                        }
                      : x,
                  ),
                )
              }
            />
            <button
              type="button"
              onClick={() =>
                save(
                  values,
                  breakdown.filter((x) => x.id !== b.id),
                )
              }
            >
              移除
            </button>
          </div>
        ))}
        <p>未分配目标 {amount(Math.max(0, gmvMinor - allocated))}</p>
        {preview && (
          <>
            <div className="dw-comparison">
              <div>
                <span>调整前</span>
                <Json
                  value={
                    goal
                      ? {
                          gmvTargetMinor: goal.gmvTargetMinor,
                          budget: goal.budget,
                          version: goal.version,
                        }
                      : "新建目标"
                  }
                />
              </div>
              <ArrowRight size={18} />
              <div>
                <span>调整后</span>
                <Json value={{ gmvTargetMinor: gmvMinor, budget, breakdown }} />
              </div>
            </div>
            <p className="dw-notice">
              保存后关联预算及AI策略需重新评估。服务端必须校验经营范围、对象归属与目标版本。
            </p>
          </>
        )}
        <Gate capability={rule} />
        {error && <p role="alert">{error}</p>}
        <button
          className="button primary"
          disabled={
            d.busy || d.uncertain || (preview && rule.status !== "AUTHORIZED")
          }
        >
          {preview ? "确认并保存目标" : "校验并预览目标"}
        </button>
      </form>
    </Modal>
  );
}
