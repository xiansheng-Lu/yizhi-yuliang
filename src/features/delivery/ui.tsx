"use client";
import { useEffect, useRef, type ReactNode } from "react";
import { X, WarningCircle, Database, ChartLine } from "@phosphor-icons/react";
import {
  availabilityNames,
  redact,
  type Capability,
  type TrendPoint,
} from "@/lib/delivery";

export function Section({
  title,
  detail,
  action,
  children,
  className = "",
}: {
  title: string;
  detail?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`dw-section ${className}`}>
      <header>
        <div>
          <h2>{title}</h2>
          {detail && <p>{detail}</p>}
        </div>
        {action}
      </header>
      {children}
    </section>
  );
}
export function Metrics({
  items,
}: {
  items: { name: string; value: ReactNode; note: string }[];
}) {
  return (
    <div className="dw-metrics">
      {items.map((item) => (
        <article key={item.name}>
          <span>{item.name}</span>
          <strong>{item.value}</strong>
          <small>{item.note}</small>
        </article>
      ))}
    </div>
  );
}
export function Missing({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="dw-empty">
      <Database size={27} weight="light" />
      <strong>{title}</strong>
      <p>{detail ?? "真实接口返回后展示，不生成模拟经营数据。"}</p>
    </div>
  );
}
export function Gate({ capability }: { capability: Capability }) {
  return (
    <span
      className={`dw-gate ${capability.status === "AUTHORIZED" ? "ready" : ""}`}
    >
      <WarningCircle size={13} />
      {availabilityNames[capability.status]} · {capability.reason}
    </span>
  );
}
export function Status({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`dw-status ${tone}`}>{children}</span>;
}
export function Json({ value }: { value: unknown }) {
  return (
    <pre className="dw-json">
      {typeof value === "string"
        ? String(redact(value))
        : (JSON.stringify(redact(value), null, 2) ?? "未提供")}
    </pre>
  );
}
export function Progress({
  value,
  max,
  label,
}: {
  value: number | null;
  max: number;
  label: string;
}) {
  const percent =
    value !== null && max > 0
      ? Math.max(0, Math.min(100, (value / max) * 100))
      : null;
  return (
    <div className="dw-progress">
      <div>
        <span>{label}</span>
        <b>{percent === null ? "--" : `${percent.toFixed(1)}%`}</b>
      </div>
      <progress aria-label={label} max={100} value={percent ?? 0} />
    </div>
  );
}
export function Modal({
  title,
  children,
  close,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null),
    onClose = useRef(close);
  useEffect(() => {
    onClose.current = close;
  }, [close]);
  useEffect(() => {
    const dialog = ref.current;
    const previous = document.activeElement as HTMLElement | null;
    dialog?.showModal();
    return () => {
      dialog?.close();
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={`dw-modal ${wide ? "wide" : ""}`}
      onCancel={(e) => {
        e.preventDefault();
        onClose.current();
      }}
      aria-label={title}
    >
      <header>
        <h2>{title}</h2>
        <button className="icon-button" aria-label="关闭对话框" onClick={close}>
          <X size={20} />
        </button>
      </header>
      <div className="dw-modal-content">{children}</div>
    </dialog>
  );
}
/** SVG is a data plot; gaps are retained instead of interpolating missing data. */
export function Trend({
  points,
  actual,
  target,
  title,
  forecast = false,
}: {
  points: TrendPoint[];
  actual: keyof TrendPoint;
  target: keyof TrendPoint;
  title: string;
  forecast?: boolean;
}) {
  const values = points
    .flatMap((p) => [p[actual], p[target]])
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (!values.length)
    return (
      <div className="dw-chart-empty">
        <ChartLine size={26} weight="light" />
        <strong>{title}</strong>
        <span>时间序列待接入</span>
      </div>
    );
  const max = Math.max(...values, 1);
  const paths = (key: keyof TrendPoint) => {
    let drawing = false;
    return points
      .map((p, i) => {
        const value = p[key];
        if (typeof value !== "number" || !Number.isFinite(value)) {
          drawing = false;
          return "";
        }
        const prefix = drawing ? "L" : "M";
        drawing = true;
        return `${prefix}${30 + (i / Math.max(points.length - 1, 1)) * 620},${180 - (value / max) * 150}`;
      })
      .join(" ");
  };
  return (
    <figure className="dw-chart">
      <figcaption>
        {title}
        <span>
          <i />
          实际 <i className="target" />
          {forecast ? "预测（非实际）" : "目标"}
        </span>
      </figcaption>
      <svg viewBox="0 0 680 205" role="img" aria-label={title}>
        {[30, 80, 130, 180].map((y) => (
          <line
            key={y}
            x1="30"
            y1={y}
            x2="650"
            y2={y}
            stroke="var(--border-subtle)"
          />
        ))}
        <path
          d={paths(target)}
          stroke="var(--text-caption)"
          strokeDasharray="5 5"
          strokeWidth="2"
          fill="none"
        />
        <path
          d={paths(actual)}
          stroke="var(--brand-primary)"
          strokeWidth="2.5"
          fill="none"
        />
        <text x="30" y="201">
          {points[0]?.date}
        </text>
        <text x="650" y="201" textAnchor="end">
          {points.at(-1)?.date}
        </text>
      </svg>
      <details>
        <summary>查看原始趋势值与日期</summary>
        <Json value={points} />
      </details>
    </figure>
  );
}
