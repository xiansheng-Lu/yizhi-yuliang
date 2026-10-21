import { notFound } from "next/navigation";
import { isPageId } from "@/lib/routes";
import { WorkspaceEntry } from "@/features/workspace-entry";
export default async function BusinessRoute({
  params,
}: {
  params: Promise<{ pageId: string }>;
}) {
  const { pageId } = await params;
  if (!isPageId(pageId)) notFound();
  return <WorkspaceEntry pageId={pageId} />;
}
