"use client";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import type { ReactNode } from "react";
import type { PageId } from "@/lib/routes";
import { AuthGuard, SnapshotView } from "@/components/provider";
import { WorkspaceShell } from "@/components/v12-shell";
import { BusinessPage } from "./business-page";
import { useWorkspace } from "@/components/provider";
import { canAccessPage } from "@/lib/access";
import { EmptyState, LoadingState } from "@/components/ui";
import { AccountCenter } from "./account-center";
import { LiveRoomPage, LiveRoomSkeleton } from "./live-room-page";
import { scopeKey } from "@/lib/live-room";
import { ProductsPage, ProductsSkeleton } from "./products-page";
import { DeliveryWorkspace, isDeliveryPage } from "./delivery-workspace";
import { DeliveryLiveBridge } from "./delivery/live-bridge";
import { PlatformDataPanel } from "./platform-workspace";
import { isContentPage } from "@/lib/content-factory";
import { AnalysisWorkspace } from "./analysis/workspace";
const ContentFactory = dynamic(() => import("./content/factory").then(module => module.ContentFactory), { loading: () => <LoadingState /> });
function AnalysisReadingShell({ children }: { pageId: PageId; children: ReactNode }) { return <main className="ba-reader-frame">{children}</main>; }
export function WorkspaceEntry({ pageId }: { pageId: PageId }) {
  const { identity } = useWorkspace();
  const query = useSearchParams();
  const reading = pageId === "reports" && (query.has("published") || query.has("preview"));
  const Shell = reading ? AnalysisReadingShell : WorkspaceShell;
  return (
    <AuthGuard requireSnapshot={false} loading={pageId === "monitor" ? <LiveRoomSkeleton /> : pageId === "products" ? <ProductsSkeleton /> : undefined}>
      <Shell pageId={pageId}>
        {!canAccessPage(identity, pageId) ? <EmptyState title="此工作区尚未授权" description="请在用户中心查看当前账号可使用的功能。" /> : pageId === "account" ? <AccountCenter key={identity?.userId} /> : isDeliveryPage(pageId) ? <DeliveryWorkspace key={`${identity?.mode}:${identity?.tenantId}:${identity?.userId}`} pageId={pageId} /> : pageId === "products" ? <ProductsPage /> : <SnapshotView loading={pageId === "monitor" ? <LiveRoomSkeleton /> : undefined} retainOnError={pageId === "monitor"}>
          {(snapshot) => pageId === "analytics" || pageId === "reports" ? <AnalysisWorkspace key={`${identity?.mode}:${identity?.tenantId}:${identity?.userId}:${pageId}`} snapshot={snapshot} /> : pageId === "monitor" ? <DeliveryLiveBridge snapshot={snapshot}><LiveRoomPage key={`${identity?.userId}:${snapshot.source}:${scopeKey(snapshot.scope)}`} snapshot={snapshot} /></DeliveryLiveBridge> : isContentPage(pageId) && identity ? <ContentFactory key={`${identity.mode}:${identity.tenantId}:${identity.userId}:${snapshot.scope.shop_id}`} pageId={pageId} snapshot={snapshot} identity={identity} /> : <BusinessPage pageId={pageId} snapshot={snapshot} />}
        </SnapshotView>}
        {!reading&&canAccessPage(identity,pageId)&&!isContentPage(pageId)&&["overview","monitor","products","planning","scripts","videos","graphics","editing","assets","experiments","analytics","reports"].includes(pageId)&&<PlatformDataPanel pageId={pageId}/>}
      </Shell>
    </AuthGuard>
  );
}
