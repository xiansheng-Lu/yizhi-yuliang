export const workspaces = [
  {
    name: "经营总控",
    pages: [
      ["overview", "AI总控台"],
      ["goals", "经营目标"],
      ["monitor", "AI直播作战室"],
      ["tasks", "AI任务与审批"],
    ],
  },
  {
    name: "内容中心",
    pages: [
      ["planning", "内容策划"],
      ["scripts", "脚本工厂"],
      ["videos", "视频工厂"],
      ["graphics", "图片图文"],
      ["editing", "智能剪辑"],
      ["assets", "素材资产库"],
      ["experiments", "素材测试"],
    ],
  },
  {
    name: "投放中心",
    pages: [
      ["plans", "投放计划"],
      ["decisions", "AI投手决策"],
      ["executions", "执行记录"],
      ["products", "商品与货盘"],
      ["budgets", "跨平台预算"],
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
export type PageId = (typeof workspaces)[number]["pages"][number][0];
export const pages = workspaces.flatMap<readonly [PageId, string]>(
  (group) => group.pages,
);
export function isPageId(value: string): value is PageId {
  return pages.some(([id]) => id === value);
}
export function pageName(id: PageId) {
  return pages.find(([value]) => value === id)![1];
}
export function loginTarget(raw: string | null): string {
  if (raw === "mobile" || raw === "/mobile") return "/mobile";
  const value = raw?.replace(/^\/app\//, "").replace(/^#\/?/, "");
  if (value === "campaigns") return "/app/plans";
  return value && isPageId(value) ? `/app/${value}` : "/app/overview";
}
