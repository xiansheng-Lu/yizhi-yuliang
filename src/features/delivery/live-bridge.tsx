"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useWorkspace } from "@/components/provider";
import type { FrontendSnapshot } from "@/lib/domain";
import { deliveryHref, isPlatform, validDate, type DeliveryScope } from "@/lib/delivery";
import { invalidateSnapshotRead } from "@/services/snapshot-reader";
import { scopedPlans } from "@/lib/reporting";
/** Only scope navigation is added. The existing live workbench remains intact. */
export function DeliveryLiveBridge({
  snapshot,
  children,
}: {
  snapshot: FrontendSnapshot;
  children: ReactNode;
}) {
  const query = useSearchParams(),
    { service, reload, identity } = useWorkspace();
  const [error, setError] = useState("");
  const applied = useRef<string | null>(null);
  const shopId = query.get("shopId"),
    liveId = query.get("liveSessionId"),
    platform = query.get("platform"),
    accountId = query.get("accountId");
  const room = snapshot.liveSessions.find(
    (s) =>
      s.id === liveId &&
      s.shopId === shopId &&
      s.tenantId === identity?.tenantId,
  );
  const account = accountId
    ? snapshot.adAccounts.find(
        (a) =>
          a.id === accountId &&
          a.shopId === shopId &&
          a.tenantId === identity?.tenantId &&
          a.connectorId === platform,
      )
    : null;
  const valid =
    !!room &&
    !!shopId &&
    !!identity?.shopIds.includes(shopId) &&
    (!accountId || !!account) &&
    (!platform || isPlatform(platform));
  const requestedDate = validDate(query.get("end")) ? query.get("end")! : snapshot.scope.date;
  const mismatch =
    valid &&
    (snapshot.scope.date !== requestedDate || snapshot.scope.shop_id !== shopId ||
      snapshot.scope.live_session_id !== liveId ||
      (platform && snapshot.scope.connector_id !== platform));
  const requestKey = `${shopId}:${liveId}:${platform}:${accountId}:${requestedDate}`;
  useEffect(() => {
    if (!mismatch || !service || applied.current === requestKey) return;
    applied.current = requestKey;
    let active = true;
    void Promise.resolve().then(async () => {
      try {
        window.dispatchEvent(new Event("yuliang:scope-changing"));
        invalidateSnapshotRead(service);
        await service.setScope({
          ...snapshot.scope,
          shop_id: shopId!,
          live_session_id: liveId!,
          connector_id: platform && isPlatform(platform) ? platform : "all",
          date: requestedDate,
        });
        await reload();
      } catch (e) {
        if (active)
          setError(e instanceof Error ? e.message : "直播上下文切换失败");
      } finally {
        window.dispatchEvent(new Event("yuliang:scope-settled"));
      }
    });
    return () => {
      active = false;
    };
  }, [
    mismatch,
    service,
    requestKey,
    snapshot.scope,
    shopId,
    liveId,
    platform,
    reload,
    requestedDate,
  ]);
  if ((shopId || liveId) && !valid)
    return (
      <div className="notice" role="alert">
        链接中的店铺、账户或直播场次无效，未切换经营范围。
        <Link href="/app/monitor">返回直播工作台</Link>
      </div>
    );
  if (mismatch)
    return (
      <div className="notice" role="status">
        {error || "正在校验并切换关联直播上下文…"}
        {error && (
          <button
            onClick={() => {
              applied.current = null;
              void reload();
            }}
          >
            重新加载
          </button>
        )}
      </div>
    );
  const plans = scopedPlans(snapshot).filter((p) =>
    snapshot.adAccounts.some(
      (a) => a.id === p.adAccountId && isPlatform(a.connectorId),
    ),
  );
  return (
    <>
      {children}
      {plans.length > 0 && (
        <section className="panel">
          <h2>投放与AI决策联动</h2>
          {plans.map((p) => {
            const a = snapshot.adAccounts.find((x) => x.id === p.adAccountId)!;
            const scope: DeliveryScope = {
              platform: isPlatform(a.connectorId) ? a.connectorId : "all",
              shopId: p.shopId,
              accountId: p.adAccountId,
              start: snapshot.scope.date,
              end: snapshot.scope.date,
              liveSessionId: p.liveSessionId,
            };
            return (
              <div className="list-row" key={p.id}>
                <span>{p.name}</span>
                <Link
                  className="text-link"
                  href={deliveryHref("plans", scope, { planId: p.id })}
                >
                  计划详情
                </Link>
                <Link
                  className="text-link"
                  href={deliveryHref("decisions", scope, { planId: p.id })}
                >
                  AI诊断
                </Link>
              </div>
            );
          })}
        </section>
      )}
    </>
  );
}
