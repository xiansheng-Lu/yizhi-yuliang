"use client";
import type { ReactNode } from "react";
import {
  ArrowClockwise,
  LockKey,
  Tray,
  WarningCircle,
} from "@phosphor-icons/react";
import { ServiceError } from "@/services/contracts";

// Adapted from 易致投手/components/ui/primitives.tsx @026bde1; original unchanged.
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "blue" | "warning" | "green";
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
export function Panel({
  title,
  detail,
  action,
  children,
}: {
  title: string;
  detail?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <h2>{title}</h2>
          {detail && <p>{detail}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
export function Metric({
  label,
  value,
  note,
}: {
  label: string;
  value: string;
  note: string;
}) {
  return (
    <article className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </article>
  );
}
export function EmptyState({
  title = "暂无数据",
  description = "数据接入后将在这里显示。",
}: {
  title?: string;
  description?: string;
}) {
  return (
    <div className="state-view">
      <Tray size={30} />
      <h2>{title}</h2>
      <p>{description}</p>
    </div>
  );
}
export function LoadingState() {
  return (
    <div className="state-view" role="status">
      <span className="loading-ring" />
      <p>正在读取工作区…</p>
    </div>
  );
}
export function ErrorState({
  error,
  retry,
  recover,
}: {
  error: ServiceError;
  retry?: () => void;
  recover?: () => void;
}) {
  if (error.code === "NO_DATA")
    return <EmptyState description={error.message} />;
  const forbidden = error.code === "FORBIDDEN";
  return (
    <div className="state-view" role="alert">
      {forbidden ? <LockKey size={32} /> : <WarningCircle size={32} />}
      <h2>{forbidden ? "暂无访问权限" : "暂时无法加载"}</h2>
      <p>{error.message}</p>
      <div className="row">
        {retry && (
          <button className="button secondary" onClick={retry}>
            <ArrowClockwise size={16} />
            重新加载
          </button>
        )}
        {recover && (
          <button className="button secondary" onClick={recover}>
            恢复演示数据
          </button>
        )}
      </div>
    </div>
  );
}
