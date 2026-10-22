"use client";
import type { PageId } from "@/lib/routes";
import { AuthGuard, SnapshotView } from "@/components/provider";
import { WorkspaceShell } from "@/components/v12-shell";
import { BusinessPage } from "./business-page";
import { useWorkspace } from "@/components/provider";
import { canAccessPage } from "@/lib/access";
import { EmptyState } from "@/components/ui";
import { AccountCenter } from "./account-center";
import { LiveRoomPage, LiveRoomSkeleton } from "./live-room-page";
import { scopeKey } from "@/lib/live-room";
import { ProductsPage, ProductsSkeleton } from "./products-page";
import { DeliveryWorkspace, isDeliveryPage } from "./delivery-workspace";
import { DeliveryLiveBridge } from "./delivery/live-bridge";
export function WorkspaceEntry({ pageId }: { pageId: PageId }) {
  const { identity } = useWorkspace();
  return (
    <AuthGuard requireSnapshot={false} loading={pageId === "monitor" ? <LiveRoomSkeleton /> : pageId === "products" ? <ProductsSkeleton /> : undefined}>
      <WorkspaceShell pageId={pageId}>
        {!canAccessPage(identity, pageId) ? <EmptyState title="此工作区尚未授权" description="请在用户中心查看当前账号可使用的功能。" /> : pageId === "account" ? <AccountCenter key={identity?.userId} /> : isDeliveryPage(pageId) ? <DeliveryWorkspace key={`${identity?.mode}:${identity?.tenantId}:${identity?.userId}`} pageId={pageId} /> : pageId === "products" ? <ProductsPage /> : <SnapshotView loading={pageId === "monitor" ? <LiveRoomSkeleton /> : undefined} retainOnError={pageId === "monitor"}>
          {(snapshot) => pageId === "monitor" ? <DeliveryLiveBridge snapshot={snapshot}><LiveRoomPage key={`${identity?.userId}:${snapshot.source}:${scopeKey(snapshot.scope)}`} snapshot={snapshot} /></DeliveryLiveBridge> : <BusinessPage pageId={pageId} snapshot={snapshot} />}
        </SnapshotView>}
      </WorkspaceShell>
    </AuthGuard>
  );
}
