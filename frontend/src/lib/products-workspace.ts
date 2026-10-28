import type { Product } from "./domain";

/** Optional read-only snapshot fields. See the products handoff for the proposed
 * contract. Legacy unscoped clicks/orders are deliberately not used as KPIs. */
export interface ProductPerformance {
  scope: "SHOP" | "LIVE_SESSION";
  shopId: string;
  liveSessionId?: string;
  skuId: string;
  date: string;
  timezone: "Asia/Shanghai";
  currency: "CNY";
  clicks: number | null;
  paidOrders: number | null;
  paidUnits: number | null;
  paidGmvMinor: number | null;
  sourceUpdatedAt: string;
  trend?: { at: string; paidGmvMinor: number; paidUnits: number }[];
  adAttribution?: {
    skuId: string;
    spendMinor: number;
    paidGmvMinor: number;
    attributionWindow: string;
    sourceUpdatedAt: string;
  };
}
export interface WorkspaceProduct extends Product {
  imageUrl?: string | null;
  specification?: string;
  category?: string;
  sourceUpdatedAt?: string;
  stockWarningThreshold?: number;
  performance?: ProductPerformance[];
}
export interface LineupItem {
  productId: string;
  skuId: string;
  startMinute: number;
  durationMinutes: number;
  note: string;
}
export interface ProductLineup {
  id: string;
  shopId: string;
  liveSessionId: string;
  sourceUpdatedAt: string;
  items: LineupItem[];
}
export interface DraftOwner {
  source: "API" | "MOCK";
  tenantId: string;
  userId: string;
  shopId: string;
  liveSessionId: string;
  date: string;
}
export interface LineupDraft {
  version: 1;
  owner: DraftOwner;
  name: string;
  savedAt: string;
  items: LineupItem[];
}
export const productKey = (product: { id: string; skuId: string }) => JSON.stringify([product.id, product.skuId]);
export const itemKey = (item: LineupItem) => productKey({ id: item.productId, skuId: item.skuId });
export const draftKey = (owner: DraftOwner) => `yuliang:product-lineup:v1:${JSON.stringify(owner)}`;
export const validCount = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
export const validTimestamp = (value: unknown): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
export function safeProductImage(value: unknown) {
  if (typeof value !== "string") return null;
  try { const url = new URL(value); return url.protocol === "https:" && !url.username && !url.password ? url.href : null; } catch { return null; }
}
export function stockRisk(product: WorkspaceProduct) {
  if (!validCount(product.stock)) return "unknown";
  return product.stock === 0 ? "out" : product.stock <= (validCount(product.stockWarningThreshold) ? product.stockWarningThreshold : 30) ? "low" : "normal";
}
export function productPerformance(product: WorkspaceProduct, scope: "SHOP" | "LIVE_SESSION", date: string, liveId: string) {
  const rows = (product.performance ?? []).filter((row) => row.scope === scope && row.shopId === product.shopId && row.skuId === product.skuId && row.date === date && row.timezone === "Asia/Shanghai" && row.currency === "CNY" && (scope === "SHOP" ? !row.liveSessionId : row.liveSessionId === liveId));
  // Conflicting duplicate scope rows must be resolved by the backend.
  return rows.length === 1 ? rows[0] : null;
}
export function conversionRate(performance: ProductPerformance | null) {
  return performance && validCount(performance.clicks) && performance.clicks > 0 && validCount(performance.paidOrders) && performance.paidOrders <= performance.clicks ? performance.paidOrders / performance.clicks : null;
}
export function skuRoi(performance: ProductPerformance | null) {
  const attribution = performance?.adAttribution;
  return attribution && attribution.skuId === performance?.skuId && validCount(attribution.spendMinor) && attribution.spendMinor > 0 && validCount(attribution.paidGmvMinor) && attribution.attributionWindow.trim() && validTimestamp(attribution.sourceUpdatedAt) ? attribution.paidGmvMinor / attribution.spendMinor : null;
}
export function productTotals(products: WorkspaceProduct[], date: string, liveId: string, completeLineup: boolean) {
  const rows = products.map((product) => productPerformance(product, "LIVE_SESSION", date, liveId));
  const completeGmv = completeLineup && rows.every((row) => row && validCount(row.paidGmvMinor));
  const completeUnits = completeLineup && rows.every((row) => row && validCount(row.paidUnits));
  return {
    count: completeLineup ? products.length : null,
    gmv: completeGmv ? rows.reduce((sum, row) => sum + row!.paidGmvMinor!, 0) : null,
    sold: completeUnits ? rows.filter((row) => row!.paidUnits! > 0).length : null,
    risk: completeLineup && products.every((p) => stockRisk(p) !== "unknown") ? products.filter((p) => ["out", "low"].includes(stockRisk(p))).length : null,
  };
}
export function reorderItems(items: LineupItem[], from: number, to: number) {
  if (from < 0 || to < 0 || from >= items.length || to >= items.length) return items;
  const next = [...items]; const [item] = next.splice(from, 1); next.splice(to, 0, item); return next;
}
export function arrangeSequentially(items: LineupItem[]) {
  let minute = 0;
  return items.map((item) => { const next = { ...item, startMinute: minute }; minute += item.durationMinutes; return next; });
}
export function validLineupItem(value: unknown): value is LineupItem {
  if (!value || typeof value !== "object") return false;
  const item = value as LineupItem;
  return typeof item.productId === "string" && !!item.productId && typeof item.skuId === "string" && !!item.skuId && validCount(item.startMinute) && item.startMinute <= 1440 && validCount(item.durationMinutes) && item.durationMinutes >= 1 && item.durationMinutes <= 180 && typeof item.note === "string" && item.note.length <= 300;
}
export function parseDraft(raw: string | null, owner: DraftOwner): LineupDraft | null {
  if (!raw) return null;
  const draft = JSON.parse(raw) as LineupDraft;
  if (!draft || draft.version !== 1 || !draft.owner || Object.entries(owner).some(([key, value]) => draft.owner[key as keyof DraftOwner] !== value) || typeof draft.name !== "string" || !draft.name.trim() || draft.name.length > 60 || !validTimestamp(draft.savedAt) || !Array.isArray(draft.items) || draft.items.length > 500 || !draft.items.every(validLineupItem) || new Set(draft.items.map(itemKey)).size !== draft.items.length) throw new Error("草稿格式或归属不正确，请导出后检查；未覆盖已保存内容。");
  return draft;
}
export function timelineIssues(items: LineupItem[]) {
  return items.flatMap((item, index) => items.slice(0, index).some((previous) => item.startMinute < previous.startMinute + previous.durationMinutes && previous.startMinute < item.startMinute + item.durationMinutes) ? [itemKey(item)] : []);
}
export function ruleAdvice(product: WorkspaceProduct, performance: ProductPerformance | null) {
  const advice: string[] = [];
  if (stockRisk(product) === "out") advice.push("当前可用库存为 0，建议先核对补货，再安排讲解；库存变更仍需原有审批。");
  if (stockRisk(product) === "low") advice.push(`当前库存 ${product.stock} 件，已达到预警线，建议复核可售库存与补货节奏。`);
  const rate = conversionRate(performance);
  if (rate !== null) advice.push(`当前口径 ${performance!.clicks} 次点击、${performance!.paidOrders} 笔支付订单，点击支付率 ${(rate * 100).toFixed(1)}%；可围绕规格、价格权益和售后补充讲解。`);
  if (performance && validCount(performance.paidUnits)) advice.push(`当前口径已售 ${performance.paidUnits} 件。可结合成交贡献调整本地讲品顺序。`);
  if (!performance) advice.push("尚无明确统计口径的经营指标，先补齐本场商品成交数据，再判断转化与讲品效果。");
  if (skuRoi(performance) === null) advice.push("SKU 广告归因尚未提供，不能用计划整体 ROI 判断这件商品的投放回报。");
  return advice;
}
