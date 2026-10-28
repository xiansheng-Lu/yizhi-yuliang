/** Frontend-only deterministic demo responses. Not an AI or production risk engine. */
import type { FrontendSnapshot, RiskPolicy, AdPlan } from "@/lib/domain";
import {
  currentPlanValue,
  isHandoff,
  parseMoneyInput,
  type CommandInterpretation,
  type CommandJob,
  type CreateJobRequest,
  type ReportingScope,
  type RiskSummary,
} from "@/lib/command-job";
import { scopedPlans } from "@/lib/reporting";

export function interpretMockCommand(
  text: string,
  snapshot: FrontendSnapshot,
  scope: ReportingScope,
): CommandInterpretation {
  const normalized = text.trim();
  const rooms = snapshot.liveSessions.filter(
    (live) =>
      (scope.shop_id === "all" || live.shopId === scope.shop_id) &&
      (scope.live_session_id === "all" || live.id === scope.live_session_id),
  );
  if (/关播|结束直播|停止直播|开始直播|开播/.test(normalized))
    return {
      kind: "ACTION",
      text: "真实直播开关仅建立负责人交接任务，不会控制推流。",
      action_type: /关播|结束直播|停止直播/.test(normalized)
        ? "REQUEST_LIVE_END"
        : "REQUEST_LIVE_START",
      new_value: {
        kind: "HANDOFF",
        value: /关播|结束直播|停止直播/.test(normalized) ? "END" : "START",
      },
      target_type: "LIVE_SESSION",
      candidate_ids: rooms.map((live) => live.id),
    };
  const roi = /roi\s*目标|目标\s*roi/i.test(normalized),
    budget = /预算/.test(normalized),
    bid = /出价|竞价/.test(normalized);
  const pause = /暂停|停投|关闭.*广告/.test(normalized),
    resume = /恢复|启用|开启.*广告/.test(normalized);
  if ([roi, budget, bid, pause, resume].filter(Boolean).length > 1)
    return {
      kind: "UNRECOGNIZED",
      text: "一条指令只能包含一个明确动作，请分别提交预算、出价、ROI或启停申请。",
    };
  if (!roi && !budget && !bid && !pause && !resume)
    return /直播|情况|现在|异常|数据/.test(normalized)
      ? {
          kind: "QUERY",
          text:
            rooms.length === 1
              ? "已读取当前场次 Mock 数据；更新时间和统计范围见下方。"
              : "请先明确店铺与直播间，再查看当前场次。",
          live_session_id: rooms.length === 1 ? rooms[0].id : null,
        }
      : {
          kind: "UNRECOGNIZED",
          text: "Mock 解析支持：看看现在直播间情况、把千川A计划日预算调到5000、ADQ出价调到3.2、ROI目标调到6、暂停广告、帮我关播。真实AI解析由后端提供。",
        };
  const action = roi
    ? "SET_ROI_TARGET"
    : budget
      ? "CHANGE_DAILY_BUDGET"
      : bid
        ? "CHANGE_BID"
        : pause
          ? "PAUSE_AD_PLAN"
          : "RESUME_AD_PLAN";
  const amount = normalized
    .match(
      /(?:调整到|调整为|调到|改为|改到|设为|设置为|提高到|降低到|到|至)\s*(-?[\d,]+(?:\.\d+)?)/,
    )?.[1]
    ?.replaceAll(",", "");
  if ((roi || budget || bid) && !amount)
    return {
      kind: "UNRECOGNIZED",
      text: "请提供明确目标值，例如“把千川A计划日预算调到5000”。",
    };
  let value: CreateJobRequest["new_value"];
  try {
    value = roi
      ? { kind: "RATIO", value: Number(amount) }
      : budget || bid
        ? {
            kind: "MONEY",
            currency: "CNY",
            amount_minor: parseMoneyInput(amount!),
          }
        : { kind: "BOOLEAN", value: resume };
    if (
      value.kind === "RATIO" &&
      (!Number.isFinite(value.value) || value.value <= 0)
    )
      throw new Error("ROI目标必须为正数。");
  } catch (error) {
    return {
      kind: "UNRECOGNIZED",
      text: error instanceof Error ? error.message : "目标值无效。",
    };
  }
  let candidates = scopedPlans(snapshot, scope);
  const connector = /千川|抖音/.test(normalized)
    ? "qianchuan"
    : /ADQ|腾讯广告/i.test(normalized)
      ? "adq"
      : /微信豆/.test(normalized)
        ? "beans"
        : /微信小店推广|小店推广/.test(normalized)
          ? "wxpromo"
          : /聚光|小红书/.test(normalized)
            ? "juguang"
            : null;
  if (connector)
    candidates = candidates.filter(
      (plan) =>
        snapshot.adAccounts.find((account) => account.id === plan.adAccountId)
          ?.connectorId === connector,
    );
  const named = candidates.filter((plan) =>
    [plan.name, ...(plan.aliases ?? [])].some((alias) =>
      normalized.toLowerCase().includes(alias.toLowerCase()),
    ),
  );
  if (named.length) candidates = named;
  return {
    kind: "ACTION",
    text:
      candidates.length === 1
        ? "Mock 已识别一个对象，请检查账户、旧值、新值及风险后确认。"
        : candidates.length
          ? "存在多个可操作计划，必须明确选择，系统不会猜测。"
          : "当前范围没有匹配计划，请调整店铺、场次或平台筛选。",
    action_type: action,
    new_value: value,
    target_type: "AD_PLAN",
    candidate_ids: candidates.map((plan) => plan.id),
  };
}

