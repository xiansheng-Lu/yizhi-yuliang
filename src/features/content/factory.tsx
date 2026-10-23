"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowCounterClockwise, ArrowRight, CheckCircle, Copy, DownloadSimple, FileText, FloppyDisk, FolderOpen, MagnifyingGlass, Plus, Stack, X } from "@phosphor-icons/react";
import { useWorkspace } from "@/components/provider";
import { canAccessPage } from "@/lib/access";
import type { FrontendSnapshot, Identity } from "@/lib/domain";
import { authorizedProducts, contentNames, createDocument, emptyContent, flowDocument, uid, validateContent, type ContentAsset, type ContentDocument, type ContentOwner, type ContentPage, type ContentState, type DocumentKind } from "@/lib/content-factory";
import { downloadBlob, inspectContentFile, readContentDraft, readContentFile, saveContentDraft } from "@/services/content-drafts";
import { AssetPicker, AssetPreview, Field, ImportButton, type EditorProps } from "./shared";
import { WritingWorkspace } from "./writing";
import { GraphicsWorkspace } from "./graphics";
import { VideoWorkspace } from "./video";
import { EditingWorkspace } from "./editing";
import "@/styles/content-factory.css";

export function ContentFactory({ pageId, snapshot, identity }: { pageId: ContentPage; snapshot: FrontendSnapshot; identity: Identity }) {
  const shops = snapshot.source === identity.mode ? snapshot.shops.filter(s => s.tenantId === identity.tenantId && identity.shopIds.includes(s.id)) : [];
  const [selectedShop, setSelectedShop] = useState(shops.some(s => s.id === snapshot.scope.shop_id) ? snapshot.scope.shop_id : "");
  const owner = useMemo<ContentOwner>(() => ({ mode: identity.mode, tenantId: identity.tenantId, userId: identity.userId, shopId: selectedShop }), [identity.mode, identity.tenantId, identity.userId, selectedShop]);
  const shop = shops.find(s => s.id === selectedShop);
  if (!shop) return <section className="cf-shop-prompt"><FolderOpen size={32} /><h2>选择创作所属店铺</h2><p>商品资料、内容草稿和素材文件按店铺分别保存。</p><Field label="创作店铺"><select value={selectedShop} onChange={e => setSelectedShop(e.target.value)}><option value="">请选择授权店铺</option>{shops.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field></section>;
  return <FactorySession key={`${owner.mode}:${owner.tenantId}:${owner.userId}:${owner.shopId}:${pageId}`} owner={owner} identity={identity} shopName={shop.name} snapshot={snapshot} pageId={pageId} />;
}
function FactorySession({ owner, identity, shopName, snapshot, pageId }: { owner: ContentOwner; identity: Identity; shopName: string; snapshot: FrontendSnapshot; pageId: ContentPage }) {
  const router = useRouter(), { reload } = useWorkspace();
  const products = useMemo(() => authorizedProducts(snapshot.products, owner), [snapshot.products, owner]);
  const [initialProducts] = useState(products);
  const [state, setState] = useState<ContentState>(emptyContent), [ready, setReady] = useState(false), [loadError, setLoadError] = useState(""), [selectedId, setSelectedId] = useState(""), [dirty, setDirty] = useState(false), [busy, setBusy] = useState(false), [message, setMessage] = useState(""), [listOpen, setListOpen] = useState(false), [query, setQuery] = useState(""), [assetType, setAssetType] = useState("all"), [previewId, setPreviewId] = useState("");
  const pendingFiles = useRef(new Map<string, Blob>()), undoHistory = useRef<ContentState[]>([]), busyRef = useRef(false), allowNavigation = useRef(false), importOwner = useRef(true);
  const [hasUndo, setHasUndo] = useState(false);
  const doc = state.documents.find(d => d.id === selectedId), documents = state.documents.filter(d => d.kind === pageId && d.title.toLowerCase().includes(query.toLowerCase()));
  const readFile = useCallback(async (id: string) => pendingFiles.current.get(id) ?? await readContentFile(owner, id), [owner]);
  useEffect(() => {
    importOwner.current = true;
    let cancelled = false;
    readContentDraft(owner).then(saved => {
      validateContent(saved, initialProducts);
      if (cancelled) return;
      const desired = new URL(window.location.href).searchParams.get("document"), existing = saved.documents.find(d => d.id === desired && d.kind === pageId) ?? saved.documents.find(d => d.kind === pageId);
      if (pageId !== "assets" && !existing) { const fresh = createDocument(pageId); saved = { ...saved, documents: [...saved.documents, fresh] }; setSelectedId(fresh.id); }
      else setSelectedId(existing?.id ?? "");
      setState(saved); setReady(true);
    }).catch(error => { if (!cancelled) setLoadError(error instanceof Error ? error.message : "无法读取工作区草稿。"); });
    return () => { cancelled = true; importOwner.current = false; };
  }, [owner, pageId, initialProducts]);
  useEffect(() => {
    if (!dirty && !busy) return;
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    const linkGuard = (event: MouseEvent) => { if (allowNavigation.current) return; const link = (event.target as HTMLElement).closest("a[href]") as HTMLAnchorElement | null; if (link && link.origin === window.location.origin && !link.download && link.href !== window.location.href) { event.preventDefault(); event.stopPropagation(); setMessage(busy ? "正在处理文件，请稍候再切换。" : "当前作品有未保存修改，请先保存草稿再离开。"); } };
    const scopeGuard = (event: Event) => { if ((event.target as HTMLElement).closest("#global-context")) { event.preventDefault(); event.stopPropagation(); setMessage("请先保存当前草稿，再切换经营范围。"); } };
    window.addEventListener("beforeunload", beforeUnload); document.addEventListener("click", linkGuard, true); document.addEventListener("change", scopeGuard, true);
    return () => { window.removeEventListener("beforeunload", beforeUnload); document.removeEventListener("click", linkGuard, true); document.removeEventListener("change", scopeGuard, true); };
  }, [dirty, busy]);
  function replace(next: ContentState | ((current: ContentState) => ContentState)) { setState(current => { if (undoHistory.current.at(-1) !== current) undoHistory.current = [...undoHistory.current.slice(-29), current]; return typeof next === "function" ? next(current) : next; }); setHasUndo(true); setDirty(true); }
  function update(patch: Partial<ContentDocument>) { if (!doc || busyRef.current) return; replace(current => ({ ...current, documents: current.documents.map(d => d.id === doc.id ? { ...d, ...patch, updatedAt: new Date().toISOString() } : d) })); }
  async function persist(next = state) {
    if (busyRef.current) throw new Error("当前操作尚未完成，请稍后重试。");
    busyRef.current = true; setBusy(true);
    try { validateContent(next, products); const files = new Map([...pendingFiles.current].filter(([id]) => next.assets.some(a => a.id === id))); const saved = await saveContentDraft(owner, next, files); setState(saved); setDirty(false); pendingFiles.current.clear(); setMessage("草稿与素材已保存在此浏览器。"); return saved; }
    finally { busyRef.current = false; setBusy(false); }
  }
  async function save() { try { await persist(); } catch (e) { setMessage((e as Error).message); } }
  async function importFiles(files: FileList | File[] | null) {
    if (!files?.length || busyRef.current) return [];
    busyRef.current = true; setBusy(true);
    const imported: ContentAsset[] = [];
    try {
      if (files.length > 20) throw new Error("一次最多导入 20 个文件。");
      for (const file of Array.from(files)) {
        const info = await inspectContentFile(file), asset: ContentAsset = { id: uid(), name: file.name, ...info, productId: doc?.productId ?? "", documentId: doc?.id ?? null, mime: file.type, size: file.size, createdAt: new Date().toISOString() };
        if (!importOwner.current) return [];
        imported.push(asset); pendingFiles.current.set(asset.id, file);
      }
      replace(current => ({ ...current, assets: [...current.assets, ...imported] })); setMessage(`已导入 ${imported.length} 个本地素材，请保存草稿以保留文件。`);
      return imported;
    } catch (error) { for (const asset of imported) pendingFiles.current.delete(asset.id); setMessage(error instanceof Error ? error.message : "文件导入失败。"); return []; }
    finally { busyRef.current = false; setBusy(false); }
  }
  async function flow(kind: DocumentKind) {
    if (!doc || busyRef.current) return;
    if (!canAccessPage(identity, kind)) { setMessage("当前账号未授权目标工作区。"); return; }
    const next = flowDocument(doc, kind);
    if (kind === "editing") { const video = state.assets.find(a => a.kind === "video" && (doc.nodes.some(n => n.type === "result" && n.assetId === a.id) || a.documentId === doc.id)); if (video) next.clips = [{ id: uid(), assetId: video.id, track: "video", start: 0, sourceStart: 0, duration: video.duration, text: video.name, volume: 1 }]; }
    try { await persist({ ...state, documents: [...state.documents, next] }); allowNavigation.current = true; router.push(`/app/${kind}?document=${encodeURIComponent(next.id)}`); }
    catch (e) { setMessage((e as Error).message); }
  }
  async function archive(asset?: ContentAsset) {
    if (!doc) return;
    if (asset) { replace({ ...state, assets: state.assets.map(a => a.id === asset.id ? { ...a, documentId: doc.id } : a) }); return; }
    const text = [doc.body, ...doc.scenes.map(s => `${s.title}\n${s.visual}\n${s.narration}`)].join("\n\n").trim();
    if (!text) { setMessage("先补充正文或分镜，再将文稿加入素材库。"); return; }
    const next: ContentAsset = { id: uid(), name: `${doc.title} V${doc.version}`, kind: "text", productId: doc.productId, documentId: doc.id, mime: "text/plain", size: new Blob([text]).size, duration: 0, createdAt: new Date().toISOString(), text };
    try { await persist({ ...state, assets: [...state.assets, next] }); setMessage("文稿已加入本地素材库，来源与商品关系已保留。"); } catch (e) { setMessage((e as Error).message); }
  }
  function create(kind: DocumentKind) { if (busy) return; const next = createDocument(kind, doc?.productId); replace({ ...state, documents: [...state.documents, next] }); setSelectedId(next.id); setListOpen(false); }
  function duplicate() { if (!doc) return; const next = { ...structuredClone(doc), id: uid(), parentId: doc.id, version: doc.version + 1, status: "draft" as const, updatedAt: new Date().toISOString() }; replace({ ...state, documents: [...state.documents, next] }); setSelectedId(next.id); }
  function undo() { const previous = undoHistory.current.pop(); if (previous) { setState({ ...previous, revision: state.revision }); if (!previous.documents.some(d => d.id === selectedId)) setSelectedId(previous.documents.find(d => d.kind === pageId)?.id ?? ""); setDirty(true); setMessage("已撤销上一步修改。"); } setHasUndo(undoHistory.current.length > 0); }
  if (loadError) return <div className="cf-shop-prompt" role="alert"><h2>暂时无法打开草稿</h2><p>{loadError}</p><button className="button secondary" onClick={() => window.location.reload()}>重新读取</button><button className="button secondary" onClick={() => { void readContentDraft(owner).then(raw => downloadBlob(new Blob([JSON.stringify(raw)], { type: "application/json" }), "原始草稿备份.json")).catch(e => setLoadError((e as Error).message)); }}>下载原始草稿备份</button></div>;
  if (!ready) return <div className="cf-loading" role="status"><span className="loading-ring" />正在打开创作工作区…</div>;
  const editorProps: EditorProps = { doc: doc!, state, owner, products, update, flow: kind => void flow(kind), archive, importFiles, file: readFile, notify: setMessage };
  const parent = state.documents.find(d => d.id === doc?.parentId), preview = state.assets.find(a => a.id === previewId);
  return <div className={`cf-factory ${busy ? "cf-busy" : ""}`} aria-label={`${contentNames[pageId]}专业工作区`}>
    <div className="cf-session-bar"><div className="cf-inline"><span className="cf-owner"><FolderOpen size={15} />{shopName}</span><span className="cf-save-state"><i className={dirty ? "unsaved" : ""} />{busy ? "正在处理…" : dirty ? "有未保存修改" : "本地草稿"}</span><small>仅保存在此浏览器</small></div><div className="cf-inline"><button className="cf-tool" disabled={!hasUndo || busy} onClick={undo}><ArrowCounterClockwise size={15} />撤销</button><button className="cf-tool" disabled={busy} onClick={() => downloadBlob(new Blob([JSON.stringify({ owner, ...state }, null, 2)], { type: "application/json" }), "驭量内容草稿备份.json")}><DownloadSimple size={15} />备份文稿与工程</button><button className="button" disabled={busy} onClick={() => void save()}><FloppyDisk size={16} />保存草稿</button></div></div>
    {message && <div className="cf-feedback" role="status"><span>{message}</span><button className="cf-tool" aria-label="关闭提示" onClick={() => setMessage("")}><X size={15} /></button></div>}
    {pageId === "assets" ? <><div className="cf-assets-heading"><div><span className="cf-kicker">CONTENT LIBRARY</span><h2>每一份创作，都有来处</h2><p>归档图片、视频、音频与文稿，继续创作时直接取用。</p></div><ImportButton busy={busy} onFiles={files => void importFiles(files)} /></div><div className="cf-library-filters"><label><MagnifyingGlass size={16} /><input aria-label="搜索素材" placeholder="搜索素材名称" value={query} onChange={e => setQuery(e.target.value)} /></label><div>{[["all", "全部"], ["image", "图片"], ["video", "视频"], ["audio", "音频"], ["text", "文稿"]].map(([id, name]) => <button key={id} aria-pressed={assetType === id} onClick={() => setAssetType(id)}>{name}</button>)}</div><small>{state.assets.length} 份本地素材</small></div><AssetPicker state={{ ...state, assets: state.assets.filter(a => (assetType === "all" || a.kind === assetType) && a.name.toLowerCase().includes(query.toLowerCase())) }} owner={owner} file={readFile} select={a => setPreviewId(a.id)} />
      <div className="cf-snapshot-assets"><h3>已关联的业务素材</h3><p>以下记录来自素材服务，未提供文件链接的记录只能查看元数据。</p>{snapshot.materials.filter(a => a.tenantId === owner.tenantId && products.some(p => p.id === a.productId)).map(a => <div key={a.id}><Stack size={16} /><strong>{a.name}</strong><span>{a.kind} · {a.version}</span><small>{a.status === "READY" ? "业务状态：可用" : a.status === "REVIEW" ? "业务状态：审核中" : "业务状态：草稿"}</small></div>)}</div>
      {preview && <section className="cf-asset-detail"><header><h3>{preview.name}</h3><button className="cf-tool" aria-label="关闭素材详情" onClick={() => setPreviewId("")}><X size={16} /></button></header><div className="cf-detail-preview"><AssetPreview asset={preview} owner={owner} file={readFile} /></div><div className="cf-detail-meta"><span>关联商品：{products.find(p => p.id === preview.productId)?.name ?? "未关联"}</span><span>来源作品：{state.documents.find(d => d.id === preview.documentId)?.title ?? "本地导入"}</span><span>文件大小：{(preview.size / 1024 / 1024).toFixed(2)} MB</span></div><footer><button className="button secondary" onClick={() => { void (async () => { try { const blob = preview.kind === "text" ? new Blob([preview.text ?? ""], { type: "text/plain;charset=utf-8" }) : await readFile(preview.id); if (!blob) throw new Error("本地文件不可用，请重新导入。"); downloadBlob(blob, preview.kind === "text" ? `${preview.name}.txt` : preview.name); } catch (e) { setMessage((e as Error).message); } })(); }}><DownloadSimple size={15} />下载原文件</button>{preview.kind === "video" && canAccessPage(identity, "editing") && <button className="button" disabled={busy} onClick={() => { const next = createDocument("editing", preview.productId); next.title = `${preview.name} · 剪辑`; next.clips = [{ id: uid(), assetId: preview.id, track: "video", start: 0, sourceStart: 0, duration: preview.duration, text: preview.name, volume: 1 }]; void persist({ ...state, documents: [...state.documents, next] }).then(() => { allowNavigation.current = true; router.push(`/app/editing?document=${next.id}`); }).catch(e => setMessage((e as Error).message)); }}>用此素材剪辑<ArrowRight size={15} /></button>}</footer></section>}
    </> : doc ? <>
      <div className="cf-project-bar"><button className="cf-project-trigger" aria-expanded={listOpen} onClick={() => setListOpen(!listOpen)}><FolderOpen size={18} /><span>项目 / {contentNames[pageId]}</span><small>{state.documents.filter(d => d.kind === pageId).length}</small></button><input className="cf-project-title" aria-label="作品名称" value={doc.title} disabled={busy} maxLength={120} onChange={e => update({ title: e.target.value })} /><span className="cf-version">V{doc.version}</span><button className="cf-tool" onClick={duplicate} disabled={busy}><Copy size={15} />新版本</button><button className="cf-tool" onClick={() => create(pageId)} disabled={busy}><Plus size={15} />新建</button></div>
      {listOpen && <section className="cf-project-list"><label className="cf-project-search"><MagnifyingGlass size={15} /><input aria-label="搜索内容项目" value={query} placeholder="查找项目" onChange={e => setQuery(e.target.value)} /></label>{documents.map(d => <button key={d.id} onClick={() => { setSelectedId(d.id); setListOpen(false); }} aria-pressed={d.id === doc.id}><FileText size={17} /><strong>{d.title}</strong><span>V{d.version} · {products.find(p => p.id === d.productId)?.name ?? "未关联商品"}</span><small>{new Date(d.updatedAt).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</small></button>)}</section>}
      <div className="cf-context-bar"><label>关联商品<select aria-label="作品关联商品" value={doc.productId} onChange={e => update({ productId: e.target.value })}><option value="">暂不关联</option>{products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label><span>{parent ? `来源：${contentNames[parent.kind]} / ${parent.title}` : "独立创作"}</span>{(pageId === "planning" || pageId === "scripts") && <button className="cf-tool" disabled={busy} onClick={() => void archive()}><CheckCircle size={15} />文稿入库</button>}</div>
      <fieldset className="cf-editor-lock" disabled={busy}>{pageId === "planning" || pageId === "scripts" ? <WritingWorkspace key={doc.id} {...editorProps} /> : pageId === "graphics" ? <GraphicsWorkspace key={doc.id} {...editorProps} /> : pageId === "videos" ? <VideoWorkspace key={doc.id} {...editorProps} /> : <EditingWorkspace key={doc.id} {...editorProps} />}</fieldset>
      <div className="cf-local-footnote"><FloppyDisk size={13} />草稿、工程与导入文件保存在当前浏览器；备份 JSON 包含文稿与工程参数，媒体文件请在素材库单独下载。<button onClick={() => { if (!dirty) void reload(); else setMessage("请先保存当前修改，再刷新业务资料。"); }}>刷新商品资料</button></div>
    </> : null}
  </div>;
}
