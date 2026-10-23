"use client";
import Link from "next/link";
import { ArrowRight } from "@phosphor-icons/react";
import { canAccessOptimizationSection, hasWorkspaceGrant } from "@/lib/access";
import { isOptimizationSection, optimizationSections, type OptimizationSection } from "@/lib/optimization";
import type { AdvertisingProduct } from "@/lib/advertising";
import { useDelivery } from "./context";
import { Decisions, DecisionPolicyPanel } from "./decisions";
import { Budgets } from "./budgets";
import { Missing } from "./ui";
import { PlatformWorkspace, PlatformDataPanel } from "../platform-workspace";
import { ProductScopeSelector } from "../advertising/product-selector";

export function OptimizationWorkspace({ product }: { product?: AdvertisingProduct }) {
  const d = useDelivery();
  const requested = d.query.get("section");
  const section: OptimizationSection = isOptimizationSection(requested) ? requested
    : optimizationSections.find(item => canAccessOptimizationSection(d.identity, item.id))?.id ?? "suggestions";
  const current = optimizationSections.find(item => item.id === section)!;
  const allowed = canAccessOptimizationSection(d.identity, section);
  function select(next: OptimizationSection) {
    if (!canAccessOptimizationSection(d.identity, next)) return;
    const query = new URLSearchParams(d.query);
    query.set("section", next);
    d.navigate(`/app/decisions?${query}`);
  }
  function changeProduct(next: AdvertisingProduct | "all") {
    const query = new URLSearchParams(d.query);
    for (const key of ["entityId", "productAccountId", "productShopId", "planId", "decisionId", "budgetId"]) query.delete(key);
    query.set("accountId", "all");
    query.set("platform", next === "adq" || next === "beans" ? next : "all");
    if (next === "all") query.delete("product"); else query.set("product", next);
    d.navigate(`/app/decisions?${query}`);
  }
  return <section className="dw-optimization" aria-label="投放优化工作台">
    <div className="dw-optimization-intro">
      <div><strong>从优化建议到预算配置</strong><p>在同一经营范围内分析与配置，审批和执行结果统一关联到执行记录。</p></div>
      <Link className="button secondary" href={d.href("executions")}>查看执行记录 <ArrowRight size={15}/></Link>
    </div>
    <div className="dw-tabs dw-optimization-tabs" role="tablist" aria-label="投放优化分区">
      {optimizationSections.map(item => <button key={item.id} id={`optimization-tab-${item.id}`} role="tab"
        aria-selected={section === item.id} aria-controls="optimization-panel"
        disabled={!canAccessOptimizationSection(d.identity, item.id)}
        title={!canAccessOptimizationSection(d.identity, item.id) ? "此分区需要原有工作区授权" : item.description}
        onClick={() => select(item.id)}
        onKeyDown={event => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const tabs = Array.from(event.currentTarget.parentElement!.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
          const index = tabs.indexOf(event.currentTarget);
          const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
          tabs[next]?.focus(); tabs[next]?.click();
        }}>{item.name}</button>)}
    </div>
    <div id="optimization-panel" role="tabpanel" aria-labelledby={`optimization-tab-${section}`}>
      <p className="dw-optimization-description">{current.description}</p>
      {allowed && product && section === "rules" && <div className="dw-optimization-product"><ProductScopeSelector value={product} onChange={changeProduct}/></div>}
      {!allowed ? <Missing title="此分区尚未授权" detail="合并入口保留原有AI决策、预算工作区授权；请在用户中心核对权限。"/>
        : product ? section === "rules"
          ? <Missing title="此产品线的策略规则接口待接入" detail="不同产品线不共享自动化权限或预算边界。待后端返回该产品线的规则与授权后开放配置。"/>
          : <PlatformWorkspace key={`${product}:${section}`} pageId={section === "suggestions" ? "decisions" : "budgets"} initialProduct={product}
              resourceTypes={section === "funds" ? ["funds"] : section === "allocation" ? ["allocations"] : undefined}/>
        : section === "suggestions" ? <Decisions/>
        : section === "rules" ? <>
          {hasWorkspaceGrant(d.identity, "decisions") && <DecisionPolicyPanel/>}
          {hasWorkspaceGrant(d.identity, "budgets") && <Budgets section="rules"/>}
        </>
        : <Budgets key={section} section={section}/>}
    </div>
    {allowed && !product && <PlatformDataPanel pageId={section === "allocation" || section === "funds" || (section === "rules" && !hasWorkspaceGrant(d.identity, "decisions")) ? "budgets" : "decisions"}/>}
  </section>;
}
