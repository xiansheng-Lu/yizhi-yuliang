"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowRight, ArrowUp, FileText, FilmSlate, ImageSquare, Paperclip, Sparkle, UploadSimple, X } from "@phosphor-icons/react";
import { uid } from "@/lib/content-factory";
import type { ContentAsset, ContentDocument, ContentOwner, ContentState, DocumentKind } from "@/lib/content-factory";
import type { Product } from "@/lib/domain";
import { readContentFile } from "@/services/content-drafts";

export interface EditorProps {
  doc: ContentDocument; state: ContentState; owner: ContentOwner; products: Product[];
  update: (patch: Partial<ContentDocument>) => void;
  flow: (kind: DocumentKind) => void; archive: (asset?: ContentAsset) => Promise<void>;
  importFiles: (files: FileList | File[] | null) => Promise<ContentAsset[]>;
  file: (id: string) => Promise<Blob | undefined>; notify: (message: string) => void;
}
export function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="cf-field"><span>{label}</span>{children}</label>; }
export function PaneTitle({ title, detail, action }: { title: string; detail?: string; action?: ReactNode }) { return <header className="cf-pane-title"><div><h2>{title}</h2>{detail && <small>{detail}</small>}</div>{action}</header>; }
export function ImportButton({ onFiles, accept = "image/*,video/*,audio/*", label = "导入素材", busy = false }: { onFiles: (files: FileList | null) => void; accept?: string; label?: string; busy?: boolean }) { return <label className={`cf-import ${busy ? "busy" : ""}`}><UploadSimple size={16} />{busy ? "正在导入…" : label}<input type="file" aria-label={label} accept={accept} multiple disabled={busy} onChange={e => { onFiles(e.target.files); e.target.value = ""; }} /></label>; }
export function useFileUrl(owner: ContentOwner, asset?: ContentAsset, pendingFile?: (id: string) => Promise<Blob | undefined>) {
  const [result, setResult] = useState({ id: "", url: "", error: "" });
  const id = asset?.id ?? "";
  useEffect(() => {
    if (!id) return;
    let cancelled = false, objectUrl = "";
    (pendingFile ? pendingFile(id) : readContentFile(owner, id)).then(blob => {
      if (cancelled) return;
      if (!blob) { setResult({ id, url: "", error: "本地文件不可用，请重新导入。" }); return; }
      objectUrl = URL.createObjectURL(blob); setResult({ id, url: objectUrl, error: "" });
    }).catch(() => { if (!cancelled) setResult({ id, url: "", error: "素材读取失败，请重新导入。" }); });
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [id, owner, pendingFile]);
  return result.id === id ? result : { id, url: "", error: "" };
}
export function AssetPreview({ asset, owner, file, controls = true }: { asset?: ContentAsset; owner: ContentOwner; file?: EditorProps["file"]; controls?: boolean }) {
  const { url, error } = useFileUrl(owner, asset, file);
  if (!asset) return <div className="cf-empty"><FilmSlate size={30} /><strong>作品将在这里呈现</strong><p>从素材库选择文件，或导入本地素材开始创作。</p></div>;
  if (asset.kind === "text") return <div className="cf-text-preview">{asset.text}</div>;
  if (error) return <div className="cf-empty" role="alert"><strong>{error}</strong></div>;
  if (!url) return <div className="cf-empty" role="status">正在读取素材…</div>;
  // Native media supports user-imported Blob URLs without a remote image proxy.
  /* eslint-disable @next/next/no-img-element */
  return asset.kind === "image" ? <img src={url} alt={asset.name} draggable={false} /> : asset.kind === "video" ? <video src={url} controls={controls} preload="metadata" playsInline aria-label={asset.name} /> : <audio src={url} controls={controls} aria-label={asset.name} />;
  /* eslint-enable @next/next/no-img-element */
}
export function AssetPicker({ state, owner, kind, select, file }: { state: ContentState; owner: ContentOwner; kind?: ContentAsset["kind"]; select: (asset: ContentAsset) => void; file: EditorProps["file"] }) {
  const assets = state.assets.filter(a => !kind || a.kind === kind);
  return <div className="cf-asset-picker">{assets.length ? assets.map(asset => <button key={asset.id} className="cf-asset-tile" onClick={() => select(asset)}><div className="cf-asset-thumb">{asset.kind === "image" ? <AssetPreview asset={asset} owner={owner} file={file} controls={false} /> : asset.kind === "video" ? <FilmSlate size={24} /> : <FileText size={24} />}</div><strong>{asset.name}</strong><small>{asset.kind === "video" || asset.kind === "audio" ? `${asset.duration.toFixed(1)} 秒` : asset.kind === "text" ? "文本" : "图片"}</small></button>) : <div className="cf-empty compact"><ImageSquare size={25} /><p>还没有可用素材，先导入文件。</p></div>}</div>;
}
export function AssistantPanel({ doc, update, children, title = "创作助手", context = "当前作品", onImport, tabs, panelContent }: Pick<EditorProps, "doc" | "update"> & { children?: ReactNode; title?: string; context?: string; onImport?: EditorProps["importFiles"]; tabs?: ReactNode; panelContent?: ReactNode }) {
  const [notice, setNotice] = useState(""), [references, setReferences] = useState<ContentAsset[]>([]);
  const messages = doc.messages ?? [];
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [messages.length]);
  function send() { const text = doc.prompt.trim(); if (!text) return; update({ messages: [...messages, { id: uid(), text, context: context + (references.length ? ` · 参考：${references.map(a => a.name).join("、")}` : ""), assetIds: references.map(a => a.id), createdAt: new Date().toISOString() }], prompt: "" }); setReferences([]); setNotice("创作要求已记录。生成服务接入后可执行，当前没有启动生成任务。"); }
  return <aside className="cf-assistant" aria-label={`${title}对话区`}><PaneTitle title={title} detail="对话创作" action={<Sparkle size={18} />} />{tabs}
    <div className="cf-chat-scroll">{panelContent ?? <div className="cf-assistant-body">{children}{!messages.length && <div className="cf-assistant-intro"><span className="cf-ai-mark"><Sparkle size={21} weight="fill" /></span><h3>想做什么，直接聊聊</h3><p>描述你的想法，也可以导入参考素材。对话与当前作品保存在一起。</p><div className="cf-prompt-chips">{(doc.kind === "graphics" ? ["设计一组商品图文", "调整当前画板的版式"] : doc.kind === "videos" ? ["把脚本变成视频", "根据参考素材设计镜头"] : doc.kind === "editing" ? ["把口播剪得更紧凑", "添加重点字幕和配乐"] : ["梳理选题与内容方向"]).map(text => <button key={text} onClick={() => update({ prompt: text })}>{text}</button>)}</div></div>}
    {messages.map(message => <article className="cf-chat-message" key={message.id}><header><b>你</b><time>{new Date(message.createdAt).toLocaleTimeString("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit" })}</time></header><p>{message.text}</p><small>{message.context}</small></article>)}<div ref={end} /></div>}</div>
    {notice && <div className="cf-chat-notice" role="status"><span>{notice}</span><button className="cf-tool" aria-label="关闭对话提示" onClick={() => setNotice("")}><X size={13} /></button></div>}
    <div className="cf-chat-context"><span><Paperclip size={13} />{context}</span></div><div className="cf-composer">{references.length > 0 && <div className="cf-reference-chips">{references.map(asset => <span key={asset.id}><Paperclip size={12} />{asset.name}<button className="cf-tool" aria-label={`移除参考 ${asset.name}`} onClick={() => setReferences(references.filter(a => a.id !== asset.id))}><X size={11} /></button></span>)}</div>}<textarea aria-label="创作对话输入" placeholder="描述你想创作或修改的内容…" value={doc.prompt} onChange={e => update({ prompt: e.target.value })} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} /><footer><div>{onImport && <ImportButton label="添加参考" onFiles={files => void onImport(files).then(assets => setReferences(current => [...current, ...assets]))} />}<small>Enter 发送 · Shift+Enter 换行</small></div><button className="cf-chat-send" aria-label="发送创作要求" disabled={!doc.prompt.trim()} onClick={send}><ArrowUp size={18} /></button></footer></div><div className="cf-chat-service"><i />AI 服务待接入 · 对话可保存</div></aside>;
}
export function FlowButton({ label, onClick }: { label: string; onClick: () => void }) { return <button className="cf-flow-button" onClick={onClick}>{label}<ArrowRight size={15} /></button>; }
