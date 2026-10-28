"use client";
import { useMemo, useState } from "react";
import { ChartLineUp, ArrowSquareOut } from "@phosphor-icons/react";
import { formatAnalysisValue, groupAnalysisPoints, pointValue, type AnalysisInsight, type AnalysisMetric, type AnalysisPoint, type AnalysisRow } from "@/lib/business-analysis";

export const analysisColors = ["#335fe1", "#12a38b", "#d58a26", "#9274c6"];
export function AnalysisTrend({ points, metrics, comparisonPoints = [], insights = [], weekly = false, onDetail, normalized = false }: {
  points: AnalysisPoint[]; metrics: AnalysisMetric[]; comparisonPoints?: AnalysisPoint[]; insights?: AnalysisInsight[]; weekly?: boolean;
  onDetail?: (date: string) => void; normalized?: boolean;
}) {
  const grouped = useMemo(() => groupAnalysisPoints(points, weekly), [points, weekly]);
  // Compare equal ordinal buckets; actual comparison dates remain visible.
  const compared = useMemo(() => groupAnalysisPoints(comparisonPoints, weekly), [comparisonPoints, weekly]);
  const [active, setActive] = useState<number | null>(null);
  const selected = active !== null ? grouped[active] : undefined;
  const available = grouped.some(point => metrics.some(metric => pointValue(point.values, metric) !== null));
  const left = 70, width = 850, top = 25, height = 205, bottom = top + height;
  const indexBase = (metric: AnalysisMetric) => grouped.map(point => pointValue(point.values, metric)).find(value => value !== null && value > 0) ?? null;
  const rawValue = (point: AnalysisPoint, metric: AnalysisMetric) => pointValue(point.values, metric);
  const value = (point: AnalysisPoint, metric: AnalysisMetric) => {
    const raw = rawValue(point, metric), base = indexBase(metric);
    return raw === null ? null : normalized ? base === null ? null : raw / base * 100 : raw;
  };
  const allValues = grouped.flatMap(point => metrics.map(metric => value(point, metric))).concat(compared.flatMap(point => metrics.map(metric => value(point, metric)))).filter((v): v is number => v !== null);
  const max = Math.max(1, ...allValues) * 1.12;
  const x = (index: number) => left + index / Math.max(1, grouped.length - 1) * width;
  const y = (v: number) => bottom - v / max * height;
  const path = (rows: AnalysisPoint[], metric: AnalysisMetric) => rows.map((point, index) => { const v = value(point, metric); return v === null ? "" : `${index === 0 || value(rows[index - 1], metric) === null ? "M" : "L"}${x(index)},${y(v)}`; }).join(" ");
  const nearest = (clientX: number, element: SVGSVGElement) => {
    const bounds = element.getBoundingClientRect(), pointer = (clientX - bounds.left) / bounds.width * 1000;
    return Math.max(0, Math.min(grouped.length - 1, Math.round((pointer - left) / width * (grouped.length - 1))));
  };
  return <div className="ba-chart">
    <div className="ba-chart-legend">{metrics.map((metric, i) => <span key={metric.id}><i style={{ background: analysisColors[i % 4] }} />{metric.name}</span>)}{compared.length > 0 && <span className="ba-legend-compare">虚线：对比周期</span>}<small>{normalized ? "指数对照 · 首个有效值=100" : metrics[0]?.unit === "minor" ? "金额（元）" : metrics[0]?.unit === "rate" ? "比例（%）" : "指标数值"}</small></div>
    <div className="ba-plot">
      {available ? <svg viewBox="0 0 1000 280" role="img" aria-label={`${metrics.map(m => m.name).join("、")} ${weekly ? "按周" : "按日"}趋势；点击时间点查看业务明细`}
        onPointerMove={event => setActive(nearest(event.clientX, event.currentTarget))} onPointerLeave={() => setActive(null)} onClick={event => { if (grouped.length) onDetail?.(grouped[nearest(event.clientX, event.currentTarget)].date); }}>
        {[0, 1, 2, 3, 4].map(tick => <g key={tick}><line x1={left} x2={left + width} y1={top + tick / 4 * height} y2={top + tick / 4 * height} className="ba-gridline" /><text x={left - 12} y={top + tick / 4 * height + 4} textAnchor="end">{normalized ? (max * (1 - tick / 4)).toFixed(0) : metrics[0]?.unit === "minor" ? (max * (1 - tick / 4) / 100).toLocaleString("zh-CN", { maximumFractionDigits: 0 }) : metrics[0]?.unit === "rate" ? (max * (1 - tick / 4) * 100).toFixed(1) : (max * (1 - tick / 4)).toLocaleString("zh-CN", { maximumFractionDigits: 1 })}</text></g>)}
        {metrics.map((metric, i) => <g key={metric.id}>
          <path d={path(grouped, metric)} fill="none" stroke={analysisColors[i % 4]} strokeWidth="2.4" vectorEffect="non-scaling-stroke" />
          {compared.length === grouped.length && <path d={path(compared, metric)} fill="none" stroke={analysisColors[i % 4]} strokeWidth="1.7" opacity=".5" strokeDasharray="5 5" vectorEffect="non-scaling-stroke" />}
          {grouped.map((point, index) => { const v = value(point, metric); return v === null ? null : <circle key={point.date} cx={x(index)} cy={y(v)} r={grouped.length === 1 ? 4 : 2.5} fill={analysisColors[i % 4]}><title>{point.date} · {metric.name}：{formatAnalysisValue(rawValue(point, metric), metric.unit)}</title></circle>; })}
        </g>)}
        {grouped.map((point, index) => {
          const anomalies = insights.filter(insight => insight.kind === "anomaly" && insight.date && (weekly ? insight.date >= point.date && insight.date <= (grouped[index + 1]?.date ?? "9999") : insight.date === point.date));
          return anomalies.length ? <g key={point.date}><line x1={x(index)} x2={x(index)} y1={top} y2={bottom} stroke="#d58a26" strokeDasharray="3 5" /><circle cx={x(index)} cy={top} r="5" fill="#d58a26"><title>{anomalies.map(insight => insight.title).join("；")}</title></circle></g> : null;
        })}
        {selected && <line x1={x(active!)} x2={x(active!)} y1={top} y2={bottom} stroke="#94a3bd" strokeDasharray="4 4" />}
        {grouped.filter((_, index) => index === 0 || index === grouped.length - 1 || index % Math.max(1, Math.ceil(grouped.length / 6)) === 0).map(point => <text key={point.date} x={x(grouped.indexOf(point))} y={bottom + 28} textAnchor="middle">{point.date.slice(5)}{weekly ? "周" : ""}</text>)}
      </svg> : <div className="ba-chart-empty"><ChartLineUp size={34} weight="light" /><b>暂无可用趋势数据</b><p>当前指标未返回时间序列，缺失日期不会补零。</p></div>}
      {selected && <div className="ba-chart-tooltip" role="status" style={{ left: `${Math.min(70, Math.max(2, x(active!) / 10 - 8))}%` }}><b>{selected.date}{weekly ? " 起当周" : ""}</b>{metrics.map((metric, i) => <div key={metric.id}><span style={{ color: analysisColors[i % 4] }}>{metric.name}</span><strong>{formatAnalysisValue(rawValue(selected, metric), metric.unit)}</strong></div>)}{compared[active!] && <small>对比 {compared[active!].date}：{formatAnalysisValue(rawValue(compared[active!], metrics[0]), metrics[0]?.unit)}</small>}</div>}
    </div>
    {available && <div className="ba-chart-footer"><label>选择时间点<input type="range" min="0" max={Math.max(0, grouped.length - 1)} value={active ?? 0} onChange={event => setActive(Number(event.target.value))} aria-label="趋势时间点" aria-valuetext={selected?.date ?? grouped[0]?.date} /></label>{onDetail && <button className="ba-text-button" onClick={() => onDetail((selected ?? grouped[0]).date)}>查看{selected?.date ?? grouped[0]?.date}业务明细<ArrowSquareOut size={13} /></button>}</div>}
    <p className="ba-caption">{normalized ? "指数仅用于观察变化方向，不比较绝对规模，不证明因果关系。" : "悬停查看数值，橙色节点为规则识别的异常。"} {weekly && compared.length !== grouped.length && compared.length > 0 ? "对比周分桶数量不一致，不绘制对比曲线。" : ""}</p>
  </div>;
}

