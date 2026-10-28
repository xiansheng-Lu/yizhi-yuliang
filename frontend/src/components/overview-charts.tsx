"use client";
import { useId, useState } from "react";
import Link from "next/link";
import { ChartLineUp, Info } from "@phosphor-icons/react";
import { money } from "@/lib/domain";
import { funnelRate, overviewMetricNames, type DistributionSlice, type FunnelStage, type OverviewFilter, type OverviewMetric, type OverviewModule, type TrendPoint } from "@/lib/overview";

const chartColors = ["var(--brand-primary)", "#17b89b", "#f0a127", "#8b79df", "#78b2fa"];
const metricColors = { spend: chartColors[0], gmv: chartColors[1], roi: chartColors[2] };
export function MissingData({ title = "数据源待接入", detail, compact = false }: { title?: string; detail: string; compact?: boolean }) {
  return <div className={`ov-missing ${compact ? "ov-missing-compact" : ""}`} role="status">
    <ChartLineUp size={compact ? 23 : 30} weight="light" />
    <b>{title}</b><p>{detail}</p>
  </div>;
}
export function Sparkline({ points, metric }: { points: TrendPoint[]; metric: OverviewMetric }) {
  const values = points.map((p) => p[metric]);
  if (values.filter((v) => v !== null).length < 2)
    return <div className="ov-spark-empty"><span />趋势数据待接入</div>;
  const max = Math.max(...values.filter((v): v is number => v !== null), 1);
  const path = values.map((value, i) => value === null ? "" :
    `${i === 0 || values[i - 1] === null ? "M" : "L"}${i / Math.max(1, values.length - 1) * 250},${32 - value / max * 27}`).join(" ");
  return <svg className="ov-spark" viewBox="0 0 250 38" preserveAspectRatio="none" aria-hidden="true">
    <path d={path} fill="none" stroke={metricColors[metric]} strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
  </svg>;
}
function timeLabel(at: string, hourly: boolean) {
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai",
    ...(hourly ? { hour: "2-digit", minute: "2-digit", hour12: false } : { month: "2-digit", day: "2-digit" }),
  }).format(new Date(at));
}
export function OverviewTrend({ trend, filter, onRetry }: { trend: OverviewModule<TrendPoint[]>; filter: OverviewFilter; onRetry?: () => void }) {
  const [visible, setVisible] = useState<OverviewMetric[]>(["spend", "gmv", "roi"]);
  const [active, setActive] = useState<number | null>(null);
  const id = useId().replace(/:/g, "");
  const points = trend.status === "ready" ? trend.data : [];
  const hourly = filter.startDate === filter.endDate;
  const left = 58, top = 20, width = 782, height = 208, bottom = top + height;
  const amounts = points.flatMap((p) => visible.filter((k) => k !== "roi").map((k) => p[k])).filter((n): n is number => n !== null);
  const amountMax = Math.max(100, ...amounts) * 1.1;
  const roiMax = Math.ceil(Math.max(1, ...points.map((p) => p.roi ?? 0)) * 1.1);
  const start = Date.parse(`${filter.startDate}T00:00:00+08:00`);
  const end = Date.parse(`${filter.endDate}T00:00:00+08:00`) + 86400000 - (hourly ? 0 : 1);
  const x = (p: TrendPoint) => left + (Date.parse(p.at) - start) / (end - start) * width;
  const y = (p: TrendPoint, key: OverviewMetric) => bottom - (p[key] ?? 0) / (key === "roi" ? roiMax : amountMax) * height;
  const selected = active !== null ? points[active] : undefined;
  const validPoints = points.length > 0;
  return <>
    <div className="ov-chart-meta">
      <div className="ov-legend" aria-label="趋势图指标">
        {(Object.keys(overviewMetricNames) as OverviewMetric[]).map((metric) => <button key={metric} type="button"
          aria-pressed={visible.includes(metric)} onClick={() => { setActive(null); setVisible((current) => current.includes(metric) ? current.filter((m) => m !== metric) : [...current, metric]); }}>
          <i style={{ background: metricColors[metric] }} />{overviewMetricNames[metric]}
        </button>)}
      </div>
      <span className="ov-granularity">{hourly ? "按小时" : "按天"}<Info size={13} aria-label="统计粒度随日期范围自动匹配" /></span>
    </div>
    <div className="ov-trend-plot">
      <div className="ov-axis-units"><span>金额（元）</span><span>投放ROI</span></div>
      <svg viewBox="0 0 900 265" role="img" aria-label={validPoints ? "广告消耗、支付GMV与投放ROI趋势；金额使用左轴，ROI使用右轴" : "趋势图暂无时间序列数据"}
        onPointerLeave={() => setActive(null)} onPointerMove={(event) => {
          if (!points.length) return;
          const bounds = event.currentTarget.getBoundingClientRect();
          const pointer = (event.clientX - bounds.left) / bounds.width * 900;
          setActive(points.reduce((best, point, i) => Math.abs(x(point) - pointer) < Math.abs(x(points[best]) - pointer) ? i : best, 0));
        }}>
        {validPoints && <defs>{visible.filter((k) => k !== "roi").map((k) => <linearGradient id={`${id}-${k}`} key={k} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={metricColors[k]} stopOpacity=".13" /><stop offset="100%" stopColor={metricColors[k]} stopOpacity=".01" /></linearGradient>)}</defs>}
        {[0, 1, 2, 3, 4].map((tick) => <g key={tick}>
          <line x1={left} x2={left + width} y1={top + tick * height / 4} y2={top + tick * height / 4} className="ov-gridline" />
          {validPoints && amounts.length > 0 && <text x={left - 10} y={top + tick * height / 4 + 4} textAnchor="end">{(amountMax * (1 - tick / 4) / 100).toLocaleString("zh-CN", { maximumFractionDigits: amountMax < 10000 ? 2 : 0 })}</text>}
          {validPoints && visible.includes("roi") && points.some((p) => p.roi !== null) && <text x={left + width + 16} y={top + tick * height / 4 + 4}>{(roiMax * (1 - tick / 4)).toFixed(1)}</text>}
        </g>)}
        {[0, 1, 2, 3, 4, 5, 6].map((tick) => <g key={tick}>
          <line x1={left + tick * width / 6} x2={left + tick * width / 6} y1={top} y2={bottom} className="ov-gridline" />
          <text x={left + tick * width / 6} y={bottom + 24} textAnchor="middle">{hourly && tick === 6 ? "24:00" : timeLabel(new Date(start + tick / 6 * (end - start)).toISOString(), hourly)}</text>
        </g>)}
        {visible.map((key) => {
          const segments: TrendPoint[][] = [];
          for (const point of points) {
            if (point[key] === null) { if (segments.at(-1)?.length) segments.push([]); continue; }
            if (!segments.length) segments.push([]);
            segments.at(-1)!.push(point);
          }
          return <g key={key}>{segments.filter((s) => s.length).map((segment, i) => {
            const path = segment.map((p, index) => `${index ? "L" : "M"}${x(p)},${y(p, key)}`).join(" ");
            return <g key={i}>
              {key !== "roi" && segment.length > 1 && <path d={`${path} L${x(segment.at(-1)!)},${bottom} L${x(segment[0])},${bottom} Z`} fill={`url(#${id}-${key})`} />}
              <path d={path} fill="none" stroke={metricColors[key]} strokeWidth="2" vectorEffect="non-scaling-stroke" />
              {segment.length === 1 && <circle cx={x(segment[0])} cy={y(segment[0], key)} r="3" fill={metricColors[key]} />}
            </g>;
          })}</g>;
        })}
        {selected && <g><line x1={x(selected)} x2={x(selected)} y1={top} y2={bottom} stroke="var(--text-caption)" strokeDasharray="4 4" />
          {visible.filter((k) => selected[k] !== null).map((key) => <circle key={key} cx={x(selected)} cy={y(selected, key)} r="4.5" fill={metricColors[key]} stroke="white" strokeWidth="2" />)}</g>}
      </svg>
      {!validPoints && <div className="ov-chart-empty"><div><MissingData title={trend.status === "error" ? "趋势数据读取失败" : trend.status === "empty" ? "所选范围暂无数据" : "经营趋势待接入"} detail={trend.status === "ready" ? "暂无可展示的时间序列。" : trend.reason} />{trend.status === "error" && onRetry && <button type="button" className="ov-chart-retry" onClick={onRetry}>重新加载趋势</button>}</div></div>}
      {validPoints && visible.length === 0 && <div className="ov-chart-empty"><p>选择上方指标查看趋势</p></div>}
      {selected && visible.length > 0 && <div className="ov-chart-tooltip" role="status" style={{ left: `${Math.min(73, Math.max(3, x(selected) / 900 * 100 - 7))}%` }}>
        <b>{new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(selected.at))}</b>
        {visible.map((key) => <div key={key}><span><i style={{ background: metricColors[key] }} />{overviewMetricNames[key]}</span><strong>{selected[key] === null ? "--" : key === "roi" ? selected[key].toFixed(2) : money(selected[key])}</strong></div>)}
      </div>}
    </div>
    {validPoints && <input className="ov-chart-keyboard" type="range" min="0" max={points.length - 1} value={active ?? 0} onChange={(e) => setActive(Number(e.target.value))} aria-label="使用左右方向键选择趋势时间点" aria-valuetext={selected ? timeLabel(selected.at, hourly) : "选择时间点"} />}
    <p className="ov-chart-footnote">Asia/Shanghai · 金额与ROI分别使用左右坐标轴 · {validPoints ? "悬停或使用时间滑块查看详情" : "数据接入后可查看多指标趋势与时间点详情"}</p>
  </>;
}
export function OverviewDonut({ slices, label, monetary = true, href = "/app/plans" }: { slices: DistributionSlice[]; label: string; monetary?: boolean; href?: string }) {
  const total = slices.reduce((n, s) => n + s.value, 0);
  return <div className="ov-donut-layout">
    <div className={`ov-donut ${total === 0 ? "ov-donut-empty" : ""}`}>
      <svg viewBox="0 0 150 150" role="img" aria-label={total > 0 ? `${label}构成，共${slices.length}类` : `${label}暂无可用数据`}>
        <circle cx="75" cy="75" r="59" fill="none" stroke="var(--border-subtle)" strokeWidth="20" />
        {slices.map((slice, i) => {
          const share = total > 0 ? slice.value / total * 100 : 0;
          const dashOffset = total > 0 ? -slices.slice(0, i).reduce((n, s) => n + s.value, 0) / total * 100 : 0;
          return <circle key={slice.id} cx="75" cy="75" r="59" pathLength="100" fill="none" stroke={chartColors[i % chartColors.length]} strokeWidth="20" strokeDasharray={`${share} ${100 - share}`} strokeDashoffset={dashOffset} transform="rotate(-90 75 75)"><title>{slice.name}：{monetary ? money(slice.value) : slice.value.toLocaleString("zh-CN")}，{share.toFixed(1)}%</title></circle>;
        })}
      </svg>
      <div className="ov-donut-center"><strong>{total > 0 ? monetary ? money(total) : total.toLocaleString("zh-CN") : "--"}</strong><span>{label}</span></div>
    </div>
    <div className="ov-donut-legend">
      {total > 0 ? slices.map((slice, i) => <Link key={slice.id} href={href} title={`查看${slice.name}相关数据`}>
        <span><i style={{ background: chartColors[i % chartColors.length] }} />{slice.name}</span>
        <small>{(slice.value / total * 100).toFixed(1)}%</small><b>{monetary ? money(slice.value) : slice.value.toLocaleString("zh-CN")}</b>
      </Link>) : <><b>暂无可展示的构成</b><p>有同口径数据后显示数值与占比。</p></>}
    </div>
  </div>;
}
export function OverviewFunnel({ funnel }: { funnel: OverviewModule<FunnelStage[]> }) {
  const stages = funnel.status === "ready" ? funnel.data : ["曝光量", "点击量", "进入直播间", "下单人数", "支付订单"].map((name, i) => ({ id: String(i), name, count: -1 }));
  const max = Math.max(1, ...stages.map((s) => s.count));
  return <>
    <div className={`ov-funnel ${funnel.status !== "ready" ? "ov-funnel-unavailable" : ""}`}>
      {stages.map((stage, i) => { const rate = funnelRate(stages, i); return <div className="ov-funnel-row" key={stage.id}>
        <div className="ov-funnel-shape"><span style={{ width: `${funnel.status === "ready" ? Math.max(6, stage.count / max * 100) : 100 - i * 16}%`, opacity: 1 - i * .13 }} title={stage.count < 0 ? `${stage.name}数据待接入` : `${stage.name}：${stage.count.toLocaleString("zh-CN")}；环节转化${rate === null ? "不可计算" : `${(rate * 100).toFixed(1)}%`}；环节流失${rate === null ? "不可计算" : `${((1 - rate) * 100).toFixed(1)}%`}`} /></div>
        <span className="ov-funnel-label">{stage.name}</span><div><b>{stage.count < 0 ? "--" : stage.count.toLocaleString("zh-CN")}</b>{i > 0 && <small>{rate === null ? "待统计" : `${(rate * 100).toFixed(1)}%`}</small>}</div>
      </div>; })}
    </div>
    {funnel.status !== "ready" && <p className="ov-module-note"><Info size={13} />{funnel.reason}</p>}
  </>;
}
