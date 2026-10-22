"use client";
import type { PageId } from "@/lib/routes";
import { AuthGuard, SnapshotView } from "@/components/provider";
import { WorkspaceShell } from "@/components/v12-shell";
import { BusinessPage } from "./business-page";
import { useWorkspace } from "@/components/provider";
import { canAccessPage } from "@/lib/access";
import { EmptyState } from "@/components/ui";
import { AccountCenter } from "./account-center";
export function WorkspaceEntry({ pageId }: { pageId: PageId }) {
  const { identity } = useWorkspace();
  return (
    <AuthGuard requireSnapshot={false}>
      <WorkspaceShell pageId={pageId}>
        {!canAccessPage(identity, pageId) ? <EmptyState title="此工作区尚未授权" description="请在用户中心查看当前账号可使用的功能。" /> : pageId === "account" ? <AccountCenter key={identity?.userId} /> : <SnapshotView>
          {(snapshot) => <BusinessPage pageId={pageId} snapshot={snapshot} />}
        </SnapshotView>}
      </WorkspaceShell>
    </AuthGuard>
  );
}
