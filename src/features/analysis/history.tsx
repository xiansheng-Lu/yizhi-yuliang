"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Copy, FileText, X } from "@phosphor-icons/react";
import type { AnalysisQuery, AnalysisResult } from "@/lib/business-analysis";
import { dimensionNames } from "@/lib/business-analysis";
import { reportPeriodIssue, reviewPeriodNames, type PublishedReport, type ReportDraft, type ReviewPeriod } from "@/lib/analysis-draft";
import type { AnalysisAdapter } from "@/services/analysis-service";
import { ErrorNotice } from "./shared";
import { LoadingState } from "@/components/ui";

type HistoryItem = { id: string; kind: "analysis" | "draft" | "published"; title: string; query?: AnalysisQuery; shopName: string; period: ReviewPeriod; href: string; analysisId: string; draft?: ReportDraft; publication?: PublishedReport };
export function ReviewHistory({ adapter, close }: { adapter: AnalysisAdapter; close: () => void }) {
  const router = useRouter();
  const [data, setData] = useState<{ results: AnalysisResult[]; drafts: ReportDraft[]; published: PublishedReport[] } | null>(null);
  const [error, setError] = useState(""), [message, setMessage] = useState(""), [attempt, setAttempt] = useState(0), [busy, setBusy] = useState("");
  const [kind, setKind] = useState("all"), [period, setPeriod] = useState("all"), [shop, setShop] = useState("all"), [start, setStart] = useState(""), [end, setEnd] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([adapter.listResults(controller.signal), adapter.listDrafts(controller.signal), adapter.listPublished(controller.signal)]).then(([results, drafts, published]) => { if (!controller.signal.aborted) setData({ results, drafts, published }); }).catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "读取历史记录失败"); });
    return () => controller.abort();
  }, [adapter, attempt]);
  const rows: HistoryItem[] = data ? [
    ...data.results.map<HistoryItem>(result => ({ id: result.id, kind: "analysis" as const, title: `${result.shopName} · 分析结果`, query: result.query, shopName: result.shopName, period: (["daily", "weekly", "monthly"] as const).find(period => !reportPeriodIssue(result.query, period)) ?? "custom", href: `/app/reports?result=${encodeURIComponent(result.id)}`, analysisId: result.id })),
    ...data.drafts.map(draft => ({ id: draft.id, kind: "draft" as const, title: draft.title, query: draft.query, shopName: draft.shopName, period: draft.period, href: `/app/reports?draft=${encodeURIComponent(draft.id)}`, analysisId: draft.analysisId, draft })),
    ...data.published.map<HistoryItem>(publication => {
      const draft = publication.snapshot ?? data.drafts.find(draft => draft.id === publication.draftId);
      return { id: publication.id, kind: "published" as const, title: publication.title ?? draft?.title ?? "已发布报告", query: publication.query ?? draft?.query, shopName: draft?.shopName ?? "发布版本", period: publication.period ?? draft?.period ?? "custom", href: `/app/reports?published=${encodeURIComponent(publication.id)}`, analysisId: publication.analysisId ?? draft?.analysisId ?? "", draft: publication.snapshot, publication };
    }),
  ] : [];
  const filtered = rows.filter(row => (kind === "all" || row.kind === kind) && (period === "all" || row.period === period) && (shop === "all" || row.query?.shopId === shop) && (!start || (row.query && row.query.startDate >= start)) && (!end || (row.query && row.query.endDate <= end)));
  const shops = [...new Map(rows.filter(row => row.query).map(row => [row.query!.shopId, row.shopName])).entries()];
  async function duplicate(item: HistoryItem) {
    if (busy) return; setBusy(item.id); setError("");
    try {
      if (!item.draft) { close(); router.push(`/app/reports?result=${encodeURIComponent(item.analysisId)}`); return; }
      const copy = structuredClone({ ...item.draft, id: crypto.randomUUID(), version: 0, title: `${item.draft.title.slice(0, 110)} · 副本`, updatedAt: new Date().toISOString() });
      const saved = await adapter.saveDraft(copy, new AbortController().signal);
      close(); router.push(`/app/reports?draft=${encodeURIComponent(saved.id)}`);
    } catch (e) { setError(e instanceof Error ? e.message : "创建副本失败"); } finally { setBusy(""); }
  }
  return <section className="ba-history" aria-label="历史报告"><div className="ba-title-line"><div><h2>历史报告</h2><p>分析结果、编辑草稿与发布版本分别保留，重新分析不会覆盖历史。</p></div><button className="button secondary" onClick={close}><X size={15} />返回当前步骤</button></div><div className="ba-history-filters"><label>记录类型<select aria-label="历史记录类型" value={kind} onChange={event => setKind(event.target.value)}><option value="all">全部记录</option><option value="analysis">分析结果</option><option value="draft">编辑草稿</option><option value="published">发布版本</option></select></label><label>报告周期<select aria-label="历史报告周期" value={period} onChange={event => setPeriod(event.target.value)}><option value="all">全部周期</option><option value="daily">日报</option><option value="weekly">周报</option><option value="monthly">月报</option><option value="custom">其他历史周期</option></select></label><label>店铺<select aria-label="历史报告店铺" value={shop} onChange={event => setShop(event.target.value)}><option value="all">全部店铺</option>{shops.map(([id, name]) => <option value={id} key={id}>{name}</option>)}</select></label><label>统计开始日期<input type="date" value={start} onChange={event => setStart(event.target.value)} /></label><label>统计结束日期<input type="date" value={end} onChange={event => setEnd(event.target.value)} /></label></div>{error && <ErrorNotice message={error} retry={() => { setError(""); setAttempt(attempt + 1); }} />}{message && <p role="status" className="ba-success">{message}</p>}{!data && !error ? <LoadingState /> : <><p className="ba-caption">共 {filtered.length} 条记录。日期筛选匹配完整包含在所选范围内的报告。</p><div className="ba-history-list">{filtered.map(item => <article key={`${item.kind}-${item.id}`}><FileText size={21} /><div><div className="ba-history-title"><b>{item.title}</b><span>{item.kind === "analysis" ? "原始分析" : item.kind === "draft" ? "编辑草稿" : "已发布版本"} · {reviewPeriodNames[item.period]}</span></div><p>{item.query ? `${item.query.startDate} — ${item.query.endDate} · ${item.shopName}` : "发布版本未返回查询元数据"}</p><details><summary>查看历史分析条件</summary><p>{item.query?.dimensions.map(dimension => dimensionNames[dimension]).join(" / ")}<br />平台：{item.query?.platformId ?? "未返回"} · Asia/Shanghai<br />来源分析ID：{item.analysisId || "未返回"}</p></details></div><div className="ba-history-actions"><Link className="button secondary" href={item.href} onClick={close}>{item.kind === "draft" ? "继续编辑" : "打开报告"}<ArrowRight size={13} /></Link><button className="ba-text-button" disabled={!!busy || !item.analysisId} onClick={() => void duplicate(item)}>{busy === item.id ? "创建中…" : "基于历史重新创建"}</button>{item.kind === "published" && <button className="ba-text-button" disabled={!item.publication?.url} title={item.publication?.url ? "复制后端正式发布地址" : "演示版本没有正式客户链接，支持导出HTML"} onClick={async () => { try { await navigator.clipboard.writeText(item.publication!.url!); setMessage("正式客户链接已复制。"); } catch { setError("复制失败，请在发布结果中手动复制正式地址。"); } }}><Copy size={13} />{item.publication?.url ? "复制客户链接" : "暂无正式客户链接"}</button>}</div></article>)}</div>{!filtered.length && <p className="ba-no-insight">暂无符合筛选条件的历史报告。</p>}</>}
  </section>;
}
