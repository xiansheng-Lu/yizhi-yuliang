import { notFound, redirect } from "next/navigation";
import { isPageId } from "@/lib/routes";
import { legacyBudgetTarget } from "@/lib/optimization";
import { WorkspaceEntry } from "@/features/workspace-entry";
export default async function BusinessRoute({
  params,
  searchParams,
}: {
  params: Promise<{ pageId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { pageId } = await params;
  if (!isPageId(pageId)) notFound();
  if (pageId === "budgets") {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(await searchParams)) {
      if (Array.isArray(value)) value.forEach(item => query.append(key, item));
      else if (value !== undefined) query.set(key, value);
    }
    redirect(legacyBudgetTarget(query));
  }
  return <WorkspaceEntry pageId={pageId} />;
}