export function mockRisk(
  request: Pick<
    CreateJobRequest,
    "action_type" | "new_value" | "target_id" | "shop_id"
  >,
  snapshot: FrontendSnapshot,
  plan: AdPlan | undefined,
  policy: RiskPolicy,
  at: string,
): RiskSummary {
  if (isHandoff(request.action_type))
    return {
      source: "MOCK",
      result: "MANUAL_ONLY",
      level: "HIGH",
      policy_version: policy.version,
      checked_at: at,
      checks: [
        {
          code: "LIVE_MANUAL",
          passed: false,
          message: "未核验推流控制授权，仅创建人工交接任务。",
        },
      ],
    };
  const checks = [
    {
      code: "OBJECT",
      passed: Boolean(plan && plan.shopId === request.shop_id),
      message: "核对企业、店铺、账户及计划（Mock预检）",
    },
  ];
  if (
    request.action_type === "CHANGE_DAILY_BUDGET" &&
    request.new_value.kind === "MONEY"
  ) {
    const amountMinor = request.new_value.amount_minor;
    const projected = snapshot.plans
      .filter((item) => item.tenantId === policy.tenantId)
      .reduce(
        (sum, item) =>
          sum + (item.id === plan?.id ? amountMinor : item.budgetMinor),
        0,
      );
    checks.push({
      code: "BUDGET_LIMIT",
      passed: projected <= policy.dailyBudgetMinor,
      message: `调整后企业计划预算合计 ${(projected / 100).toFixed(2)} 元 / 上限 ${(policy.dailyBudgetMinor / 100).toFixed(2)} 元。正式额度须由后端检查。`,
    });
  }
  if (
    plan &&
    (request.action_type === "CHANGE_DAILY_BUDGET" ||
      request.action_type === "CHANGE_BID") &&
    request.new_value.kind === "MONEY"
  ) {
    const previous =
      request.action_type === "CHANGE_DAILY_BUDGET"
        ? plan.budgetMinor
        : plan.bidMinor;
    const percent =
      previous > 0
        ? (Math.abs(request.new_value.amount_minor - previous) / previous) * 100
        : Infinity;
    checks.push({
      code: "CHANGE_LIMIT",
      passed: percent <= policy.maxChangePercent,
      message: `本次变动 ${Number.isFinite(percent) ? percent.toFixed(1) : "未知"}% / 允许 ${policy.maxChangePercent}%。`,
    });
  }
  const last = snapshot.tasks.find(
    (job) => job.target_id === request.target_id && job.status === "SUCCEEDED",
  );
  const cooldown =
    !last ||
    Date.parse(at) - Date.parse(last.updated_at) >=
      policy.cooldownMinutes * 60000;
  checks.push({
    code: "COOLDOWN",
    passed: cooldown,
    message: `同计划动作冷却 ${policy.cooldownMinutes} 分钟（Mock演示）。`,
  });
  const blocked = checks.some((check) => !check.passed);
  return {
    source: "MOCK",
    result: blocked ? "BLOCKED" : "REVIEW_REQUIRED",
    level: blocked ? "HIGH" : "MEDIUM",
    checks,
    policy_version: policy.version,
    checked_at: at,
  };
}
export function createMockJob(
  input: CreateJobRequest,
  snapshot: FrontendSnapshot,
  at: string,
  scenario: CommandJob["mock_scenario"] = "SUCCESS",
): CommandJob {
  const plan = snapshot.plans.find((plan) => plan.id === input.target_id);
  const room = snapshot.liveSessions.find(
    (live) => live.id === input.target_id,
  );
  const risk = mockRisk(input, snapshot, plan, snapshot.riskPolicies[0], at);
  const status = risk.result === "BLOCKED" ? "BLOCKED" : "DRAFT";
  return {
    schema_version: "1.0",
    job_id: crypto.randomUUID(),
    source: input.source,
    tenant_id: plan?.tenantId ?? room!.tenantId,
    shop_id: input.shop_id,
    account_id: plan?.adAccountId ?? null,
    live_session_id:
      input.target_type === "LIVE_SESSION"
        ? room!.id
        : (plan!.liveSessionId ?? null),
    target_type: input.target_type,
    target_id: input.target_id,
    action_type: input.action_type,
    old_value: plan ? currentPlanValue(plan, input.action_type) : null,
    new_value: input.new_value,
    intent: input.intent.trim(),
    status,
    created_at: at,
    updated_at: at,
    expires_at: new Date(Date.parse(at) + 15 * 60000).toISOString(),
    risk_summary: risk,
    approval: {
      state: "NOT_REQUESTED",
      actor_id: null,
      decided_at: null,
      method: null,
      approved_version: null,
    },
    execution_receipt: null,
    error:
      status === "BLOCKED"
        ? {
            code: risk.checks.find((check) => !check.passed)!.code,
            message: "Mock 预检阻断；正式风控仍由后端负责，未获得真实授权。",
            retryable: false,
            at,
          }
        : null,
    version: 1,
    idempotency_key: input.idempotency_key,
    mode: "MOCK",
    history: [
      { status: "DRAFT", at, detail: "创建前端 Mock command_job" },
      ...(status === "BLOCKED"
        ? [
            {
              status: "BLOCKED" as const,
              at,
              detail: "Mock预检阻断，不会提交平台",
            },
          ]
        : []),
    ],
    mock_scenario: scenario,
  };
}
