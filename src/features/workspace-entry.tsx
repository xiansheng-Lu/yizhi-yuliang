"use client";
import type { PageId } from "@/lib/routes";
import { AuthGuard, SnapshotView } from "@/components/provider";
import { WorkspaceShell } from "@/components/workspace-shell";
import { BusinessPage } from "./business-page";
export function WorkspaceEntry({ pageId }: { pageId: PageId }) {
  return (
    <AuthGuard>
      <WorkspaceShell pageId={pageId}>
        <SnapshotView>
          {(snapshot) => <BusinessPage pageId={pageId} snapshot={snapshot} />}
        </SnapshotView>
      </WorkspaceShell>
    </AuthGuard>
  );
}
