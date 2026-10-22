import type { Identity } from "./domain";
import { customerPageIds, isPageId, type PageId } from "./routes";

export function canAccessPage(identity: Identity | null, pageId: PageId) {
  if (!identity) return false;
  if (pageId === "account") return true;
  if (!customerPageIds.includes(pageId)) return false;
  return identity.mode === "MOCK"
    ? !identity.workspacePages || identity.workspacePages.includes(pageId)
    : Boolean(identity.workspacePages?.includes(pageId));
}

export function authorizedLoginTarget(identity: Identity, target: string) {
  if (target === "/mobile") return canAccessPage(identity, "command") ? target : "/app/account";
  const pageId = target.replace(/^\/app\//, "");
  return isPageId(pageId) && canAccessPage(identity, pageId) ? target : "/app/account";
}
