"use client";
import { useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ArrowRight,
  CaretRight,
  Columns,
  MagnifyingGlass,
  Plus,
  Robot,
  ShieldCheck,
} from "@phosphor-icons/react";
import { useWorkspace } from "@/components/provider";
import {
  actionAvailability,
  amount,
  defaultPlanFilter,
  deliveryHref,
  filterPlans,
  planStatusNames,
  platforms,
  scopeKey,
  time,
  type Action,
  type Campaign,
  type Plan,
  type PlanFilter,
} from "@/lib/delivery";
import type { CommandJob } from "@/lib/command-job";
import { displayTime } from "@/lib/command-job";
import type {
  PlanChangeRequest,
  RiskPreview,
} from "@/services/delivery-service";
import { useDelivery } from "./context";
import {
  Gate,
  Json,
  Metrics,
  Missing,
  Modal,
  Section,
  Status,
  Trend,
} from "./ui";

export function Plans() {
  const d = useDelivery();
  const filter = d.filters[d.scope.platform] ?? defaultPlanFilter;
  const currentCapability = d.data.capabilities.find(
    (c) => c.accountId === d.scope.accountId,
  );
  const visibleColumns = filter.columns.filter(
    (column) =>
      !currentCapability || currentCapability.supportedColumns.includes(column),
  );
  const patch = (value: Partial<PlanFilter>) =>
    d.setFilter(d.scope.platform, { ...filter, page: 1, ...value });
  const [selection, setSelection] = useState<{ key: string; ids: string[] }>({
    key: "",
    ids: [],
  });
  const key = scopeKey(d.scope),
    selected = selection.key === key ? selection.ids : [];
  const [columns, setColumns] = useState(false),
    [batch, setBatch] = useState(false);
  const [operation, setOperation] = useState<Plan | null>(null);
  const goal = d.query.get("goalId");
  const relatedPlans = goal
    ? new Set(
        d.data.goals
          .find((g) => g.id === goal)
          ?.breakdown.filter((b) => b.type === "PLAN")
          .map((b) => b.targetId) ?? [],
      )
    : null;
  const plans = filterPlans(
      d.data.plans.filter((p) => !relatedPlans || relatedPlans.has(p.id)),
      {
        ...filter,
        columns: visibleColumns,
        status:
          currentCapability &&
          !currentCapability.supportedFilters.includes("status")
            ? "all"
            : filter.status,
        level:
          currentCapability &&
          !currentCapability.supportedFilters.includes("level") &&
          !currentCapability.supportedFilters.includes("content")
            ? "all"
            : filter.level,
      },
    ),
    pages = Math.max(1, Math.ceil(plans.length / filter.pageSize)),
    page = Math.min(filter.page, pages);
  const rows = plans.slice(
    (page - 1) * filter.pageSize,
    page * filter.pageSize,
  );
  const detail = d.query.get("planId"),
    active = d.data.plans.find((p) => p.id === detail);
  const relevant = d.data.executions.filter(
    (t) =>
      new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(
        new Date(t.createdAt),
      ) === d.scope.end,
  );
  if (detail)
    return active ? (
      <PlanDetail
        plan={active}
        operate={() => setOperation(active)}
        operation={operation}
        closeOperation={() => setOperation(null)}
      />
    ) : (
      <Section title="投放对象详情">
        <Link className="dw-link" href={d.href("plans")}>
          <ArrowLeft size={15} />
          返回计划中心
        </Link>
        <Missing
          title={d.loading ? "正在读取投放对象" : "当前范围内未找到该投放对象"}
          detail="对象可能不属于此店铺、账户或平台，也可能尚未同步。请检查范围后重试。"
        />
      </Section>
    );
  const noun =
    d.scope.platform === "all"
      ? "投放对象"
      : platforms[d.scope.platform].object;
  const setSelected = (ids: string[]) => setSelection({ key, ids });
  return (
    <>
      <Metrics
        items={[
          {
            name: `运行中的${noun}`,
            value: d.data.plans.filter((p) => p.status === "RUNNING").length,
            note: "当前平台与账户范围",
          },
          {
            name: "待审核",
            value: d.data.plans.filter((p) => p.status === "REVIEW").length,
            note: "以平台审核状态为准",
          },
          {
            name: "投放异常",
            value: d.data.plans.filter((p) => p.status === "FAILED").length,
            note: "数据异常与平台失败分开核查",
          },
          {
            name: "当日变更申请",
            value: relevant.length,
            note: `${d.scope.end} · 含尚未生效的申请`,
          },
        ]}
      />
      <Section
        title={d.scope.platform === "beans" ? "加热订单工作区" : "计划工作区"}
        detail={
          d.scope.platform === "all"
            ? "跨渠道只读汇总；创建和修改前需选择具体平台及账户。"
            : `${platforms[d.scope.platform].name} · ${platforms[d.scope.platform].object}与原始ID独立保留`
        }
        action={
          <button
            className="button primary"
            onClick={() =>
              d.navigate(
                d.href("plans", {
                  view: "create",
                  product: d.scope.platform === "beans" ? "beans" : "adq",
                }),
              )
            }
          >
            <Plus size={16} />
            {d.scope.platform === "beans" ? "配置加热方案" : "新建投放"}
          </button>
        }
      >
        <div className="dw-filters">
          <label className="dw-search">
            <MagnifyingGlass size={16} />
            <input
              aria-label="搜索投放对象"
              placeholder="搜索名称、业务ID或平台原始ID"
              value={filter.search}
              onChange={(e) => patch({ search: e.target.value })}
            />
          </label>
          {(!currentCapability ||
            currentCapability.supportedFilters.includes("status")) && (
            <select
              aria-label="投放状态"
              value={filter.status}
              onChange={(e) => patch({ status: e.target.value })}
            >
              <option value="all">全部状态</option>
              {Object.entries(planStatusNames).map(([v, name]) => (
                <option value={v} key={v}>
                  {name}
                </option>
              ))}
            </select>
          )}
          {d.scope.platform !== "all" &&
            (!currentCapability ||
              currentCapability.supportedFilters.includes(
                d.scope.platform === "adq" ? "level" : "content",
              )) && (
              <select
                aria-label={
                  d.scope.platform === "adq" ? "广告层级" : "内容类型"
                }
                value={filter.level}
                onChange={(e) => patch({ level: e.target.value })}
              >
                <option value="all">
                  {d.scope.platform === "adq" ? "全部广告层级" : "全部内容类型"}
                </option>
                {(d.scope.platform === "adq"
                  ? [
                      ["CAMPAIGN", "推广计划"],
                      ["ADGROUP", "广告组"],
                      ["AD", "广告"],
                    ]
                  : [
                      ["VIDEO", "短视频"],
                      ["LIVE", "直播"],
                    ]
                )
                  .filter(
                    ([v]) =>
                      !currentCapability ||
                      currentCapability.supportedObjects.includes(v),
                  )
                  .map(([v, n]) => (
                    <option key={v} value={v}>
                      {n}
                    </option>
                  ))}
              </select>
            )}
          <select
            aria-label="排序方式"
            value={filter.sort}
            onChange={(e) =>
              patch({ sort: e.target.value as PlanFilter["sort"] })
            }
          >
            <option value="updated">按更新时间</option>
            <option value="spend">按消耗</option>
            <option value="name">按名称</option>
          </select>
          <button
            className="button secondary"
            onClick={() => patch({ descending: !filter.descending })}
          >
            {filter.descending ? "降序" : "升序"}
          </button>
          <button
            className="button secondary"
            onClick={() => setColumns(!columns)}
            aria-expanded={columns}
          >
            <Columns size={15} />
            自定义列
          </button>
        </div>
        {columns && (
          <div className="dw-column-options">
            {[
              ["spend", "消耗"],
              ["result", "效果"],
              ["budget", "预算 / 额度"],
              ...(d.scope.platform === "beans" ? [] : [["bid", "出价"]]),
              ["updated", "更新时间"],
            ]
              .filter(
                ([id]) =>
                  !currentCapability ||
                  currentCapability.supportedColumns.includes(id),
              )
              .map(([id, name]) => (
                <label key={id}>
                  <input
                    type="checkbox"
                    checked={visibleColumns.includes(id)}
                    onChange={(e) =>
                      patch({
                        columns: e.target.checked
                          ? [...filter.columns, id]
                          : filter.columns.filter((v) => v !== id),
                      })
                    }
                  />
                  {name}
                </label>
              ))}
          </div>
        )}
        <div className="dw-bulk">
          <span>已选 {selected.length} 项</span>
          <button disabled={!selected.length} onClick={() => setBatch(true)}>
            批量操作预览
          </button>
          <button
            disabled={selected.length !== 1}
            onClick={() =>
              d.navigate(d.href("decisions", { planId: selected[0] }))
            }
          >
            <Robot size={14} />
            AI诊断选中对象
          </button>
          {selected.length > 0 && (
            <button onClick={() => setSelected([])}>清空选择</button>
          )}
        </div>
        <div className="dw-table-scroll">
          <table className="dw-table">
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    aria-label="选择当前页"
                    checked={
                      rows.length > 0 &&
                      rows.every((p) => selected.includes(p.id))
                    }
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [
                              ...new Set([
                                ...selected,
                                ...rows.map((p) => p.id),
                              ]),
                            ]
                          : selected.filter(
                              (id) => !rows.some((p) => p.id === id),
                            ),
                      )
                    }
                  />
                </th>
                <th>{noun} / 原始ID</th>
                <th>账户 / 渠道</th>
                <th>状态</th>
                {visibleColumns.includes("spend") && <th>消耗</th>}
                {visibleColumns.includes("result") && (
                  <th>
                    {d.scope.platform === "beans"
                      ? "加热效果 / 进度"
                      : "归因效果 / ROI"}
                  </th>
                )}
                {visibleColumns.includes("budget") && (
                  <th>
                    {d.scope.platform === "beans" ? "订单额度" : "预算 / 额度"}
                  </th>
                )}
                {visibleColumns.includes("bid") &&
                  d.scope.platform !== "beans" && <th>出价</th>}
                {visibleColumns.includes("updated") && <th>平台更新</th>}
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`选择${p.name}`}
                      checked={selected.includes(p.id)}
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? [...selected, p.id]
                            : selected.filter((id) => id !== p.id),
                        )
                      }
                    />
                  </td>
                  <td>
                    <Link
                      href={d.href("plans", { planId: p.id })}
                      className="dw-object-name"
                    >
                      {p.name}
                    </Link>
                    <small>
                      {p.nativeId} ·{" "}
                      {p.kind === "CAMPAIGN"
                        ? p.level
                        : p.contentType === "LIVE"
                          ? "直播加热"
                          : "短视频加热"}
                    </small>
                  </td>
                  <td>
                    {d.data.accounts.find((a) => a.id === p.accountId)?.name}
                    <small>{platforms[p.platform].name}</small>
                  </td>
                  <td>
                    <Status
                      tone={
                        p.status === "RUNNING"
                          ? "green"
                          : p.status === "FAILED"
                            ? "red"
                            : "neutral"
                      }
                    >
                      {planStatusNames[p.status]}
                    </Status>
                  </td>
                  {visibleColumns.includes("spend") && (
                    <td>
                      {amount(p.metrics.spend, platforms[p.platform].currency)}
                    </td>
                  )}
                  {visibleColumns.includes("result") && (
                    <td>
                      {p.kind === "CAMPAIGN" ? (
                        <>
                          {amount(p.metrics.attributedGmvMinor)}
                          <small>ROI {p.metrics.roi?.toFixed(2) ?? "--"}</small>
                        </>
                      ) : (
                        <>
                          {p.extensions.views?.toLocaleString() ?? "--"} 播放
                          <small>
                            {p.progress === null
                              ? "进度未接入"
                              : `${p.progress}%`}
                          </small>
                        </>
                      )}
                    </td>
                  )}
                  {visibleColumns.includes("budget") && (
                    <td>
                      {p.kind === "CAMPAIGN"
                        ? amount(p.budgetMinor)
                        : amount(p.amountBeans, "BEAN")}
                    </td>
                  )}
                  {visibleColumns.includes("bid") &&
                    d.scope.platform !== "beans" && (
                      <td>
                        {p.kind === "CAMPAIGN" ? amount(p.bidMinor) : "不适用"}
                      </td>
                    )}
                  {visibleColumns.includes("updated") && (
                    <td>{time(p.updatedAt)}</td>
                  )}
                  <td>
                    <div className="dw-row-actions">
                      <Link href={d.href("plans", { planId: p.id })}>详情</Link>
                      {p.kind === "CAMPAIGN" && (
                        <button onClick={() => setOperation(p)}>调整</button>
                      )}
                      <Link href={d.href("decisions", { planId: p.id })}>
                        诊断
                      </Link>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!rows.length && (
          <Missing
            title={
              d.loading
                ? "正在读取投放数据"
                : d.scope.platform === "beans"
                  ? "暂无已接入的加热订单"
                  : "当前条件下暂无投放对象"
            }
            detail={
              d.scope.platform === "beans"
                ? "微信豆订单与 ADQ 计划使用独立模型。可配置人工加热方案，授权订单接口接入后展示结果。"
                : "可调整搜索、状态、日期或账户范围。未接入数据不会回退为演示记录。"
            }
          />
        )}
        <div className="dw-pagination">
          <span>
            共 {plans.length} 项 · 第 {page} / {pages} 页
          </span>
          <select
            aria-label="每页条数"
            value={filter.pageSize}
            onChange={(e) => patch({ pageSize: Number(e.target.value) })}
          >
            {[10, 20, 50].map((n) => (
              <option key={n} value={n}>
                {n} 条 / 页
              </option>
            ))}
          </select>
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
      {operation && (
        <Operation
          key={operation.id}
          plan={operation}
          close={() => setOperation(null)}
        />
      )}
      {batch && (
        <Modal title="批量操作预览" close={() => setBatch(false)}>
          <p>
            已选 {selected.length}{" "}
            个对象。每个对象均需独立版本、风险预检与审批。
          </p>
          {d.data.plans
            .filter((p) => selected.includes(p.id))
            .map((p) => (
              <div className="dw-list-row" key={p.id}>
                <span>
                  {p.name}
                  <small>
                    {platforms[p.platform].name} · {p.accountId}
                  </small>
                </span>
                <button
                  className="button secondary"
                  onClick={() => {
                    setBatch(false);
                    setOperation(p);
                  }}
                >
                  逐项校验
                </button>
              </div>
            ))}
          <p className="dw-notice">
            批量写入契约尚待后端提供，当前可逐项生成审批申请。部分失败或结果未知时不会自动重放。
          </p>
        </Modal>
      )}
    </>
  );
}

