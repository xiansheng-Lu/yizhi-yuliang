export const workspaces = [
  {
    name: "经营总控",
    pages: [
      ["overview", "经营概况"],
      ["goals", "经营目标"],
      ["monitor", "AI直播作战室"],
      ["tasks", "AI任务与审批"],
    ],
  },
  {
    name: "内容中心",
    pages: [
      ["planning", "内容策划"],
      ["scripts", "内容策划"],
      ["videos", "视频创作"],
      ["graphics", "平面创作"],
      ["editing", "智能剪辑"],
      ["assets", "素材库"],
      ["experiments", "素材库"],
    ],
  },
  {
    name: "投放中心",
    pages: [
      ["plans", "计划中心"],
      ["decisions", "投放优化"],
      ["executions", "执行记录"],
      ["products", "商品与货盘"],
      ["budgets", "投放优化"],
    ],
  },
  {
    name: "经营与系统中心",
    pages: [
      ["analytics", "经营分析"],
      ["reports", "完整复盘"],
      ["agents", "AI岗位管理"],
      ["connections", "平台连接"],
      ["settings", "企业和权限设置"],
    ],
  },
] as const;
export type LegacyPageId = (typeof workspaces)[number]["pages"][number][0];
export type PageId = LegacyPageId | "command" | "account";
export const pages = workspaces.flatMap<readonly [LegacyPageId, string]>(
  (group) => group.pages,
);
export function isPageId(value: string): value is PageId {
  return value === "command" || value === "account" || pages.some(([id]) => id === value);
}
export function pageName(id: PageId) {
  if (id === "command") return "AI驭量中枢";
  if (id === "account") return "用户中心";
  return pages.find(([value]) => value === id)![1];
}
export function legacyContentTarget(pageId: string, query = new URLSearchParams()): string | null {
  const target = pageId === "scripts" ? "planning" : pageId === "experiments" ? "assets" : null;
  if (!target) return null;
  const search = query.toString();
  return `/app/${target}${search ? `?${search}` : ""}`;
}
export function loginTarget(raw: string | null): string {
  if (raw === "mobile" || raw === "/mobile") return "/mobile";
  // Frozen homepage uses this bare legacy target for its default workbench CTA.
  // Explicit /app/overview and legacy hash deep links still open the overview.
  if (raw === "overview") return "/app/command";
  const value = raw?.replace(/^\/app\//, "").replace(/^#\/?/, "");
  if (value === "campaigns") return "/app/plans";
  if (value === "budgets") return "/app/decisions?section=allocation";
  const contentTarget = legacyContentTarget(value ?? "");
  if (contentTarget) return contentTarget;
  return value && isPageId(value) ? `/app/${value}` : "/app/command";
}

/** V12 navigation. The original registry above preserves all audited IDs. */
export const navigationGroups: { name: string; pages: readonly (readonly [PageId, string])[] }[] = [
  { name: "AI驭量中枢", pages: [["command", pageName("command")]] },
  { name: "经营概况", pages: [["overview", pageName("overview")]] },
  { name: "直播运营", pages: [["monitor", "AI直播作战室"], ["products", "商品与货盘"]] },
  { name: "投放管理", pages: [["goals", "经营目标"], ["plans", "计划中心"], ["decisions", "投放优化"], ["executions", "执行记录"]] },
  { name: "内容中心", pages: [["planning", "内容策划"], ["videos", "视频创作"], ["graphics", "平面创作"], ["editing", "智能剪辑"], ["assets", "素材库"]] },
  { name: "数据复盘", pages: [["analytics", "经营分析"], ["reports", "完整复盘"]] },
];

export const customerPageIds: readonly PageId[] = [
  ...navigationGroups.flatMap((group) => group.pages.map(([id]) => id)),
  "tasks",
  "budgets", // Legacy grant remains valid for the budget panels.
  "scripts", // Existing script grants open the unified planning workspace.
  "experiments", // Accept legacy identity grants without exposing the removed entry.
];
