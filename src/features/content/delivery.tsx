"use client";
import { useEffect, useRef, useState } from "react";
import { CheckCircle, LinkSimple, MagnifyingGlass, X } from "@phosphor-icons/react";
import { advertisingProducts, type AdvertisingProduct } from "@/lib/advertising";
import { accountLinkKey, contentDeliveryAccounts, type ContentDeliveryAccount } from "@/lib/content-delivery";
import type { ContentAsset, ContentOwner } from "@/lib/content-factory";
import type { FrontendSnapshot, Identity } from "@/lib/domain";
import type { FrontendService } from "@/services/contracts";
import { PlatformService } from "@/services/platform-service";
import { AssetPreview, Field, ImportButton, type EditorProps } from "./shared";

export function ContentDelivery({ owner, identity, snapshot, service, assets, initialAssetId, file, importFiles, busy, onSave, onClose }: {
  owner: ContentOwner; identity: Identity; snapshot: FrontendSnapshot; service: FrontendService | null;
  assets: ContentAsset[]; initialAssetId: string; file: EditorProps["file"]; importFiles: EditorProps["importFiles"]; busy: boolean;
  onSave: (assetId: string, additions: ContentDeliveryAccount[], removals: string[]) => Promise<void>; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [assetId, setAssetId] = useState(initialAssetId), [product, setProduct] = useState<AdvertisingProduct>("adq"), [query, setQuery] = useState(""), [refresh, setRefresh] = useState(0);
  const [catalog, setCatalog] = useState<{ product: string; accounts: ContentDeliveryAccount[]; error: string; loading: boolean }>({ product: "", accounts: [], error: "", loading: true });
  const [additions, setAdditions] = useState<ContentDeliveryAccount[]>([]), [removals, setRemovals] = useState<string[]>([]), [saving, setSaving] = useState(false), [error, setError] = useState("");
  const asset = assets.find(a => a.id === assetId), locked = busy || saving;
  const existing = asset?.accountLinks ?? [], retained = existing.filter(a => !removals.includes(accountLinkKey(a))), selected = [...retained, ...additions.filter(a => !retained.some(l => accountLinkKey(l) === accountLinkKey(a)))];
  const loading = catalog.product !== product || catalog.loading;
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => element?.close(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const source = owner.mode === "MOCK" ? snapshot : service ? await new PlatformService(service, identity).context(product, controller.signal) : null;
        if (!source) throw new Error("投放账号服务尚未连接，请检查接口配置。");
        const accounts = contentDeliveryAccounts(owner, identity, product, source);
        if (!controller.signal.aborted) setCatalog({ product, accounts, error: "", loading: false });
      } catch (e) { if (!controller.signal.aborted) setCatalog({ product, accounts: [], error: (e as Error).message, loading: false }); }
    }
    void load(); return () => controller.abort();
  }, [owner, identity, snapshot, service, product, refresh]);
  function remove(key: string) { setAdditions(current => current.filter(a => accountLinkKey(a) !== key)); if (existing.some(a => accountLinkKey(a) === key)) setRemovals(current => [...new Set([...current, key])]); }
  function toggle(account: ContentDeliveryAccount) {
    const key = accountLinkKey(account);
    if (selected.some(a => accountLinkKey(a) === key)) remove(key);
    else { setRemovals(current => current.filter(k => k !== key)); if (!existing.some(a => accountLinkKey(a) === key)) setAdditions(current => [...current, account]); }
  }
  async function save() {
    if (!asset || locked) return;
    setSaving(true); setError("");
    try {
      // Re-read selected product lines before recording a new association.
      const fresh = (await Promise.all([...new Set(additions.map(a => a.productLine))].map(async line => {
        const source = owner.mode === "MOCK" ? snapshot : service ? await new PlatformService(service, identity).context(line) : null;
        return source ? contentDeliveryAccounts(owner, identity, line, source) : [];
      }))).flat();
      const checked = additions.map(a => { const match = fresh.find(candidate => accountLinkKey(candidate) === accountLinkKey(a)); if (!match) throw new Error("所选账号的授权或店铺关系已变化，请刷新账号后重新选择。"); return match; });
      await onSave(asset.id, checked, removals); onClose();
    } catch (e) { setError((e as Error).message); } finally { setSaving(false); }
  }
  return <dialog className="cf-delivery-dialog" ref={dialog} aria-labelledby="cf-delivery-title" onCancel={e => { e.preventDefault(); if (!locked) onClose(); }}>
    <header className="cf-delivery-header"><div className="cf-delivery-icon"><LinkSimple size={22} /></div><div><small>内容交付</small><h2 id="cf-delivery-title">关联投放账号</h2></div><button className="cf-tool" aria-label="关闭账号关联" disabled={locked} onClick={onClose}><X size={20} /></button></header>
    <div className="cf-delivery-body"><section className="cf-delivery-artifact"><div className="cf-delivery-step"><b>01</b><h3>选择交付成品</h3></div><Field label="成品素材"><select value={assetId} disabled={locked} onChange={e => { setAssetId(e.target.value); setAdditions([]); setRemovals([]); setError(""); }}><option value="">请选择已入库的成品</option>{assets.filter(a => a.kind !== "audio").map(a => <option key={a.id} value={a.id}>{a.name} · {new Date(a.createdAt).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })}</option>)}</select></Field><div className="cf-delivery-preview">{asset ? <AssetPreview asset={asset} owner={owner} file={file} /> : <div className="cf-empty"><CheckCircle size={30} /><strong>先把创作变成成品</strong><p>图片可在画板中入库；视频请导入完成后的成片；文稿可在项目中入库。</p></div>}</div><ImportButton label="导入成品文件" accept="image/*,video/*" busy={locked} onFiles={files => { void importFiles(files).then(imported => { if (imported[0]) { setAssetId(imported[0].id); setAdditions([]); setRemovals([]); } }); }} /><p className="cf-delivery-caption">关联当前选中的文件版本。继续编辑后，请将新成品入库并重新关联。</p></section>
    <section className="cf-delivery-accounts"><div className="cf-delivery-step"><b>02</b><h3>选择投放账号</h3><span>可多选</span></div><Field label="投放平台"><select value={product} disabled={locked} onChange={e => { setProduct(e.target.value as AdvertisingProduct); setQuery(""); }}>{Object.entries(advertisingProducts).map(([id, p]) => <option key={id} value={id}>{p.name}</option>)}</select></Field><label className="cf-delivery-search"><MagnifyingGlass size={16} /><input aria-label="搜索投放账号" placeholder="搜索账号名称或 ID" disabled={locked} value={query} onChange={e => setQuery(e.target.value)} /></label><div className="cf-delivery-list" aria-label="可关联投放账号" aria-busy={loading}>{loading ? <p role="status">正在读取账号授权…</p> : catalog.error ? <p role="alert">{catalog.error}</p> : catalog.accounts.filter(a => `${a.accountName} ${a.nativeId}`.toLowerCase().includes(query.toLowerCase())).length ? catalog.accounts.filter(a => `${a.accountName} ${a.nativeId}`.toLowerCase().includes(query.toLowerCase())).map(account => <label className="cf-delivery-account" key={accountLinkKey(account)}><input type="checkbox" disabled={locked || !asset} checked={selected.some(a => accountLinkKey(a) === accountLinkKey(account))} onChange={() => toggle(account)} /><span className="cf-account-monogram">{advertisingProducts[account.productLine].short.slice(0, 1)}</span><span><strong>{account.accountName}</strong><small>{account.nativeId}</small></span><em>{owner.mode === "MOCK" ? "演示账号" : "已授权"}</em></label>) : <div className="cf-empty"><strong>{query ? "没有匹配的账号" : "当前店铺暂无可关联账号"}</strong><p>{query ? "尝试其他名称或账号 ID。" : "请先在连接管理完成账号授权和店铺关联。"}</p></div>}</div><button className="cf-tool" disabled={locked || loading} onClick={() => { setCatalog({ product: "", accounts: [], error: "", loading: true }); setRefresh(v => v + 1); }}>刷新账号</button>
    <div className="cf-delivery-selection"><strong>已选 {selected.length} 个账号</strong>{selected.length ? selected.map(account => <div key={accountLinkKey(account)}><span><b>{account.accountName}</b><small>{advertisingProducts[account.productLine].short} · {account.nativeId}{"linkedAt" in account ? " · 本地关联" : " · 待保存"}</small></span><button className="cf-tool" aria-label={`移除关联：${account.accountName}`} disabled={locked} onClick={() => remove(accountLinkKey(account))}><X size={15} /></button></div>) : <p>可跨平台选择多个账号。</p>}</div></section></div>
    <footer className="cf-delivery-footer"><div><strong>{owner.mode === "MOCK" ? "演示关联 · 仅本地保存" : "本地关联 · 待同步"}</strong><p>保存成品与账号的关联记录；平台上传、审核及投放需在接入后继续完成。</p>{error && <p className="cf-delivery-error" role="alert">{error}</p>}</div><button className="button secondary" disabled={locked} onClick={onClose}>取消</button><button className="button" disabled={locked || !asset || (!additions.length && !removals.length)} onClick={() => void save()}><LinkSimple size={16} />{locked ? "正在保存…" : "保存关联"}</button></footer>
  </dialog>;
}
