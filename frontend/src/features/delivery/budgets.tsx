"use client";
import { useState, type FormEvent } from "react";
import Link from "next/link";
import { ArrowRight, ArrowsLeftRight, Wallet } from "@phosphor-icons/react";
import {
  actionAvailability,
  amount,
  availabilityNames,
  platforms,
  scopeKey,
  time,
  type BudgetAllocation,
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
import { ApprovalReview } from "./approval";

export function Budgets({ section = "allocation" }: { section?: "allocation" | "funds" | "rules" }) {
  const d = useDelivery();
  const tab = section === "allocation" ? "预算分配" : section === "funds" ? "账户资金" : "风险控制";
  const [edit, setEdit] = useState<BudgetAllocation | null>(null),
    [create, setCreate] = useState(false);
  const [approval, setApproval] = useState<BudgetAllocation | null>(null);
  const rows = d.data.budgets.filter(
    (b) => !d.query.get("planId") || b.planId === d.query.get("planId"),
  );
  const roots = rows.filter((b) => b.planId === null),
    units =
      d.scope.platform === "all"
        ? (["CNY", "BEAN"] as const)
        : [platforms[d.scope.platform].currency];
  return (
    <>
      {section === "allocation" && <div className="dw-page-tools">
        <p className="dw-muted">经营额度、账户余额和平台计划预算分别管理。</p>
        <AnalyzeButton
          kind="BUDGET"
          ids={rows.map((x) => x.id)}
          label="AI生成预算建议"
        />
      </div>}
      {section === "allocation" && units.map((unit) => {
        const current = roots.filter((b) => b.currency === unit),
          complete =
            current.length > 0 && current.every((b) => b.spent !== null),
          limit = current.length
            ? current.reduce((s, b) => s + b.limit, 0)
            : null,
          used = complete ? current.reduce((s, b) => s + b.spent!, 0) : null;
        return (
          <div key={unit} className="dw-budget-unit">
            <span className="dw-unit-label">
              <Wallet size={15} />
              {unit === "CNY" ? "人民币经营额度" : "微信豆经营额度"}
            </span>
            <Metrics
              items={[
                {
                  name: "周期预算上限",
                  value: amount(limit, unit),
                  note: `${d.scope.start} 至 ${d.scope.end}`,
                },
                {
                  name: "已使用预算",
                  value: amount(used, unit),
                  note: "仅同资金单位汇总",
                },
                {
                  name: "剩余经营额度",
                  value: amount(
                    limit !== null && used !== null ? limit - used : null,
                    unit,
                  ),
                  note: "内部经营额度不等于账户余额",
                },
                {
                  name: "待审批调整",
                  value: current.filter(
                    (b) => b.internalStatus === "PENDING_APPROVAL",
                  ).length,
                  note: "批准后仍需平台执行和核验",
                },
              ]}
            />
            <Progress
              label={`${unit === "CNY" ? "人民币" : "微信豆"}预算进度`}
              value={used}
              max={limit ?? 0}
            />
          </div>
        );
      })}
      <div>
        {tab === "预算分配" && (
          <div className="dw-budget-grid">
            <Section
              title="账户与计划分配矩阵"
              detail="内部方案、平台预算修改和核验结果分别记录"
              action={<Status>{rows.length} 个分配对象</Status>}
            >
              <div className="dw-table-scroll">
                <table className="dw-table">
                  <thead>
                    <tr>
                      <th>账户 / 关联计划</th>
                      <th>预算上限</th>
                      <th>已分配 / 消耗</th>
                      <th>内部方案</th>
                      <th>平台修改</th>
                      <th>核验状态</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((b) => (
                      <tr key={b.id}>
                        <td>
                          <b>
                            {d.data.accounts.find((a) => a.id === b.accountId)
                              ?.name ?? b.accountId}
                          </b>
                          <small>
                            {b.planId
                              ? (d.data.plans.find((p) => p.id === b.planId)
                                  ?.name ?? b.planId)
                              : "账户经营预算"}
                          </small>
                        </td>
                        <td>{amount(b.limit, b.currency)}</td>
                        <td>
                          {amount(b.allocated, b.currency)}
                          <small>消耗 {amount(b.spent, b.currency)}</small>
                          <Progress
                            label="分配进度"
                            value={b.allocated}
                            max={b.limit}
                          />
                        </td>
                        <td>
                          <Status>{b.internalStatus}</Status>
                          {b.approvalId && <small>审批 {b.approvalId}</small>}
                        </td>
                        <td>{b.platformStatus}</td>
                        <td>{b.verificationStatus}</td>
                        <td>
                          <button
                            className="dw-link"
                            onClick={() => setEdit(b)}
                          >
                            调整分配
                          </button>
                          {b.internalStatus === "PENDING_APPROVAL" && (
                            <button
                              className="dw-link"
                              onClick={() => setApproval(b)}
                            >
                              查看并审批
                            </button>
                          )}
                          {b.executionId && (
                            <Link
                              className="dw-link"
                              href={d.href("executions", {
                                taskId: b.executionId,
                              })}
                            >
                              执行进度
                            </Link>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!rows.length && (
                <Missing
                  title="预算分配接口待接入"
                  detail="已有计划日预算仍在计划详情保留。缺少经营分配对象时，不把计划预算当作账户余额或可调拨资金。"
                />
              )}
              <p className="dw-notice">
                内部预算分配成功不代表广告账户发生资金转移，也不代表平台预算已修改。
              </p>
            </Section>
            <Section title="预算消耗趋势">
              {units.map((unit) => (
                <Trend
                  key={unit}
                  title={`${unit === "CNY" ? "人民币" : "微信豆"}预算与实际消耗`}
                  points={
                    d.data.budgetTrends.find((t) => t.currency === unit)
                      ?.points ?? []
                  }
                  actual="spend"
                  target="budget"
                />
              ))}
              <div className="dw-next-step">
                <ArrowsLeftRight size={23} />
                <h3>分配前先核对安全边界</h3>
                <p>服务端校验可用额度、单次调整上限、审批阈值和并发版本。</p>
                <button
                  className="button secondary"
                  onClick={() => d.navigate(d.href("decisions", { section: "rules", planId: d.query.get("planId") }))}
                >
                  查看预算边界
                </button>
              </div>
            </Section>
          </div>
        )}
        {tab === "账户资金" && (
          <Section
            title="实际账户资金"
            detail="余额仅展示平台同步值；微信豆不按固定比例换算人民币"
          >
            <div className="dw-funds-grid">
              {d.data.accounts
                .filter(
                  (a) =>
                    (d.scope.shopId === "all" || a.shopId === d.scope.shopId) &&
                    (d.scope.platform === "all" ||
                      a.platform === d.scope.platform) &&
                    (d.scope.accountId === "all" || a.id === d.scope.accountId),
                )
                .map((a) => {
                  const f = d.data.funds.find((x) => x.accountId === a.id);
                  return (
                    <article key={a.id}>
                      <div className="dw-subheading">
                        <h3>{a.name}</h3>
                        <Status>{availabilityNames[a.authorization]}</Status>
                      </div>
                      <p>
                        {platforms[a.platform].name} · {a.id}
                      </p>
                      <strong>{amount(f?.balance, a.currency)}</strong>
                      <small>最近同步 {time(f?.syncedAt)}</small>
                      {f?.warning && <p className="dw-notice">{f.warning}</p>}
                      <a
                        className="dw-link"
                        href={
                          a.platform === "adq"
                            ? "https://ad.qq.com/"
                            : "https://channels.weixin.qq.com/"
                        }
                        target="_blank"
                        rel="noreferrer"
                      >
                        前往平台核对 <ArrowRight size={14} />
                      </a>
                    </article>
                  );
                })}
            </div>
            {!d.data.accounts.length && <Missing title="暂无已关联账户" />}
          </Section>
        )}
        {tab === "风险控制" && (
          <Section title="预算安全边界">
            <div className="dw-risk-grid">
              {d.data.boundaries.map((b) => (
                <article key={b.accountId}>
                  <h3>
                    {d.data.accounts.find((a) => a.id === b.accountId)?.name ??
                      b.accountId}
                  </h3>
                  <dl>
                    <dt>日预算上限</dt>
                    <dd>{amount(b.dailyLimit, b.currency)}</dd>
                    <dt>单次修改上限</dt>
                    <dd>{amount(b.singleChangeLimit, b.currency)}</dd>
                    <dt>人工审批阈值</dt>
                    <dd>{amount(b.approvalThreshold, b.currency)}</dd>
                    <dt>最小观察周期</dt>
                    <dd>{b.minObservationMinutes} 分钟</dd>
                    <dt>最大调整频率</dt>
                    <dd>{b.maxChangesPerDay} 次 / 日</dd>
                    <dt>规则版本</dt>
                    <dd>v{b.version}</dd>
                  </dl>
                </article>
              ))}
            </div>
            {!d.data.boundaries.length && (
              <Missing
                title="账户级预算安全边界待接入"
                detail="企业现有风控规则继续由原服务维护，不将企业CNY规则直接套用到微信豆。"
              />
            )}
          </Section>
        )}
      </div>
      {tab === "预算分配" && (
        <button className="button secondary" onClick={() => setCreate(true)}>
          新建预算分配方案
        </button>
      )}
      {create && <NewBudget close={() => setCreate(false)} />}
      {approval && (
        <ApprovalReview
          approvalId={approval.approvalId}
          accountId={approval.accountId}
          budgetId={approval.id}
          close={() => setApproval(null)}
        />
      )}
      {edit && (
        <BudgetEditor
          key={edit.id}
          allocation={edit}
          close={() => setEdit(null)}
        />
      )}
    </>
  );
}
function NewBudget({ close }: { close: () => void }) {
  const d = useDelivery(),
    account = d.data.accounts.find(
      (a) => a.id === d.scope.accountId && a.platform === d.scope.platform,
    );
  const draft = d.draft as {
    kind?: string;
    scope?: string;
    planId?: string;
    value?: string;
    reason?: string;
  } | null;
  const restore =
    draft?.kind === "NEW_BUDGET" && draft.scope === scopeKey(d.scope);
  const [planId, setPlanId] = useState(restore ? (draft!.planId ?? "") : ""),
    [value, setValue] = useState(restore ? (draft!.value ?? "") : ""),
    [reason, setReason] = useState(restore ? (draft!.reason ?? "") : ""),
    [preview, setPreview] = useState(false);
  const parent = d.data.budgets.find(
    (b) => b.accountId === account?.id && b.planId === null,
  );
  const rule = actionAvailability(
    d.data,
    d.identity,
    account?.id ?? "all",
    "allocate",
  );
  const amountNative = Number(value) * (account?.currency === "BEAN" ? 1 : 100);
  const valid =
    !!account &&
    Number.isSafeInteger(amountNative) &&
    amountNative > 0 &&
    !!reason.trim() &&
    (!parent || amountNative <= parent.limit - parent.allocated);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!valid || !account) return;
    if (!preview) {
      setPreview(true);
      return;
    }
    await d.mutate(
      account.id,
      "allocate",
      () =>
        d.api.allocate({
          scope: d.scope,
          accountId: account.id,
          planId: planId || null,
          amount: amountNative,
          currency: account.currency,
          parentAllocationId: parent?.id ?? null,
          expectedParentVersion: parent?.version ?? null,
          reason,
          idempotencyKey: crypto.randomUUID(),
        }),
      close,
    );
  }
  const dirty = () => {
    d.markDirty(true);
    setPreview(false);
  };
  return (
    <Modal title="新建内部预算分配方案" close={() => d.guard(close)}>
      <form className="dw-form" onSubmit={submit}>
        <p>
          {account
            ? `${account.name} · ${account.id}`
            : "先在顶部选择具体平台与账户；无实际账户时不会创建虚构分配对象。"}
        </p>
        <label>
          分配层级
          <select
            value={planId}
            onChange={(e) => {
              setPlanId(e.target.value);
              dirty();
            }}
          >
            <option value="">账户经营预算</option>
            {d.data.plans
              .filter((p) => p.accountId === account?.id)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
          </select>
        </label>
        <label>
          分配额度（{account?.currency === "BEAN" ? "微信豆" : "元"}）
          <input
            type="number"
            required
            step={account?.currency === "BEAN" ? "1" : "0.01"}
            min="0.01"
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              dirty();
              d.saveDraft({
                kind: "NEW_BUDGET",
                scope: scopeKey(d.scope),
                planId,
                value: e.target.value,
                reason,
              });
            }}
          />
        </label>
        <label>
          分配原因
          <textarea
            required
            value={reason}
            onChange={(e) => {
              setReason(e.target.value);
              dirty();
              d.saveDraft({
                kind: "NEW_BUDGET",
                scope: scopeKey(d.scope),
                planId,
                value,
                reason: e.target.value,
              });
            }}
          />
        </label>
        <p>
          上级可用经营额度：
          {parent
            ? amount(parent.limit - parent.allocated, parent.currency)
            : "未提供，由后端重新校验"}
        </p>
        {preview && (
          <>
            <Json
              value={{
                accountId: account?.id,
                planId: planId || null,
                before: "尚未创建分配对象",
                after: amount(amountNative, account?.currency),
                parentAllocationId: parent?.id ?? null,
                reason,
              }}
            />
            <p className="dw-notice">
              提交内部方案审批，不会触发真实资金转账。后端重新校验可用额度、账户边界与上级版本。
            </p>
          </>
        )}
        <Gate capability={rule} />
        <button
          className="button primary"
          disabled={
            !valid ||
            d.busy ||
            d.uncertain ||
            (preview && rule.status !== "AUTHORIZED")
          }
        >
          {preview ? "确认并提交分配审批" : "预览分配方案"}
        </button>
      </form>
    </Modal>
  );
}
function BudgetEditor({
  allocation: b,
  close,
}: {
  allocation: BudgetAllocation;
  close: () => void;
}) {
  const d = useDelivery(),
    decision = d.data.decisions.find((x) => x.id === d.query.get("decisionId"));
  const saved = d.draft as {
    kind?: string;
    scope?: string;
    value?: string;
    reason?: string;
  } | null;
  const restore =
    saved?.kind === `BUDGET:${b.id}` && saved.scope === scopeKey(d.scope);
  const [value, setValue] = useState(
      restore
        ? saved!.value!
        : String(b.allocated / (b.currency === "CNY" ? 100 : 1)),
    ),
    [reason, setReason] = useState(
      restore ? saved!.reason! : (decision?.recommendation ?? ""),
    ),
    [preview, setPreview] = useState(false);
  const newAmount = Number(value) * (b.currency === "CNY" ? 100 : 1),
    rule = actionAvailability(d.data, d.identity, b.accountId, "allocate");
  const valid =
    Number.isSafeInteger(newAmount) &&
    newAmount >= 0 &&
    newAmount <= b.limit &&
    newAmount !== b.allocated &&
    !!reason.trim();
  const changed = (v: string, r: string) => {
    setValue(v);
    setReason(r);
    setPreview(false);
    d.markDirty(true);
    d.saveDraft({
      kind: `BUDGET:${b.id}`,
      scope: scopeKey(d.scope),
      value: v,
      reason: r,
    });
  };
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!valid) return;
    if (!preview) {
      setPreview(true);
      return;
    }
    await d.mutate(
      b.accountId,
      "allocate",
      () =>
        d.api.budget({
          scope: d.scope,
          allocationId: b.id,
          planId: b.planId,
          amount: newAmount,
          reason,
          expectedVersion: b.version,
          decisionId: decision?.id,
          idempotencyKey: crypto.randomUUID(),
        }),
      close,
    );
  }
  return (
    <Modal title="调整内部经营预算" close={() => d.guard(close)}>
      <form className="dw-form" onSubmit={submit}>
        <p>
          分配对象 {b.id} · 版本 v{b.version}
        </p>
        <label>
          新分配额度（{b.currency === "CNY" ? "元" : "微信豆"}）
          <input
            type="number"
            min="0"
            step={b.currency === "CNY" ? "0.01" : "1"}
            required
            value={value}
            onChange={(e) => changed(e.target.value, reason)}
          />
        </label>
        <label>
          调整原因
          <textarea
            required
            value={reason}
            onChange={(e) => changed(value, e.target.value)}
          />
        </label>
        <p>
          账户可用经营上限 {amount(b.limit, b.currency)} · 当前分配{" "}
          {amount(b.allocated, b.currency)}
        </p>
        {!valid && (
          <p className="dw-notice">
            额度应在经营上限内，且与原配置不同，并填写调整原因。
          </p>
        )}
        {preview && (
          <>
            <div className="dw-comparison">
              <div>
                <span>当前额度</span>
                <b>{amount(b.allocated, b.currency)}</b>
              </div>
              <ArrowRight size={20} />
              <div>
                <span>调整后额度</span>
                <b>{amount(newAmount, b.currency)}</b>
              </div>
            </div>
            <Json
              value={{
                internalStatus: "提交后等待后端审批",
                platformStatus: "未提交平台",
                verificationStatus: "未核验",
                decisionId: decision?.id ?? null,
                expectedVersion: b.version,
              }}
            />
            <p className="dw-notice">
              本次为内部预算调整申请，后端校验额度并创建审批；不代表账户资金转移。
            </p>
          </>
        )}
        <Gate capability={rule} />
        <button
          className="button primary"
          disabled={
            !valid ||
            d.busy ||
            d.uncertain ||
            (preview && rule.status !== "AUTHORIZED")
          }
        >
          {preview ? "确认并提交预算审批" : "校验额度并预览"}
        </button>
      </form>
    </Modal>
  );
}
