"use client";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { ArrowClockwise, ArrowDown, ArrowRight, ArrowUp, ArrowSquareOut, Broadcast, ChartBar, CheckCircle, Clock, DownloadSimple, FloppyDisk, ImageSquare, Info, MagnifyingGlass, Package, Plus, Robot, ShieldCheck, ShoppingBag, Sparkle, Stack, Trash, WarningCircle } from "@phosphor-icons/react";
import { useWorkspace } from "@/components/provider";
import { money, type FrontendSnapshot, type Identity } from "@/lib/domain";
import { arrangeSequentially, conversionRate, draftKey, itemKey, parseDraft, productKey, productPerformance, productTotals, reorderItems, ruleAdvice, safeProductImage, skuRoi, stockRisk, timelineIssues, validCount, validTimestamp, type DraftOwner, type LineupDraft, type LineupItem, type ProductLineup, type ProductPerformance, type WorkspaceProduct } from "@/lib/products-workspace";
import { buildProductWorkspace } from "@/services/products-adapter";
import "@/styles/products.css";

type Mode = "prepare" | "live" | "review";
const modes = [{ id: "prepare", label: "备播", detail: "准备好商品，排好每一次讲解。" }, { id: "live", label: "直播中", detail: "跟进商品表现，及时调整讲品节奏。" }, { id: "review", label: "直播后", detail: "复盘成交贡献，为下一场积累依据。" }] as const;
const integer = (value: number | null | undefined) => validCount(value) ? value.toLocaleString("zh-CN") : "--";
const currency = (value: number | null | undefined) => validCount(value) ? money(value) : "--";
const minute = (value: number) => `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
const timestamp = (value?: string | null) => validTimestamp(value) ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "未提供";
const isStale = (value: string | undefined | null, now: number) => validTimestamp(value) && now - Date.parse(value) > 300_000;

function Section({ title, detail, action, children, className = "" }: { title: string; detail?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`pw-panel ${className}`} aria-label={title}><div className="pw-panel-title"><div><h2>{title}</h2>{detail && <p>{detail}</p>}</div>{action}</div>{children}</section>;
}
function Missing({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="pw-missing"><Package size={27} /><strong>{title}</strong><p>{children}</p></div>;
}
function ProductImage({ product, large = false }: { product: WorkspaceProduct; large?: boolean }) {
  const [failed, setFailed] = useState<string | null>(null);
  const url = safeProductImage(product.imageUrl);
  return <span className={`pw-photo ${large ? "large" : ""}`}>{url && failed !== url ?
    // Merchant/CDN URLs are dynamic. Native img avoids silently excluding image hosts.
    // eslint-disable-next-line @next/next/no-img-element
    <img src={url} alt={product.name} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(url)} /> : <><ImageSquare size={large ? 34 : 22} /><small>暂无图片</small></>}</span>;
}
function Stock({ product }: { product: WorkspaceProduct }) {
  const risk = stockRisk(product);
  return <span className={`pw-stock ${risk}`}><i />{risk === "unknown" ? "库存未提供" : risk === "out" ? "已售罄" : risk === "low" ? `仅剩 ${product.stock} 件` : `${integer(product.stock)} 件`}</span>;
}
export function ProductsSkeleton() {
  return <div className="products-workspace pw-loading" aria-label="正在加载商品工作区" role="status"><div className="pw-skeleton heading" /><div className="pw-metrics">{[0, 1, 2, 3].map((n) => <div key={n} className="pw-skeleton metric" />)}</div><div className="pw-main-grid"><div className="pw-skeleton list" /><div className="pw-skeleton list" /></div><span className="sr-only">正在读取店铺、商品及直播货盘</span></div>;
}
export function ProductsPage() {
  const { data, lastValidSnapshot, identity, reload } = useWorkspace();
  if (!identity) return <ProductsSkeleton />;
  const snapshot = data.status === "ready" ? data.data : lastValidSnapshot;
  if (!snapshot) {
    if (data.status !== "error") return <ProductsSkeleton />;
    return <div className="products-workspace"><h1>商品与货盘</h1><Section title={data.error.code === "API_NOT_CONFIGURED" ? "商品数据未接入" : data.error.code === "NO_DATA" ? "暂无商品数据" : "商品请求失败"}><Missing title={data.error.message}>请检查小店授权和数据服务后重试。</Missing><button className="pw-button" onClick={() => void reload()}><ArrowClockwise size={15} />重新读取</button></Section></div>;
  }
  return <ProductsWorkspace key={`${identity.mode}:${identity.tenantId}:${identity.userId}`} snapshot={snapshot} identity={identity} />;
}

function ProductsWorkspace({ snapshot, identity }: { snapshot: FrontendSnapshot; identity: Identity }) {
  const { data, reload } = useWorkspace();
  const model = useMemo(() => buildProductWorkspace(snapshot, identity), [snapshot, identity]);
  const [shopId, setShopId] = useState(() => model.shops.find((shop) => shop.id === snapshot.scope.shop_id)?.id ?? model.shops[0]?.id ?? "");
  const shop = model.shops.find((value) => value.id === shopId);
  const sessions = model.sessions.filter((session) => session.shopId === shop?.id);
  const [liveId, setLiveId] = useState(() => model.sessions.find((session) => session.shopId === shopId && session.id === snapshot.scope.live_session_id)?.id ?? model.sessions.find((session) => session.shopId === shopId)?.id ?? "");
  const session = sessions.find((value) => value.id === liveId);
  const [mode, setMode] = useState<Mode>(() => session?.status === "ENDED" ? "review" : session?.status === "LIVE" || session?.status === "MOCK_LIVE" ? "live" : "prepare");
  const [refreshing, setRefreshing] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 30_000); return () => clearInterval(timer); }, []);
  const [switcher, setSwitcher] = useState<{ shopId: string; liveId: string } | null>(null);
  const beforeSwitchRef = useRef<() => boolean>(() => true);
  const products = model.products.filter((product) => product.shopId === shop?.id);
  const matchingLineups = model.lineups.filter((lineup) => lineup.shopId === shop?.id && lineup.liveSessionId === session?.id);
  const lineup = matchingLineups.length === 1 ? matchingLineups[0] : undefined;
  const actualProducts = lineup ? lineup.items.flatMap((item) => products.filter((product) => productKey(product) === itemKey(item))) : [];
  const totals = productTotals(actualProducts, snapshot.scope.date, session?.id ?? "", !!lineup);
  const demo = snapshot.source === "MOCK";
  const stale = !demo && (isStale(lineup?.sourceUpdatedAt, now) || actualProducts.some((product) => isStale(product.sourceUpdatedAt, now) || isStale(productPerformance(product, "LIVE_SESSION", snapshot.scope.date, session?.id ?? "")?.sourceUpdatedAt, now)));
  const owner: DraftOwner = { source: snapshot.source, tenantId: identity.tenantId, userId: identity.userId, shopId: shop?.id ?? "", liveSessionId: session?.id ?? "", date: snapshot.scope.date };
  function switchScope(next: { shopId: string; liveId: string }) {
    if (!beforeSwitchRef.current()) { setSwitcher(next); return; }
    setShopId(next.shopId); setLiveId(next.liveId);
  }
  async function refresh() {
    if (refreshing) return;
    setRefreshing(true);
    try { await reload(); } finally { setRefreshing(false); }
  }
  return <div className="products-workspace">
    <header className="pw-heading"><div><span className="pw-kicker"><ShoppingBag size={15} />直播运营</span><h1>商品与货盘</h1><p>{modes.find((item) => item.id === mode)?.detail}</p></div><div className="pw-heading-actions"><Link className="pw-button" href="/app/monitor"><Broadcast size={16} />直播作战室<ArrowSquareOut size={13} /></Link><button className="pw-button" disabled={refreshing} onClick={() => void refresh()}><ArrowClockwise size={16} />{refreshing ? "读取中…" : "刷新数据"}</button></div></header>
    <div className="pw-context"><div className="pw-filter-group"><label>店铺<select aria-label="货盘店铺" value={shop?.id ?? ""} onChange={(event) => switchScope({ shopId: event.target.value, liveId: model.sessions.find((session) => session.shopId === event.target.value)?.id ?? "" })}><option value="" disabled>请选择已授权店铺</option>{model.shops.map((shop) => <option key={shop.id} value={shop.id}>{shop.name}</option>)}</select></label><label>直播场次<select aria-label="货盘直播场次" value={session?.id ?? ""} onChange={(event) => switchScope({ shopId, liveId: event.target.value })}><option value="" disabled>暂无直播场次</option>{sessions.map((session) => <option key={session.id} value={session.id}>{session.name}</option>)}</select></label><span className="pw-date">统计日 {snapshot.scope.date}<small>Asia/Shanghai</small></span></div><div className="pw-modes" role="group" aria-label="工作模式">{modes.map((item) => <button key={item.id} aria-pressed={mode === item.id} className={mode === item.id ? "active" : ""} onClick={() => setMode(item.id)}>{item.label}</button>)}</div></div>
    <div className={`pw-data-strip ${demo ? "demo" : stale ? "warning" : ""}`}><span><i />{demo ? "Mock 演示 · 示例商品与图片" : data.status === "error" ? "请求失败 · 保留上次数据" : stale ? "数据已过期 · 请刷新后核对" : "正式 API 数据"}</span><span>{session ? `${demo ? "演示场次" : ({ LIVE: "直播中", ENDED: "已结束", NOT_STARTED: "未开播", UNKNOWN: "状态未知", MOCK_LIVE: "状态未知" }[session.status])} · ` : "未选择场次 · "}{lineup ? `货盘来源 ${timestamp(lineup.sourceUpdatedAt)}` : "正式货盘未接入"}{refreshing ? " · 正在读取" : ""}</span></div>
    {data.status === "error" && <p className="pw-feedback error" role="alert">{data.error.message} 当前数值为上次成功读取的结果。</p>}
    {model.rejected && <p className="pw-feedback error" role="alert">数据源与账号模式不匹配，已阻止展示。</p>}
    <div className="pw-metrics" aria-label="本场商品核心指标">{[
      { label: "本场商品数", value: integer(totals.count), unit: "款 / SKU", note: lineup ? "来自已读取货盘，不含本地增删" : "待提供完整场次货盘", icon: Stack },
      { label: "商品支付 GMV", value: currency(totals.gmv), note: "本场订单实付口径", icon: ChartBar },
      { label: "成交商品数", value: integer(totals.sold), unit: "款 / SKU", note: "本场实售件数大于 0 的商品", icon: ShoppingBag },
      { label: "库存预警", value: integer(totals.risk), unit: "款 / SKU", note: "当前库存低于预警线或售罄", icon: WarningCircle },
    ].map((metric) => <article key={metric.label} className="pw-metric"><div><span>{metric.label}</span><metric.icon size={18} /></div><strong>{metric.value}<small>{metric.unit}</small></strong><p>{metric.note}</p></article>)}</div>
    {shop ? <DraftWorkspace key={draftKey(owner)} owner={owner} products={products} lineup={lineup} snapshot={snapshot} mode={mode} now={now} beforeSwitchRef={beforeSwitchRef} /> : <Section title="选择经营店铺"><Missing title="暂无已授权店铺">请先在用户中心确认小店授权范围。</Missing></Section>}
    {switcher && <div className="pw-dialog-backdrop"><section className="pw-dialog" role="alertdialog" aria-modal="true" aria-labelledby="pw-switch-title"><h2 id="pw-switch-title">草稿还未保存</h2><p>本地存储不可用或草稿待保存。继续切换会放弃本次未保存的编辑。</p><div><button autoFocus className="pw-button" onClick={() => setSwitcher(null)}>返回保存</button><button className="pw-button" onClick={() => { setShopId(switcher.shopId); setLiveId(switcher.liveId); setSwitcher(null); }}>放弃编辑并切换</button></div></section></div>}
  </div>;
}

function DraftWorkspace({ owner, products, lineup, snapshot, mode, now, beforeSwitchRef }: { owner: DraftOwner; products: WorkspaceProduct[]; lineup?: ProductLineup; snapshot: FrontendSnapshot; mode: Mode; now: number; beforeSwitchRef: React.RefObject<() => boolean> }) {
  const { service, identity } = useWorkspace();
  const initial = useState(() => {
    try { return { draft: parseDraft(typeof window === "undefined" ? null : localStorage.getItem(draftKey(owner)), owner), error: "" }; }
    catch (error) { return { draft: null, error: error instanceof Error ? error.message : "无法读取本地草稿" }; }
  })[0];
  const [items, setItems] = useState<LineupItem[]>(() => initial.draft?.items ?? lineup?.items ?? []);
  const [name, setName] = useState(initial.draft?.name ?? "本场讲品编排");
  const [savedAt, setSavedAt] = useState<string | null>(initial.draft?.savedAt ?? null);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState(initial.error);
  const [storageError, setStorageError] = useState(!!initial.error);
  const [tab, setTab] = useState<"lineup" | "catalog">(items.length ? "lineup" : "catalog");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState("order");
  const [checked, setChecked] = useState<string[]>([]);
  const [selectedKey, setSelectedKey] = useState(() => items[0] ? itemKey(items[0]) : products[0] ? productKey(products[0]) : "");
  const [metricScope, setMetricScope] = useState<"SHOP" | "LIVE_SESSION">("LIVE_SESSION");
  const [timelineChoice, setTimelineChoice] = useState<{ mode: Mode; tab: "plan" | "actual" } | null>(null);
  const timelineTab = timelineChoice?.mode === mode ? timelineChoice.tab : mode === "review" ? "actual" : "plan";
  const setTimelineTab = (tab: "plan" | "actual") => setTimelineChoice({ mode, tab });
  const [question, setQuestion] = useState("");
  const [aiResult, setAiResult] = useState<{ key: string; text: string; error: boolean } | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const aiController = useRef(0);
  const mounted = useRef(true);
  const dragging = useRef<number | null>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const selected = products.find((product) => productKey(product) === selectedKey);
  const performance = selected ? productPerformance(selected, metricScope, owner.date, owner.liveSessionId) : null;
  const roi = skuRoi(performance);
  const rate = conversionRate(performance);
  const currentItem = items.find((item) => itemKey(item) === selectedKey);
  const productMap = new Map(products.map((product) => [productKey(product), product]));
  const lineupProducts = items.flatMap((item) => productMap.get(itemKey(item)) ? [productMap.get(itemKey(item))!] : []);
  const missingItems = items.filter((item) => !productMap.has(itemKey(item)));
  const overlaps = timelineIssues(items);
  const present = new Set(items.map(itemKey));
  let visible = (tab === "catalog" ? products : lineupProducts).filter((product) => `${product.name} ${product.skuId} ${product.specification ?? ""}`.toLowerCase().includes(query.toLowerCase()) && (filter === "all" || (filter === "risk" ? ["out", "low"].includes(stockRisk(product)) : filter === "unplanned" ? !present.has(productKey(product)) : product.category === filter)));
  if (sort !== "order") visible = [...visible].sort((a, b) => sort === "stock" ? (a.stock ?? Infinity) - (b.stock ?? Infinity) : sort === "price" ? a.priceMinor - b.priceMinor : (productPerformance(b, metricScope, owner.date, owner.liveSessionId)?.paidGmvMinor ?? -1) - (productPerformance(a, metricScope, owner.date, owner.liveSessionId)?.paidGmvMinor ?? -1));
  const categories = [...new Set(products.map((product) => product.category).filter((value): value is string => !!value))];
  const actualProducts = lineup?.items.flatMap((item) => productMap.get(itemKey(item)) ? [productMap.get(itemKey(item))!] : []) ?? [];
  const ranking = actualProducts.flatMap((product) => { const row = productPerformance(product, "LIVE_SESSION", owner.date, owner.liveSessionId); return row && validCount(row.paidGmvMinor) ? [{ product, value: row.paidGmvMinor, units: row.paidUnits }] : []; }).sort((a, b) => b.value - a.value);
  const maxGmv = Math.max(1, ...ranking.map((row) => row.value));
  const completeRanking = !!lineup && ranking.length === actualProducts.length;
  const totalGmv = completeRanking ? ranking.reduce((sum, row) => sum + row.value, 0) : null;
  const replay = snapshot.replaySegments.filter((segment) => segment.liveSessionId === owner.liveSessionId && products.some((product) => product.id === segment.productId) && Number.isFinite(segment.startSeconds) && Number.isFinite(segment.endSeconds) && segment.startSeconds >= 0 && segment.endSeconds > segment.startSeconds).sort((a, b) => a.startSeconds - b.startSeconds);
  const risks = lineupProducts.filter((product) => stockRisk(product) !== "normal");
  const demo = owner.source === "MOCK";
  const canAnalyze = !!identity?.permissions.includes("snapshot.read") && (demo || identity.permissions.includes("task.create"));
  const stale = !demo && selected && (isStale(selected.sourceUpdatedAt, now) || isStale(performance?.sourceUpdatedAt, now));
  const aiKey = JSON.stringify([selectedKey, metricScope, performance, selected?.stock, question, snapshot.revision]);
  const activeAiKey = useRef(aiKey);
  useEffect(() => { activeAiKey.current = aiKey; }, [aiKey]);
  const returnedAi = aiResult?.key === aiKey ? aiResult : null;

  function save(nextItems = items, nextName = name) {
    if (!owner.liveSessionId) { setMessage("选择直播场次后才能保存编排草稿。"); return false; }
    if (initial.error) { setMessage("原草稿读取失败，为避免覆盖，请先导出当前编排；可用浏览器设置检查本地存储。"); return false; }
    try {
      const draft: LineupDraft = { version: 1, owner, name: nextName.trim() || "本场讲品编排", savedAt: new Date().toISOString(), items: nextItems };
      const key = draftKey(owner);
      const current = localStorage.getItem(key);
      if (current) { const stored = parseDraft(current, owner); if (stored && stored.savedAt !== savedAt) throw new Error("另一个窗口已更新草稿，请导出当前编排后刷新，避免覆盖。"); }
      localStorage.setItem(key, JSON.stringify(draft));
      setSavedAt(draft.savedAt); setDirty(false); setStorageError(false); setMessage("已保存到当前浏览器，尚未同步到微信小店。"); return true;
    } catch (error) { setStorageError(true); setMessage(error instanceof Error ? error.message : "本地草稿保存失败，请导出编排。"); return false; }
  }
  useEffect(() => { beforeSwitchRef.current = () => !dirty || save(); return () => { beforeSwitchRef.current = () => true; }; });
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  function edit(next: LineupItem[]) { setItems(next); setDirty(true); setMessage(""); }
  function add(keys: string[]) {
    if (!owner.liveSessionId) { setMessage("请先选择直播场次。"); return; }
    let end = Math.max(0, ...items.map((item) => item.startMinute + item.durationMinutes));
    const additions = products.filter((product) => keys.includes(productKey(product)) && !present.has(productKey(product))).map((product) => { const item = { productId: product.id, skuId: product.skuId, startMinute: Math.min(end, 1440), durationMinutes: 10, note: "" }; end += 10; return item; });
    if (!additions.length) { setMessage("所选商品已在当前货盘草稿中。"); return; }
    if (items.length + additions.length > 500) { setMessage("单份草稿最多支持 500 个 SKU，请分场次编排。"); return; }
    edit([...items, ...additions]); setChecked([]); setMessage(`已将 ${additions.length} 个 SKU 加入未保存草稿。`);
  }
  function exportDraft() {
    const blob = new Blob([JSON.stringify({ version: 1, owner, name, savedAt, status: "LOCAL_DRAFT_NOT_SYNCED", items }, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = `货盘草稿-${owner.date}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    setMessage("已导出本地草稿，文件不代表微信小店已更新。");
  }
  function updateItem(patch: Partial<LineupItem>) { edit(items.map((item) => itemKey(item) === selectedKey ? { ...item, ...patch } : item)); }
  function move(index: number, to: number) { edit(arrangeSequentially(reorderItems(items, index, to))); }
  async function askAi() {
    if (!selected || !service || aiBusy || !canAnalyze) return;
    const token = ++aiController.current; const key = aiKey;
    setAiBusy(true); setAiResult(null);
    const prompt = `仅分析商品，不执行任何变更。商品${selected.name.slice(0, 60)}，SKU ${selected.skuId.slice(0, 40)}。统计日${owner.date}，口径${metricScope === "SHOP" ? "店铺整体" : "本直播场次"}。库存${selected.stock ?? "未知"}，售价分${selected.priceMinor}，实售件数${performance?.paidUnits ?? "缺失"}，支付GMV分${performance?.paidGmvMinor ?? "缺失"}，点击${performance?.clicks ?? "缺失"}，订单${performance?.paidOrders ?? "缺失"}。来源时间${performance?.sourceUpdatedAt ?? "缺失"}。${stale ? "数据已过期，结论需核对。" : ""}未提供SKU归因时不计算广告ROI。运营问题：${question.trim().slice(0, 120) || "如何优化讲品顺序与转化？"}`;
    try {
      if (demo) {
        const focus = /库存|补货|售罄/.test(question) ? "先核对可售库存与补货时间，再确认该 SKU 的讲解位置。" : /转化|点击|话术/.test(question) ? "讲解时先说明规格与权益，再展示使用场景，并对照点击支付率观察反馈。" : "建议结合本场成交贡献安排主推商品，库存不足的 SKU 先核实补货；修改保留为草稿。";
        setAiResult({ key, error: false, text: `模拟分析（固定规则生成）\n${selected.name} · ${metricScope === "SHOP" ? "店铺当日" : "本直播场次"}\n${focus}\n${ruleAdvice(selected, performance).join("\n")}` });
        return;
      }
      const result = await service.interpretCommand({ text: prompt.slice(0, 500), scope: { ...snapshot.scope, shop_id: owner.shopId, live_session_id: metricScope === "SHOP" ? "all" : owner.liveSessionId || "all", connector_id: "all" } });
      if (mounted.current && token === aiController.current && activeAiKey.current === key) setAiResult({ key, text: result.kind === "QUERY" && typeof result.text === "string" && result.text.trim() ? result.text : "服务未返回只读分析。未创建或执行任何商品变更，请调整问题后重试。", error: result.kind !== "QUERY" });
    } catch (error) { if (mounted.current && token === aiController.current && activeAiKey.current === key) setAiResult({ key, text: error instanceof Error ? error.message : "AI 分析请求失败，请重试。", error: true }); }
    finally { if (mounted.current && token === aiController.current) setAiBusy(false); }
  }

  return <>
    <div className="pw-main-grid">
      <Section title="直播货盘" detail={lineup ? demo ? "已载入演示货盘，编排修改只保存为本地草稿。" : "正式货盘已读取，编排修改保存在独立本地草稿。" : "从店铺商品库开始编排，本地草稿尚未同步。"} className="pw-lineup" action={<span className="pw-tag">{dirty ? "有未保存编辑" : savedAt ? "本地草稿" : "待编排"}</span>}>
        <div className="pw-mode-hint">{mode === "prepare" ? <><CheckCircle size={15} /><span>备播检查 · {items.length} 个 SKU · {risks.length} 项库存待关注 · {overlaps.length} 项排期冲突</span></> : mode === "live" ? <><Broadcast size={15} /><span>直播跟进 · 讲解顺序可调整为草稿，经营指标以来源时间为准</span></> : <><ChartBar size={15} /><span>直播复盘 · 对照下方成交贡献与实际讲品片段，为下场优化编排</span></>}</div>
        <div className="pw-draft-toolbar"><label className="pw-draft-name"><Stack size={16} /><input aria-label="货盘草稿名称" value={name} maxLength={60} onChange={(event) => { setName(event.target.value); setDirty(true); }} /></label><div><button className="pw-icon-button" title="导出本地货盘草稿" aria-label="导出本地货盘草稿" onClick={exportDraft}><DownloadSimple size={17} /></button><button className="pw-button primary" disabled={!owner.liveSessionId} onClick={() => save()}><FloppyDisk size={15} />保存草稿</button></div></div>
        <div className="pw-list-tabs"><div role="group" aria-label="商品列表范围"><button className={tab === "lineup" ? "active" : ""} aria-pressed={tab === "lineup"} onClick={() => { setTab("lineup"); setChecked([]); }}>货盘编排 <b>{items.length}</b></button><button className={tab === "catalog" ? "active" : ""} aria-pressed={tab === "catalog"} onClick={() => { setTab("catalog"); setChecked([]); }}>店铺商品库 <b>{products.length}</b></button></div><span><Clock size={12} />{savedAt ? `本地保存 ${timestamp(savedAt)}` : "尚未保存"}</span></div>
        <div className="pw-list-filters"><label className="pw-search"><MagnifyingGlass size={16} /><input aria-label="搜索商品或SKU" placeholder="搜索商品、SKU、规格" value={query} onChange={(event) => setQuery(event.target.value)} /></label><select aria-label="商品筛选" value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">全部商品</option><option value="risk">库存预警</option>{tab === "catalog" && <option value="unplanned">未加入货盘</option>}{categories.map((category) => <option key={category} value={category}>{category}</option>)}</select><select aria-label="商品排序" value={sort} onChange={(event) => setSort(event.target.value)}><option value="order">编排顺序</option><option value="gmv">GMV 从高到低</option><option value="stock">库存从低到高</option><option value="price">价格从低到高</option></select></div>
        <div className="pw-bulk"><label><input type="checkbox" aria-label="全选当前筛选商品" checked={visible.length > 0 && visible.every((product) => checked.includes(productKey(product)))} onChange={(event) => setChecked(event.target.checked ? [...new Set([...checked, ...visible.map(productKey)])] : checked.filter((key) => !visible.some((product) => productKey(product) === key)))} />已选 {checked.length} 项</label><button disabled={!checked.length || !owner.liveSessionId} onClick={() => tab === "catalog" ? add(checked) : (edit(items.filter((item) => !checked.includes(itemKey(item)))), setChecked([]))}>{tab === "catalog" ? <Plus size={13} /> : <Trash size={13} />}{tab === "catalog" ? "加入货盘草稿" : "从草稿移除"}</button></div>
        <div className="pw-list-head"><span>商品 / 规格</span><span>售价 / 库存</span><span>{metricScope === "SHOP" ? "店铺" : "本场"} GMV / 销量</span><span>{tab === "lineup" ? "讲解排序" : "编排"}</span></div>
        <div className="pw-product-list" aria-label="商品列表">{visible.map((product) => {
          const key = productKey(product); const index = items.findIndex((item) => itemKey(item) === key); const row = productPerformance(product, metricScope, owner.date, owner.liveSessionId);
          const canMove = tab === "lineup" && sort === "order" && !query && filter === "all";
          return <article key={key} className={`pw-product-row ${selectedKey === key ? "selected" : ""}`} draggable={canMove} onDragStart={(event) => { dragging.current = index; event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", String(index)); }} onDragEnd={() => { dragging.current = null; }} onDragOver={(event) => { if (canMove) event.preventDefault(); }} onDrop={(event) => { event.preventDefault(); if (canMove && dragging.current !== null) move(dragging.current, index); dragging.current = null; }}>
            <div className="pw-row-product"><input type="checkbox" aria-label={`选择 ${product.name}`} checked={checked.includes(key)} onChange={(event) => setChecked(event.target.checked ? [...checked, key] : checked.filter((value) => value !== key))} /><button className="pw-product-select" onClick={() => setSelectedKey(key)} aria-pressed={selectedKey === key}><ProductImage product={product} /><span><strong>{product.name}</strong><small>{product.specification || "规格未提供"}</small><code>{product.skuId}</code></span></button></div>
            <div className="pw-row-price"><strong>{money(product.priceMinor)}</strong><Stock product={product} /></div><button className="pw-row-performance" onClick={() => setSelectedKey(key)}><strong>{currency(row?.paidGmvMinor)}</strong><small>{row ? `实售 ${integer(row.paidUnits)} 件` : "经营口径待接入"}</small></button>
            <div className="pw-row-order">{tab === "lineup" ? <><small>{String(index + 1).padStart(2, "0")}</small><button aria-label={`上移 ${product.name}`} disabled={index === 0 || !canMove} title={canMove ? "上移并按顺序重排时间" : "清空筛选并选择编排顺序后可调整"} onClick={() => move(index, index - 1)}><ArrowUp size={13} /></button><button aria-label={`下移 ${product.name}`} disabled={index === items.length - 1 || !canMove} onClick={() => move(index, index + 1)}><ArrowDown size={13} /></button></> : <button disabled={present.has(key) || !owner.liveSessionId} aria-label={`将 ${product.name} 加入草稿`} onClick={() => add([key])}>{present.has(key) ? <CheckCircle size={16} /> : <Plus size={17} />}</button>}</div>
          </article>;
        })}{!visible.length && <Missing title={products.length ? "没有符合条件的商品" : "暂无店铺商品"}>{items.length === 0 && tab === "lineup" ? <button className="pw-text-button" onClick={() => setTab("catalog")}>从店铺商品库添加商品 <ArrowRight size={13} /></button> : "调整筛选条件，或刷新已授权小店的商品数据。"}</Missing>}</div>
        {missingItems.length > 0 && <div className="pw-feedback error" role="alert">草稿中 {missingItems.length} 个 SKU 已不可见，请核对授权或商品状态。<button className="pw-text-button" onClick={() => edit(items.filter((item) => productMap.has(itemKey(item))))}>从草稿移除不可见商品</button></div>}
        <div className="pw-list-footer"><span>{items.length} 个 SKU · 计划 {items.reduce((sum, item) => sum + item.durationMinutes, 0)} 分钟</span><span><ShieldCheck size={13} />本地编排不改变商品上下架、价格和库存</span></div>
        {message && <p className={`pw-feedback ${storageError ? "error" : ""}`} role={storageError ? "alert" : "status"}>{message}</p>}
      </Section>
      <aside className="pw-detail-column" aria-label="选中商品经营详情">
        <Section title="商品经营表现" action={<span className="pw-tag">{demo ? "演示数据" : "只读分析"}</span>}>
          {selected ? <><div className="pw-selected-product"><ProductImage product={selected} large /><div><h3>{selected.name}</h3><p>{selected.specification || "规格未提供"}</p><code>{selected.skuId}</code><strong>{money(selected.priceMinor)}</strong></div></div><div className="pw-scope-toggle" role="group" aria-label="商品指标口径"><button aria-pressed={metricScope === "LIVE_SESSION"} className={metricScope === "LIVE_SESSION" ? "active" : ""} onClick={() => setMetricScope("LIVE_SESSION")}>本直播场次</button><button aria-pressed={metricScope === "SHOP"} className={metricScope === "SHOP" ? "active" : ""} onClick={() => setMetricScope("SHOP")}>店铺整体 · 当日</button></div><div className="pw-detail-metrics"><div><small>支付 GMV</small><strong>{currency(performance?.paidGmvMinor)}</strong></div><div><small>实际销量</small><strong>{integer(performance?.paidUnits)}<em>件</em></strong></div><div><small>支付订单</small><strong>{integer(performance?.paidOrders)}<em>笔</em></strong></div><div><small>点击支付率</small><strong>{rate === null ? "--" : `${(rate * 100).toFixed(1)}%`}</strong></div></div><p className="pw-source-note">{performance ? `来源更新 ${timestamp(performance.sourceUpdatedAt)}${stale ? " · 数据已过期" : ""}` : "经营数据未接入明确口径，旧版点击 / 订单未计入。"}</p><ProductTrend performance={performance} /><div className="pw-inventory"><div><span><Package size={15} />可售库存</span><Stock product={selected} /></div><p>预警线 ≤ {selected.stockWarningThreshold ?? 30} 件{selected.stockWarningThreshold === undefined ? "（本页观察阈值）" : ""} · 来源更新 {timestamp(selected.sourceUpdatedAt)}{!demo && isStale(selected.sourceUpdatedAt, now) ? " · 已过期" : ""}</p></div>{roi !== null && performance?.adAttribution ? <div className="pw-attribution"><span>SKU 广告 ROI <b>{roi.toFixed(2)}</b></span><small>{performance.adAttribution.attributionWindow} · {timestamp(performance.adAttribution.sourceUpdatedAt)}</small></div> : <p className="pw-source-note"><Info size={13} />SKU 广告归因未接入</p>}</> : <Missing title="选择一件商品">点击商品图片或名称，查看经营表现。</Missing>}
        </Section>
        <Section title="AI 商品助手" className="pw-ai" action={<Robot size={19} />}>
          {selected ? <><div className="pw-ai-label"><Sparkle size={14} />当前数据建议 · 本地规则</div><ul>{ruleAdvice(selected, performance).slice(0, 3).map((advice) => <li key={advice}>{advice}</li>)}</ul><label className="pw-question"><span className="sr-only">商品分析问题</span><textarea aria-label="商品分析问题" rows={2} maxLength={120} value={question} placeholder="例如：这件商品应该放在什么时候讲？" onChange={(event) => setQuestion(event.target.value)} /></label><button className="pw-button primary pw-ai-submit" disabled={aiBusy || !service || !canAnalyze} onClick={() => void askAi()}><Sparkle size={15} />{aiBusy ? "正在分析当前商品…" : demo ? "获取模拟 AI 分析" : "请求 AI 分析"}</button>{returnedAi && <div className={`pw-ai-response ${returnedAi.error ? "error" : ""}`} role={returnedAi.error ? "alert" : "status"}><b>{returnedAi.error ? "分析未完成" : demo ? "Mock 规则演示" : "分析服务返回"}</b><p>{returnedAi.text}</p></div>}<small className="pw-source-note">{!canAnalyze ? "当前账号未获得分析解析权限。" : "仅提交当前商品与所选口径。"}{demo ? "Mock 使用固定规则，不调用正式分析服务。" : "服务返回建议后，由运营核对再行动。"}</small></> : <Missing title="选择商品后开始分析" />}
        </Section>
      </aside>
    </div>
    <div className="pw-bottom-grid">
      <Section title="商品成交贡献" detail={`本场订单实付 GMV · ${demo ? "演示" : "正式"}货盘内商品`} action={<ChartBar size={17} />}>
        {ranking.length ? <><div className="pw-ranking">{ranking.slice(0, 6).map((row, index) => <button key={productKey(row.product)} className="pw-rank-row" onClick={() => setSelectedKey(productKey(row.product))}><span className={`pw-rank-number top-${index + 1}`}>{index + 1}</span><ProductImage product={row.product} /><span className="pw-rank-body"><span><strong>{row.product.name}</strong><b>{money(row.value)}</b></span><span className="pw-rank-track"><i style={{ width: `${row.value / maxGmv * 100}%` }} /></span><small>实售 {integer(row.units)} 件{totalGmv && totalGmv > 0 ? ` · 贡献 ${(row.value / totalGmv * 100).toFixed(1)}%` : ""}</small></span></button>)}</div>{!completeRanking && <p className="pw-source-note">部分商品经营指标缺失，仅列出已返回数据，不计算整体贡献率。</p>}</> : <Missing title={lineup ? "暂无本场成交指标" : "本场货盘尚未接入"}>接入带场次与 SKU 的订单实付数据后展示排行。</Missing>}
      </Section>
      <Section title="直播讲品时间轴" detail={timelineTab === "plan" ? "本地计划 · 相对开播时间，不代表实际已讲" : "已返回的实际讲品片段 · 相对开播时间"} action={<div className="pw-mini-tabs"><button aria-pressed={timelineTab === "plan"} onClick={() => setTimelineTab("plan")}>计划</button><button aria-pressed={timelineTab === "actual"} onClick={() => setTimelineTab("actual")}>实际片段</button></div>}>
        {timelineTab === "plan" ? <><div className="pw-timeline">{[...items].sort((a, b) => a.startMinute - b.startMinute).map((item) => { const key = itemKey(item); const product = productMap.get(key); return <button className={`pw-timeline-item ${selectedKey === key ? "active" : ""} ${overlaps.includes(key) ? "conflict" : ""}`} key={key} onClick={() => setSelectedKey(key)}><span>{minute(item.startMinute)}<small>{minute(item.startMinute + item.durationMinutes)}</small></span><i /><span><strong>{product?.name ?? "商品已不可见"}</strong><small>{item.durationMinutes} 分钟{overlaps.includes(key) ? " · 时间重叠" : ""}</small><p>{item.note || "尚未填写讲解要点"}</p></span></button>; })}</div>{!items.length && <Missing title="还没有讲解计划">加入商品后，可设置开始时间、时长及要点。</Missing>}{currentItem && selected && <div className="pw-plan-editor"><h3>编辑讲解 · {selected.name}</h3><div><label>开播后第几分钟<input aria-label="讲解开始分钟" type="number" min={0} max={1440} value={currentItem.startMinute} onChange={(event) => { if (validCount(event.target.valueAsNumber) && event.target.valueAsNumber <= 1440) updateItem({ startMinute: event.target.valueAsNumber }); }} /></label><label>讲解时长（分钟）<input aria-label="讲解时长分钟" type="number" min={1} max={180} value={currentItem.durationMinutes} onChange={(event) => { if (validCount(event.target.valueAsNumber) && event.target.valueAsNumber >= 1 && event.target.valueAsNumber <= 180) updateItem({ durationMinutes: event.target.valueAsNumber }); }} /></label></div><label>讲解要点<textarea aria-label="讲解要点" rows={2} maxLength={300} value={currentItem.note} placeholder="规格、权益、演示或需要核实的问题" onChange={(event) => updateItem({ note: event.target.value })} /></label><button className="pw-text-button" onClick={() => edit(arrangeSequentially(items))}><Clock size={13} />按当前顺序连续排期</button></div>}</> : replay.length ? <div className="pw-timeline">{replay.map((segment) => <div className="pw-replay-item" key={segment.id}><span>{minute(Math.floor(segment.startSeconds / 60))}<small>{minute(Math.floor(segment.endSeconds / 60))}</small></span><div><button className="pw-text-button" onClick={() => { const product = products.find((product) => product.id === segment.productId); if (product) setSelectedKey(productKey(product)); }}>{products.find((product) => product.id === segment.productId)?.name}</button><p>{segment.transcript}</p>{safeProductImage(segment.videoUrl) ? <a href={safeProductImage(segment.videoUrl)!} target="_blank" rel="noreferrer">查看回放片段 <ArrowSquareOut size={12} /></a> : <small>回放视频未提供</small>}</div></div>)}</div> : <Missing title="暂无实际讲品记录">本地计划不会被当作真实讲解记录。</Missing>}
      </Section>
      <Section title="库存风险与商品异常" detail="基于当前草稿 · 变更须走现有权限与审批" action={<ShieldCheck size={18} />}>
        {risks.map((product) => <button className="pw-risk-item" key={productKey(product)} onClick={() => setSelectedKey(productKey(product))}><span className={`pw-risk-icon ${stockRisk(product)}`}><WarningCircle size={19} /></span><span><strong>{product.name}</strong><small>{stockRisk(product) === "out" ? "库存为零，讲解前确认补货" : stockRisk(product) === "unknown" ? "库存未提供，需核对可售数量" : `可售 ${product.stock} 件，建议核对补货`}</small></span><ArrowRight size={13} /></button>)}
        {!!overlaps.length && <div className="pw-risk-note"><WarningCircle size={16} /><p>{overlaps.length} 项讲解与其他商品时间重叠，请检查时间轴。</p></div>}
        {missingItems.length > 0 && <div className="pw-risk-note"><WarningCircle size={16} /><p>草稿中有 {missingItems.length} 个 SKU 已不可见，不能据此确认正式货盘。</p></div>}
        {!risks.length && <Missing title={items.length ? "当前草稿未发现库存预警" : "编排后检查库存风险"}>{items.length ? "仍需在开播前核对库存更新时间。" : "加入商品后自动检查售罄、低库存与缺失项。"}</Missing>}
        <div className="pw-approval-note"><ShieldCheck size={18} /><div><strong>商品变更受权限保护</strong><p>上下架、售价与库存编辑接口尚未接入。本页只保存货盘与讲解草稿。</p></div></div>
      </Section>
    </div>
    <details className="pw-data-notes"><summary><Info size={15} />数据口径与接入说明</summary><p>商品与库存来自微信小店经营快照；实售件数、实付 GMV、点击和订单需带店铺 / 场次、SKU、统计日及来源时间。旧字段未声明统计范围，不用作本场或店铺当日指标。缺失值显示 --，零值照常展示，禁止用售价 × 订单数推算实付 GMV。</p><p>广告计划数据继续供作战室使用，未含 SKU 归因时本页不展示单品广告 ROI。趋势仅绘制接口返回的时间点；正式来源超过 5 分钟提示过期，未知更新时间单独标明。模式切换仅改变工作视角，不改变真实直播状态。</p><p>草稿按数据源、租户、用户、店铺、场次和统计日隔离，只在当前浏览器保存。刷新不会覆盖未保存编辑；切换场次前尝试保存，失败时可导出。无后端货盘编辑接口，不显示同步成功。</p></details>
  </>;
}

function ProductTrend({ performance }: { performance: ProductPerformance | null }) {
  const points = performance?.trend ?? [];
  if (points.length < 2) return <div className="pw-trend-empty"><ChartBar size={21} /><span>成交趋势未接入</span><small>需要至少 2 个同口径时间点</small></div>;
  const minTime = Date.parse(points[0].at); const maxTime = Date.parse(points[points.length - 1].at);
  const max = Math.max(1, ...points.map((point) => point.paidGmvMinor));
  const coordinates = points.map((point) => `${10 + (Date.parse(point.at) - minTime) / Math.max(1, maxTime - minTime) * 280},${78 - point.paidGmvMinor / max * 62}`).join(" ");
  return <div className="pw-trend"><div><span>商品成交趋势</span><small>分时实付 GMV</small></div><svg viewBox="0 0 300 90" role="img" aria-label="商品分时支付GMV趋势"><title>{points.map((point) => `${timestamp(point.at)}：${money(point.paidGmvMinor)}`).join("；")}</title><path d="M10 78H290 M10 45H290 M10 14H290" fill="none" stroke="var(--border-subtle)" strokeDasharray="3 4" /><polyline points={coordinates} fill="none" stroke="var(--brand-primary)" strokeWidth="2" vectorEffect="non-scaling-stroke" /></svg><div><small>{timestamp(points[0].at)}</small><small>{timestamp(points[points.length - 1].at)}</small></div></div>;
}
