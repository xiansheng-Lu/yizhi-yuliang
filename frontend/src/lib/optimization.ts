/** View identifiers do not grant access to the underlying decision/budget APIs. */
export const optimizationSections = [
  { id: "suggestions", name: "优化建议", description: "查看分析依据，处理调预算、停投和素材优化建议。" },
  { id: "allocation", name: "预算分配", description: "管理账户与计划额度，预览并提交预算分配申请。" },
  { id: "funds", name: "账户资金", description: "查看平台同步的账户资金，区分人民币与微信豆。" },
  { id: "rules", name: "风险规则", description: "核对预算安全边界，设置账户策略与自动化规则。" },
] as const;
export type OptimizationSection = (typeof optimizationSections)[number]["id"];
export function isOptimizationSection(value: string | null): value is OptimizationSection {
  return optimizationSections.some(section => section.id === value);
}
export function legacyBudgetTarget(query: URLSearchParams) {
  const next = new URLSearchParams(query);
  const legacyTabs: Record<string, OptimizationSection> = { "预算分配": "allocation", "账户资金": "funds", "AI预算优化": "suggestions", "风险控制": "rules" };
  const section = next.get("section");
  const tab = next.get("tab") ?? "";
  next.set("section", isOptimizationSection(section) ? section : Object.hasOwn(legacyTabs, tab) ? legacyTabs[tab] : "allocation");
  next.delete("tab");
  return `/app/decisions?${next}`;
}
