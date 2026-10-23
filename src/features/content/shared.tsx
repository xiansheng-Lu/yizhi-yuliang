"use client";
import { useEffect, useState, type ReactNode } from "react";
import { ArrowRight, FileText, FilmSlate, ImageSquare, Sparkle, UploadSimple } from "@phosphor-icons/react";
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
export function AssistantPanel({ doc, update, children, title = "创作助手" }: Pick<EditorProps, "doc" | "update"> & { children?: ReactNode; title?: string }) {
  return <aside className="cf-assistant"><PaneTitle title={title} detail="把创作意图留在作品旁" action={<Sparkle size={18} />} /><div className="cf-assistant-body">{children}<div className="cf-assistant-intro"><span className="cf-ai-mark"><Sparkle size={20} weight="fill" /></span><h3>从一个清楚的想法开始</h3><p>描述目标、风格与修改要求。商品资料和当前作品将作为创作上下文。</p></div><div className="cf-capability-note"><i />AI 创作服务待接入。当前可手动编辑、保存草稿和导出文件。</div></div><div className="cf-composer"><textarea aria-label="创作指令" placeholder="例如：面向办公室人群，突出便携和冲泡方便…" value={doc.prompt} onChange={e => update({ prompt: e.target.value })} /><footer><small>指令随草稿保存</small><button className="button" disabled title="生成接口接入后开放"><Sparkle size={14} />生成</button></footer></div></aside>;
}
export function FlowButton({ label, onClick }: { label: string; onClick: () => void }) { return <button className="cf-flow-button" onClick={onClick}>{label}<ArrowRight size={15} /></button>; }
