import type { Identity } from "./domain";
import { customerPageIds, isPageId, type PageId } from "./routes";
import { type OptimizationSection } from "./optimization";

export function hasWorkspaceGrant(identity: Identity | null, pageId: PageId) {
  if (!identity) return false;
  if (pageId === "account") return true;
  if (!customerPageIds.includes(pageId)) return false;
  return identity.mode === "MOCK"
    ? !identity.workspacePages || identity.workspacePages.includes(pageId)
    : Boolean(identity.workspacePages?.includes(pageId));
}

export function canAccessPage(identity: Identity | null, pageId: PageId) {
  if (pageId === "reports" || pageId === "analytics") return hasWorkspaceGrant(identity, "reports") || hasWorkspaceGrant(identity, "analytics");
  if (pageId === "planning") return hasWorkspaceGrant(identity, "planning") || hasWorkspaceGrant(identity, "scripts");
  // The unified shell is reachable through either original grant; each panel
  // continues to check its own grant and all writes retain backend permissions.
  return pageId === "decisions"
    ? hasWorkspaceGrant(identity, "decisions") || hasWorkspaceGrant(identity, "budgets")
    : hasWorkspaceGrant(identity, pageId);
}

export function canAccessOptimizationSection(identity: Identity | null, section: OptimizationSection) {
  return section === "suggestions" ? hasWorkspaceGrant(identity, "decisions")
    : section === "rules" ? canAccessPage(identity, "decisions")
    : hasWorkspaceGrant(identity, "budgets");
}

export function authorizedLoginTarget(identity: Identity, target: string) {
  if (target === "/mobile") return canAccessPage(identity, "command") ? target : "/app/account";
  const pageId = target.split("?")[0].replace(/^\/app\//, "");
  return isPageId(pageId) && canAccessPage(identity, pageId) ? target : "/app/account";
}