export function AnalysisTable({ rows, metrics, onSelect }: { rows: AnalysisRow[]; metrics: AnalysisMetric[]; onSelect?: (row: AnalysisRow) => void }) {
  const [sort, setSort] = useState({ id: metrics[0]?.id ?? "", desc: true });
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const metric = metrics.find(m => m.id === sort.id);
  const filtered = rows.filter(row => row.name.includes(search)).sort((a, b) => {
    const av = metric ? pointValue(a.values, metric) : null, bv = metric ? pointValue(b.values, metric) : null;
    if (av === null) return 1; if (bv === null) return -1;
    return (av - bv) * (sort.desc ? -1 : 1);
  });
  return <div className="ba-details"><div className="ba-table-tools"><label>搜索明细<input placeholder="按业务对象名称搜索" value={search} onChange={event => { setSearch(event.target.value); setPage(0); }} /></label><small>{filtered.length} 条记录 · 点击表头排序</small></div><div className="table-scroll"><table><thead><tr><th>业务对象</th>{metrics.map(metric => <th key={metric.id} aria-sort={sort.id === metric.id ? sort.desc ? "descending" : "ascending" : "none"}><button onClick={() => setSort({ id: metric.id, desc: sort.id === metric.id ? !sort.desc : true })}>{metric.name}{sort.id === metric.id ? sort.desc ? " ↓" : " ↑" : ""}</button></th>)}</tr></thead><tbody>{filtered.slice(page * 5, page * 5 + 5).map(row => <tr key={row.id}><td>{onSelect ? <button className="ba-text-button" onClick={() => onSelect(row)}>{row.name}<ArrowSquareOut size={12} /></button> : row.name}<small>{row.date}</small></td>{metrics.map(metric => <td key={metric.id}>{formatAnalysisValue(pointValue(row.values, metric), metric.unit)}</td>)}</tr>)}</tbody></table></div>{!filtered.length && <p className="ba-caption">暂无符合条件的明细；未返回的数据不会补造。</p>}{filtered.length > 5 && <div className="ba-pagination"><button disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button><span>{page + 1} / {Math.ceil(filtered.length / 5)}</span><button disabled={(page + 1) * 5 >= filtered.length} onClick={() => setPage(page + 1)}>下一页</button></div>}</div>;
}
