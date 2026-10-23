"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowDown, ArrowLeft, ArrowUp, Copy, DownloadSimple, Eye, FileText, FloppyDisk, Globe, Plus, Trash } from "@phosphor-icons/react";
import { Badge, Panel } from "@/components/ui";
import { exportReportHtml, moveReportModule, reviewPeriodNames, validateDraft, type PublishedReport, type ReportDraft, type ReportModule } from "@/lib/analysis-draft";
import type { AnalysisAdapter } from "@/services/analysis-service";
import { AnalysisModal, ErrorNotice } from "./shared";

export function ReportPreview({ draft }: { draft: ReportDraft }) {
  return <iframe className="ba-client-preview" title="客户阅读页预览" sandbox="" srcDoc={exportReportHtml(draft)} />;
}
export function ReportEditor({ initial, adapter, delivery = false }: { initial: ReportDraft; adapter: AnalysisAdapter; delivery?: boolean }) {
  const router = useRouter();
  const [draft, setDraft] = useState<ReportDraft>(structuredClone(initial));
  const [saved, setSaved] = useState(JSON.stringify(initial));
  const [selected, setSelected] = useState(initial.modules[0]?.id);
  const [preview, setPreview] = useState(delivery);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [published, setPublished] = useState<PublishedReport | null>(null);
  const [navigate, setNavigate] = useState("");
  const [newModule, setNewModule] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newSection, setNewSection] = useState("");
  const dirty = JSON.stringify(draft) !== saved;
  const activeModule = draft.modules.find(module => module.id === selected);
  const bufferKey = `yuliang:review-unsaved:${initial.source}:${initial.id}`;
  const [bufferReady, setBufferReady] = useState(false);
  useEffect(() => {
    let active = true;
    void Promise.resolve().then(() => {
      if (!active) return;
      try {
        const raw = window.sessionStorage.getItem(bufferKey);
        if (raw) { const buffer = JSON.parse(raw) as ReportDraft; if (buffer.version === initial.version) { setDraft(buffer); setMessage("已恢复当前会话内尚未保存的编辑。"); } }
      } catch { /* The saved backend/local draft remains available. */ }
      setBufferReady(true);
    });
    return () => { active = false; };
  }, [bufferKey, initial]);
  useEffect(() => {
    if (!bufferReady) return;
    try { if (dirty) window.sessionStorage.setItem(bufferKey, JSON.stringify(draft)); else window.sessionStorage.removeItem(bufferKey); } catch { /* beforeunload still guards edits when session storage is unavailable. */ }
  }, [bufferReady, bufferKey, dirty, draft]);
  useEffect(() => {
    if (!dirty) return;
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    const intercept = (event: MouseEvent) => {
      const anchor = (event.target as Element)?.closest?.("a[href]");
      if (!(anchor instanceof HTMLAnchorElement) || anchor.target || anchor.origin !== window.location.origin || anchor.getAttribute("href")?.startsWith("#")) return;
      event.preventDefault(); event.stopPropagation(); setNavigate(anchor.pathname + anchor.search);
    };
    window.addEventListener("beforeunload", unload); document.addEventListener("click", intercept, true);
    return () => { window.removeEventListener("beforeunload", unload); document.removeEventListener("click", intercept, true); };
  }, [dirty]);
  function editModule(patch: Partial<ReportModule>) { setDraft(current => ({ ...current, modules: current.modules.map(item => item.id === selected ? { ...item, ...patch } : item) })); setMessage(""); }
  async function save(): Promise<ReportDraft> {
    validateDraft(draft);
    if (!dirty) return draft;
    const next = await adapter.saveDraft(draft, new AbortController().signal);
    setDraft(next); setSaved(JSON.stringify(next)); setMessage(`草稿已保存 · 版本 ${next.version}`); return next;
  }
  async function run(action: "save" | "publish" | "leave") {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(""); setMessage("");
    try {
      const current = await save();
      if (action === "publish") { setPublished(await adapter.publishDraft(current.id, current.version, new AbortController().signal)); setMessage(current.source === "MOCK" ? "本地演示版本已保存，可导出独立HTML交付；公网发布需接入后端。" : "客户网页已发布。"); }
      if (action === "leave") { setNavigate(""); router.push(navigate); }
    } catch (e) { setError(e instanceof Error ? e.message : "操作失败，请重试。"); } finally { busyRef.current = false; setBusy(false); }
  }
  function download() {
    try {
      validateDraft(draft);
      const url = URL.createObjectURL(new Blob([exportReportHtml(draft)], { type: "text/html;charset=utf-8" }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = `${draft.title.replace(/[<>:"/\\|?*]/g, "_")}.html`; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000); setMessage("已导出当前编辑版本的独立客户网页，可直接在浏览器打开。");
    } catch (e) { setError(e instanceof Error ? e.message : "导出失败"); }
  }
  return <div className="ba-workspace"><header className="ba-report-heading"><div><span className="ba-eyebrow">数据复盘 / 可编辑报告</span><h2>复盘报告编辑</h2><p>将分析转化为可审核、可交付的客户报告。</p></div><div className="ba-actions"><Link href="/app/reports" className="button secondary"><ArrowLeft size={15} />返回分析条件</Link><button className="button secondary" disabled={busy} onClick={() => void run("save")}><FloppyDisk size={15} />{busy ? "处理中…" : "保存草稿"}</button><button className="button secondary" onClick={() => setPreview(true)}><Eye size={15} />预览报告</button><button className="button primary" disabled={busy} onClick={() => void run("publish")}><Globe size={15} />发布网页报告</button></div></header>
    <div className="ba-source-banner"><FileText size={19} /><div><b>{draft.source === "MOCK" ? "演示数据 · 复盘草稿" : "独立复盘草稿"}</b><span>人工文字、模块和图表调整只保存到本草稿，原始分析与经营数据独立保留。</span></div><Badge tone={dirty ? "warning" : "green"}>{dirty ? "有未保存更改" : "已保存"}</Badge></div>
    {error && <ErrorNotice message={error} />}{message && <p className="ba-success" role="status">{message}</p>}
    {published && <div className="ba-publication"><b>{published.source === "MOCK" ? "本地演示发布版本" : "发布成功"}</b><span>发布时间：{new Date(published.publishedAt).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}</span>{published.url ? <a className="ba-text-button" href={published.url} target="_blank" rel="noreferrer">打开客户网页</a> : <><Link className="ba-text-button" href={`/app/reports?published=${encodeURIComponent(published.id)}`}>查看已发布快照</Link><small>此链接仅在当前浏览器账号内可读，不是公网客户链接。导出HTML后可交付客户。</small></>}<button className="button secondary" onClick={download}><DownloadSimple size={15} />导出HTML</button><button className="button secondary" disabled={!published.url} onClick={async () => { try { await navigator.clipboard.writeText(published.url!); setMessage("正式客户链接已复制。"); } catch { setError("复制失败，请手动复制发布地址。"); } }}><Copy size={15} />复制客户链接</button></div>}
    <div className="ba-editor-meta"><label>报告名称<input aria-label="报告名称" maxLength={120} value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label><label>报告摘要<textarea aria-label="报告摘要" rows={3} maxLength={20000} value={draft.modules.find(item => item.id === "summary")?.text ?? ""} onChange={event => { const exists = draft.modules.some(item => item.id === "summary"); setDraft({ ...draft, modules: exists ? draft.modules.map(item => item.id === "summary" ? { ...item, text: event.target.value } : item) : [{ id: "summary", title: "经营摘要", text: event.target.value, visible: true, showChart: false, metricIds: [] }, ...draft.modules] }); }} /></label><div><small>继承的统计周期</small><b>{draft.query.startDate} — {draft.query.endDate}</b><span>{draft.shopName} · {draft.platformName} · {reviewPeriodNames[draft.period]}</span><Link href={`/app/reports?result=${encodeURIComponent(draft.analysisId)}`}>查看原始分析</Link></div></div>
    <div className="ba-editor-layout"><aside className="ba-module-list" aria-label="复盘报告模块"><div><h2>报告模块</h2><button className="ba-text-button" onClick={() => { setNewTitle(""); setNewSection(""); setNewModule(true); }}><Plus size={15} />新增</button></div>{draft.modules.map((item, index) => <article key={item.id} className={item.id === selected ? "active" : ""}><button className="ba-module-select" aria-pressed={item.id === selected} onClick={() => setSelected(item.id)}><span>{String(index + 1).padStart(2, "0")}</span><b>{item.title || "未命名模块"}</b>{!item.visible && <small>隐藏</small>}</button><div className="ba-module-tools"><button aria-label={`上移${item.title}`} disabled={index === 0} onClick={() => setDraft({ ...draft, modules: moveReportModule(draft.modules, item.id, -1) })}><ArrowUp size={14} /></button><button aria-label={`下移${item.title}`} disabled={index === draft.modules.length - 1} onClick={() => setDraft({ ...draft, modules: moveReportModule(draft.modules, item.id, 1) })}><ArrowDown size={14} /></button><button aria-label={`删除${item.title}`} disabled={draft.modules.length === 1} onClick={() => { const next = draft.modules.filter(module => module.id !== item.id); setDraft({ ...draft, modules: next }); if (selected === item.id) setSelected(next[0].id); }}><Trash size={14} /></button></div></article>)}</aside>
      <Panel title="编辑模块" detail="文字可修改；原始指标和明细以分析快照为准">{activeModule && <div className="ba-module-editor"><label>模块标题<input aria-label="模块标题" value={activeModule.title} maxLength={120} onChange={event => editModule({ title: event.target.value })} /></label><div className="ba-editor-toggles"><label><input type="checkbox" checked={activeModule.showMetrics !== false} onChange={event => editModule({ showMetrics: event.target.checked })} />显示指标</label><label><input type="checkbox" checked={activeModule.visible} onChange={event => editModule({ visible: event.target.checked })} />客户报告中显示此模块</label><label><input type="checkbox" checked={activeModule.showChart} onChange={event => editModule({ showChart: event.target.checked })} />显示图表</label></div><label>分析结论与正文<textarea aria-label="分析结论与正文" rows={13} value={activeModule.text} maxLength={20000} onChange={event => editModule({ text: event.target.value })} /></label><fieldset><legend>本模块引用指标</legend>{draft.metrics.map(metric => <label key={metric.id}><input type="checkbox" checked={activeModule.metricIds.includes(metric.id)} onChange={event => editModule({ metricIds: event.target.checked ? [...activeModule.metricIds, metric.id] : activeModule.metricIds.filter(id => id !== metric.id) })} />{metric.name}</label>)}</fieldset><div className="ba-editor-footer"><small>文字 {activeModule.text.length}/20000 · 图表使用首个引用指标</small><button className="button secondary" onClick={download}><DownloadSimple size={15} />导出客户网页 HTML</button></div></div>}</Panel></div>
    {preview && <AnalysisModal title="客户阅读页预览" close={() => setPreview(false)}><p className="ba-caption">当前编辑版本 · 隐藏模块不会出现在客户网页中。</p><ReportPreview draft={draft} /><div className="ba-actions"><button className="button secondary" onClick={download}><DownloadSimple size={15} />导出客户网页</button><button className="button primary" onClick={() => setPreview(false)}>继续编辑</button></div></AnalysisModal>}
    {navigate && <AnalysisModal title="保存未完成的复盘编辑" close={() => setNavigate("")}><p>当前草稿有未保存内容，请保存后离开，或放弃本次未保存的修改。</p><div className="ba-actions"><button className="button primary" disabled={busy} onClick={() => void run("leave")}>保存并离开</button><button className="button secondary" disabled={busy} onClick={() => { const href = navigate; window.sessionStorage.removeItem(bufferKey); setNavigate(""); router.push(href); }}>放弃修改并离开</button><button className="button secondary" onClick={() => { const href = navigate; setNavigate(""); router.push(href); }}>保留未保存内容并切换</button><button className="button secondary" onClick={() => setNavigate("")}>继续编辑</button></div>{error && <ErrorNotice message={error} />}</AnalysisModal>}
    {newModule && <AnalysisModal title="新增复盘模块" close={() => setNewModule(false)}><div className="ba-module-editor"><label>模块标题<input value={newTitle} maxLength={120} onChange={event => setNewTitle(event.target.value)} placeholder="例如：下周行动计划" /></label><label>引用已有分析模块<select value={newSection} onChange={event => { setNewSection(event.target.value); const section = draft.sections.find(section => section.id === event.target.value); if (section && !newTitle) setNewTitle(section.title); }}><option value="">自定义文字模块</option>{draft.sections.map(section => <option key={section.id} value={section.id}>{section.title}</option>)}</select></label><button className="button primary" disabled={!newTitle.trim() || draft.modules.length >= 40} onClick={() => { const section = draft.sections.find(section => section.id === newSection), id = crypto.randomUUID(); setDraft({ ...draft, modules: [...draft.modules, { id, title: newTitle.trim(), text: section ? `${section.conclusion}\n${section.advice}` : "", visible: true, showChart: !!section, sectionId: section?.id, metricIds: section?.metricIds ?? [] }] }); setSelected(id); setNewModule(false); }}>添加模块</button></div></AnalysisModal>}
  </div>;
}