function PlanDetail({
  plan,
  operate,
  operation,
  closeOperation,
}: {
  plan: Plan;
  operate: () => void;
  operation: Plan | null;
  closeOperation: () => void;
}) {
  const d = useDelivery();
  const [tab, setTab] = useState("配置");
  const decisions = d.data.decisions.filter((x) => x.planId === plan.id),
    tasks = d.data.executions.filter((x) => x.planId === plan.id);
  return (
    <>
      <Link className="dw-link" href={d.href("plans")}>
        <ArrowLeft size={15} />
        返回计划中心
      </Link>
      <Section
        title={plan.name}
        detail={`${platforms[plan.platform].name} · 业务ID ${plan.id} · 平台ID ${plan.nativeId}`}
        action={
          <div className="dw-actions">
            <Status>{planStatusNames[plan.status]}</Status>
            {plan.kind === "CAMPAIGN" && (
              <button className="button primary" onClick={operate}>
                修改配置
              </button>
            )}
            <Link
              className="button secondary"
              href={d.href("decisions", { planId: plan.id })}
            >
              <Robot size={15} />
              AI诊断此对象
            </Link>
          </div>
        }
      >
        <Metrics
          items={
            plan.kind === "CAMPAIGN"
              ? [
                  {
                    name: "广告消耗",
                    value: amount(plan.metrics.spend),
                    note: `${d.scope.start} 至 ${d.scope.end}`,
                  },
                  {
                    name: "支付归因GMV",
                    value: amount(plan.metrics.attributedGmvMinor),
                    note: "广告归因数据，不代表全店经营成交",
                  },
                  {
                    name: "投放ROI",
                    value: plan.metrics.roi?.toFixed(2) ?? "--",
                    note: plan.metrics.attributionWindow ?? "归因口径未提供",
                  },
                ]
              : [
                  {
                    name: "微信豆消耗",
                    value: amount(plan.metrics.spend, "BEAN"),
                    note: "独立资金单位",
                  },
                  {
                    name: "播放 / 互动",
                    value: `${plan.extensions.views ?? "--"} / ${plan.extensions.interactions ?? "--"}`,
                    note: "以加热结果接口为准",
                  },
                  {
                    name: "加热进度",
                    value: plan.progress === null ? "--" : `${plan.progress}%`,
                    note: `${time(plan.startsAt)} 至 ${time(plan.endsAt)}`,
                  },
                ]
          }
        />
        <div className="dw-tabs" role="tablist" aria-label="投放详情">
          {[
            "配置",
            "效果趋势",
            "关联对象",
            "AI诊断",
            "预算历史",
            "执行记录",
          ].map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </div>
        <div role="tabpanel">
          {tab === "配置" && (
            <>
              <div className="dw-facts">
                <div>
                  <span>投放账户</span>
                  <b>
                    {d.data.accounts.find((a) => a.id === plan.accountId)?.name}
                  </b>
                  <small>{plan.accountId}</small>
                </div>
                {plan.kind === "CAMPAIGN" ? (
                  <>
                    <div>
                      <span>日预算 / 出价</span>
                      <b>
                        {amount(plan.budgetMinor)} / {amount(plan.bidMinor)}
                      </b>
                    </div>
                    <div>
                      <span>ROI目标 / 层级</span>
                      <b>
                        {plan.targetRoi ?? "--"} / {plan.level}
                      </b>
                    </div>
                  </>
                ) : (
                  <>
                    <div>
                      <span>加热内容 / 目标</span>
                      <b>
                        {plan.contentId} / {plan.heatingGoal}
                      </b>
                    </div>
                    <div>
                      <span>订单额度</span>
                      <b>{amount(plan.amountBeans, "BEAN")}</b>
                    </div>
                  </>
                )}
                <div>
                  <span>版本 / 平台更新</span>
                  <b>{plan.version ?? "现有快照未提供"}</b>
                  <small>{time(plan.updatedAt)}</small>
                </div>
              </div>
              <Json value={plan.extensions} />
            </>
          )}
          {tab === "效果趋势" && (
            <div className="dw-chart-grid">
              <Trend
                title={
                  plan.platform === "adq"
                    ? "广告消耗与预算（分）"
                    : "加热消耗与额度（微信豆）"
                }
                points={
                  d.data.trends.find((x) => x.planId === plan.id)?.points ?? []
                }
                actual="spend"
                target="budget"
              />
              {plan.kind === "CAMPAIGN" && (
                <>
                  <Trend
                    title="归因GMV趋势（分）"
                    points={
                      d.data.trends.find((x) => x.planId === plan.id)?.points ??
                      []
                    }
                    actual="actualGmvMinor"
                    target="targetGmvMinor"
                  />
                  <Trend
                    title="ROI实际表现与目标线"
                    points={
                      d.data.trends.find((x) => x.planId === plan.id)?.points ??
                      []
                    }
                    actual="roi"
                    target="targetRoi"
                  />
                </>
              )}
            </div>
          )}
          {tab === "关联对象" && (
            <>
              {plan.linkedObjects.map((object) => (
                <div className="dw-list-row" key={object.id}>
                  <span>
                    {object.name}
                    <small>
                      {object.type} · {object.id}
                    </small>
                  </span>
                  {object.type === "LIVE" ? (
                    <Link
                      className="dw-link"
                      href={`/app/monitor?${new URLSearchParams({ shopId: plan.shopId, accountId: plan.accountId, liveSessionId: object.id, platform: plan.platform, planId: plan.id, start: d.scope.start, end: d.scope.end })}`}
                    >
                      进入直播作战室 <ArrowRight size={14} />
                    </Link>
                  ) : (
                    <Link
                      className="dw-link"
                      href={`/app/${object.type === "PRODUCT" ? "products" : "assets"}?${new URLSearchParams({ shopId: plan.shopId, accountId: plan.accountId, [object.type === "PRODUCT" ? "productId" : "materialId"]: object.id, planId: plan.id })}`}
                    >
                      查看关联内容
                    </Link>
                  )}
                </div>
              ))}
              {!plan.linkedObjects.length && (
                <Missing title="暂无已关联的素材、商品或直播场次" />
              )}
            </>
          )}
          {tab === "AI诊断" && (
            <>
              {decisions.map((x) => (
                <Link
                  key={x.id}
                  className="dw-list-row"
                  href={d.href("decisions", {
                    decisionId: x.id,
                    planId: plan.id,
                  })}
                >
                  <span>
                    {x.title}
                    <small>{x.id}</small>
                  </span>
                  <CaretRight size={15} />
                </Link>
              ))}
              {!decisions.length && (
                <Missing title="此投放对象暂无真实AI诊断" />
              )}
            </>
          )}
          {tab === "预算历史" && (
            <>
              {tasks
                .filter((t) => t.action.includes("预算"))
                .map((t) => (
                  <Link
                    key={t.id}
                    className="dw-list-row"
                    href={d.href("executions", { taskId: t.id })}
                  >
                    <span>
                      {time(t.createdAt)} · {t.action}
                      <small>
                        任务 {t.id} · 审批 {t.approvalId ?? "未发起"}
                      </small>
                    </span>
                    <CaretRight size={15} />
                  </Link>
                ))}
              {!tasks.some((t) => t.action.includes("预算")) && (
                <Missing title="暂无预算变更历史" />
              )}
            </>
          )}
          {tab === "执行记录" && (
            <>
              {tasks.map((t) => (
                <Link
                  className="dw-list-row"
                  key={t.id}
                  href={d.href("executions", { taskId: t.id })}
                >
                  <span>
                    {t.action}
                    <small>
                      {t.id} · {time(t.updatedAt)}
                    </small>
                  </span>
                  <CaretRight size={15} />
                </Link>
              ))}
              {!tasks.length && <Missing title="暂无关联执行任务" />}
            </>
          )}
        </div>
      </Section>
      {operation && (
        <Operation key={operation.id} plan={operation} close={closeOperation} />
      )}
    </>
  );
}

const operationMap = {
  budget: "CHANGE_DAILY_BUDGET",
  bid: "CHANGE_BID",
  pause: "PAUSE_AD_PLAN",
  resume: "RESUME_AD_PLAN",
} as const;
function Operation({ plan, close }: { plan: Plan; close: () => void }) {
  const d = useDelivery();
  const { service } = useWorkspace();
  const draft = d.draft as {
    kind?: string;
    scope?: string;
    planId?: string;
    action?: keyof typeof operationMap;
    value?: string;
  } | null;
  const restore =
    draft?.kind === "CHANGE" &&
    draft.planId === plan.id &&
    draft.scope === scopeKey(d.scope);
  const [action, setAction] = useState<keyof typeof operationMap>(
      restore ? draft!.action! : "budget",
    ),
    [value, setValue] = useState(
      restore
        ? draft!.value!
        : plan.kind === "CAMPAIGN"
          ? plan.budgetMinor !== null
            ? String(plan.budgetMinor / 100)
            : ""
          : "",
    );
  const [preview, setPreview] = useState<RiskPreview | null>(null),
    [job, setJob] = useState<CommandJob | null>(null),
    [receipt, setReceipt] = useState<string | null>(null);
  const [key] = useState(() => crypto.randomUUID());
  const capability = actionAvailability(
    d.data,
    d.identity,
    plan.accountId,
    action,
  );
  if (plan.kind !== "CAMPAIGN")
    return (
      <Modal title="加热订单操作" close={close}>
        <Missing
          title="当前没有可用的订单修改接口"
          detail="加热订单不提供 ADQ 出价、暂停或恢复通用操作。请根据平台实际授权处理。"
        />
      </Modal>
    );
  const current =
    action === "budget"
      ? plan.budgetMinor
      : action === "bid"
        ? plan.bidMinor
        : plan.status === "RUNNING";
  const next =
    action === "pause"
      ? false
      : action === "resume"
        ? true
        : Math.round(Number(value) * 100);
  const changed =
    current !== next &&
    (typeof next === "boolean" || (Number.isSafeInteger(next) && next > 0));
  const cap = d.data.capabilities.find((c) => c.accountId === plan.accountId);
  const input: PlanChangeRequest = {
    planId: plan.id,
    accountId: plan.accountId,
    action: operationMap[action],
    newValue: next,
    expectedVersion: plan.version,
    idempotencyKey: key,
  };
  const reset = (newAction: keyof typeof operationMap) => {
    const sourceValue =
      newAction === "bid" ? (plan as Campaign).bidMinor : (plan as Campaign).budgetMinor;
    const nextValue = sourceValue !== null ? String(sourceValue / 100) : "";
    setAction(newAction);
    setValue(nextValue);
    setPreview(null);
    setJob(null);
    d.markDirty(true);
    d.saveDraft({
      kind: "CHANGE",
      scope: scopeKey(d.scope),
      planId: plan.id,
      action: newAction,
      value: nextValue,
    });
  };
  async function preflight() {
    if (!changed) return;
    if (cap?.execution === "COMMAND_JOB")
      await d.mutate(
        plan.accountId,
        action as Action,
        () => d.api.legacyChange(input, plan, d.identity),
        setJob,
      );
    else
      await d.mutate(
        plan.accountId,
        action as Action,
        () => d.api.preview(input),
        setPreview,
      );
  }
  async function confirm() {
    if (job && service)
      await d.mutate(
        plan.accountId,
        action,
        () =>
          service.confirmJob({
            job_id: job.job_id,
            expected_version: job.version,
          }),
        (j) => {
          setJob(j);
          setReceipt(j.job_id);
        },
      );
    else if (preview)
      await d.mutate(
        plan.accountId,
        action,
        () => d.api.changePlan(input, preview, plan),
        (r) => setReceipt(r.executionId ?? r.resourceId),
      );
  }
  return (
    <Modal title={`调整投放配置 · ${plan.name}`} close={() => d.guard(close)}>
      <div className="dw-form">
        {(d.scope.platform !== plan.platform ||
          d.scope.accountId !== plan.accountId) && (
          <Link
            className="button secondary"
            href={deliveryHref(
              "plans",
              {
                ...d.scope,
                platform: plan.platform,
                accountId: plan.accountId,
                shopId: plan.shopId,
              },
              { planId: plan.id },
            )}
          >
            切换至此账户工作区
          </Link>
        )}
        <label>
          修改类型
          <select
            disabled={!!job || !!receipt || d.busy}
            value={action}
            onChange={(e) => reset(e.target.value as keyof typeof operationMap)}
          >
            <option value="budget">修改日预算</option>
            <option value="bid">修改出价</option>
            <option value="pause">暂停计划</option>
            <option value="resume">恢复计划</option>
          </select>
        </label>
        {(action === "budget" || action === "bid") && (
          <label>
            新配置（元）
            <input
              type="number"
              min="0.01"
              step="0.01"
              value={value}
              disabled={!!job || !!receipt || d.busy}
              onChange={(e) => {
                setValue(e.target.value);
                setPreview(null);
                d.markDirty(true);
                d.saveDraft({
                  kind: "CHANGE",
                  scope: scopeKey(d.scope),
                  planId: plan.id,
                  action,
                  value: e.target.value,
                });
              }}
            />
          </label>
        )}
        <div className="dw-comparison">
          <div>
            <span>原配置</span>
            <b>
              {typeof current === "boolean"
                ? current
                  ? "开启"
                  : "暂停"
                : amount(current)}
            </b>
          </div>
          <ArrowRight size={20} />
          <div>
            <span>新配置</span>
            <b>
              {typeof next === "boolean"
                ? next
                  ? "开启"
                  : "暂停"
                : amount(next)}
            </b>
          </div>
        </div>
        <Gate capability={capability} />
        <p className="dw-notice">
          <ShieldCheck size={16} />
          预检、确认、审批、平台执行与回读核验分开记录。前端不直接推进执行成功。
        </p>
        {job && (
          <>
            <h3>后端风险预检 · 规则 v{job.risk_summary.policy_version}</h3>
            {job.risk_summary.checks.map((check) => (
              <p key={check.code}>
                <Status tone={check.passed ? "green" : "red"}>
                  {check.passed ? "通过" : "阻断"}
                </Status>{" "}
                {check.message}
              </p>
            ))}
            <p>
              申请ID {job.job_id} · 当前 {job.status}
            </p>
          </>
        )}
        {preview && (
          <>
            {preview.checks.map((check, i) => (
              <p key={i}>
                {check.passed ? "通过" : "阻断"} · {check.message}
              </p>
            ))}
            <small>预检有效至 {displayTime(preview.expiresAt)}</small>
          </>
        )}
        {receipt ? (
          <Link
            className="button primary"
            href={d.href("executions", { taskId: receipt })}
          >
            查看申请与执行核验 <ArrowRight size={14} />
          </Link>
        ) : !job && !preview ? (
          <button
            className="button primary"
            disabled={
              !changed ||
              capability.status !== "AUTHORIZED" ||
              d.busy ||
              d.uncertain
            }
            onClick={() => void preflight()}
          >
            {d.busy
              ? "预检中…"
              : cap?.execution === "COMMAND_JOB"
                ? "生成待确认申请并预检"
                : "请求服务器风险预检"}
          </button>
        ) : (
          <button
            className="button primary"
            disabled={
              d.busy ||
              d.uncertain ||
              (!!job &&
                (job.status !== "DRAFT" ||
                  job.risk_summary.result === "BLOCKED")) ||
              (!!preview && preview.checks.some((c) => !c.passed))
            }
            onClick={() => void confirm()}
          >
            确认变更并送审
          </button>
        )}
      </div>
    </Modal>
  );
}
