import type { FrontendSnapshot, AdPlan } from "./domain";
import type { ReportingScope, CommandJob } from "./command-job";
export function scopedPlans(
  snapshot: FrontendSnapshot,
  scope = snapshot.scope,
): AdPlan[] {
  return snapshot.plans.filter(
    (plan) =>
      (scope.shop_id === "all" || plan.shopId === scope.shop_id) &&
      (scope.live_session_id === "all" ||
        plan.liveSessionId === scope.live_session_id) &&
      (scope.connector_id === "all" ||
        snapshot.adAccounts.find((account) => account.id === plan.adAccountId)
          ?.connectorId === scope.connector_id),
  );
}
export function scopedJobs(snapshot: FrontendSnapshot): CommandJob[] {
  const scope = snapshot.scope;
  return snapshot.tasks.filter(
    (job) =>
      (scope.shop_id === "all" || job.shop_id === scope.shop_id) &&
      (scope.live_session_id === "all" ||
        job.live_session_id === scope.live_session_id) &&
      (scope.connector_id === "all" ||
        snapshot.adAccounts.find((account) => account.id === job.account_id)
          ?.connectorId === scope.connector_id),
  );
}
export function normalizeScope(
  snapshot: FrontendSnapshot,
  scope: ReportingScope,
): ReportingScope {
  const live = snapshot.liveSessions.find(
    (live) => live.id === scope.live_session_id,
  );
  return {
    ...scope,
    live_session_id:
      live && (scope.shop_id === "all" || live.shopId === scope.shop_id)
        ? live.id
        : "all",
    timezone: "Asia/Shanghai",
  };
}
export function scopedEvidence(snapshot: FrontendSnapshot): FrontendSnapshot {
  const scope = snapshot.scope;
  const liveIds = new Set(
    snapshot.liveSessions
      .filter(
        (live) =>
          (scope.shop_id === "all" || live.shopId === scope.shop_id) &&
          (scope.live_session_id === "all" ||
            live.id === scope.live_session_id),
      )
      .map((live) => live.id),
  );
  return {
    ...snapshot,
    products: snapshot.products.filter(
      (product) => scope.shop_id === "all" || product.shopId === scope.shop_id,
    ),
    replaySegments: snapshot.replaySegments.filter((segment) =>
      liveIds.has(segment.liveSessionId),
    ),
    replayReports: snapshot.replayReports.filter((report) =>
      liveIds.has(report.liveSessionId),
    ),
  };
}
