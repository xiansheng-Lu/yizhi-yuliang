"use client";
import { useEffect, useRef, type ReactNode } from "react";
import { WarningCircle, X } from "@phosphor-icons/react";
import { dimensions, type AnalysisQuery } from "@/lib/business-analysis";
import type { FrontendSnapshot } from "@/lib/domain";
import { shiftDate } from "@/lib/reporting-date";
export function AnalysisModal({ title, children, close }: { title: string; children: ReactNode; close: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const previous = document.activeElement as HTMLElement | null; ref.current?.showModal(); return () => { previous?.focus(); }; }, []);
  return <dialog ref={ref} className="ba-modal" aria-label={title} onCancel={event => { event.preventDefault(); close(); }}><header><h2>{title}</h2><button className="icon-button" aria-label="关闭窗口" onClick={close}><X size={18} /></button></header>{children}</dialog>;
}
export function ErrorNotice({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="ba-error" role="alert"><WarningCircle size={18} /><span>{message}</span>{retry && <button onClick={retry}>重试</button>}</div>;
}
export function analysisSelectionHref(query: AnalysisQuery) {
  return `/app/reports?${new URLSearchParams({ shop: query.shopId, platform: query.platformId, start: query.startDate, end: query.endDate, dimensions: query.dimensions.join(",") })}`;
}
export function initialAnalysisQuery(params: URLSearchParams, snapshot: FrontendSnapshot): AnalysisQuery {
  const selected = params.get("dimensions")?.split(",").filter((value): value is AnalysisQuery["dimensions"][number] => dimensions.includes(value as AnalysisQuery["dimensions"][number]));
  return { shopId: params.get("shop") ?? (snapshot.scope.shop_id !== "all" ? snapshot.scope.shop_id : snapshot.shops[0]?.id ?? ""), platformId: (params.get("platform") ?? "all") as AnalysisQuery["platformId"], startDate: params.get("start") ?? shiftDate(snapshot.scope.date, -6), endDate: params.get("end") ?? snapshot.scope.date, dimensions: selected ? [...new Set(selected)] : [], timezone: "Asia/Shanghai" };
}
