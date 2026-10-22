import type { FrontendSnapshot, Identity } from "../lib/domain";
import { itemKey, productKey, validCount, validLineupItem, validTimestamp, type ProductLineup, type ProductPerformance, type WorkspaceProduct } from "../lib/products-workspace";
import { demoProductWorkspace } from "./products-demo";

function validPerformance(value: unknown): value is ProductPerformance {
  if (!value || typeof value !== "object") return false;
  const row = value as ProductPerformance;
  return ["SHOP", "LIVE_SESSION"].includes(row.scope) && typeof row.shopId === "string" && typeof row.skuId === "string" && /^\d{4}-\d{2}-\d{2}$/.test(row.date) && row.timezone === "Asia/Shanghai" && row.currency === "CNY" && validTimestamp(row.sourceUpdatedAt) && (row.scope !== "LIVE_SESSION" || typeof row.liveSessionId === "string") && [row.clicks, row.paidOrders, row.paidUnits, row.paidGmvMinor].every((value) => value === null || validCount(value));
}
function normalizeProduct(value: WorkspaceProduct): WorkspaceProduct | null {
  if (!value || typeof value.id !== "string" || typeof value.skuId !== "string" || typeof value.name !== "string" || !validCount(value.priceMinor)) return null;
  return { ...value, stock: validCount(value.stock) ? value.stock : null, stockWarningThreshold: validCount(value.stockWarningThreshold) ? value.stockWarningThreshold : undefined, sourceUpdatedAt: validTimestamp(value.sourceUpdatedAt) ? value.sourceUpdatedAt : undefined, specification: typeof value.specification === "string" ? value.specification : undefined, category: typeof value.category === "string" ? value.category : undefined, performance: Array.isArray(value.performance) ? value.performance.filter(validPerformance).map((row) => ({ ...row,
    trend: Array.isArray(row.trend) ? row.trend.filter((point) => point && validTimestamp(point.at) && new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date(point.at)) === row.date && validCount(point.paidGmvMinor) && validCount(point.paidUnits)).sort((a, b) => Date.parse(a.at) - Date.parse(b.at)).filter((point, index, rows) => index === 0 || point.at !== rows[index - 1].at) : undefined,
    adAttribution: row.adAttribution && typeof row.adAttribution.attributionWindow === "string" ? row.adAttribution : undefined,
  })) : [] };
}
/** Consume the existing authorized snapshot. Never request invented endpoints,
 * convert legacy orders to GMV, or join plan-wide ROI onto a SKU. */
export function buildProductWorkspace(snapshot: FrontendSnapshot, identity: Identity) {
  if (snapshot.source !== identity.mode) return { products: [], lineups: [], shops: [], sessions: [], rejected: true };
  const shops = snapshot.shops.filter((shop) => shop.tenantId === identity.tenantId && identity.shopIds.includes(shop.id));
  const allowedShops = new Set(shops.map((shop) => shop.id));
  const sessions = snapshot.liveSessions.filter((session) => session.tenantId === identity.tenantId && allowedShops.has(session.shopId));
  const source = snapshot.source === "MOCK" ? demoProductWorkspace(snapshot) : { products: snapshot.products, lineups: (snapshot as FrontendSnapshot & { productLineups?: ProductLineup[] }).productLineups };
  const normalized = source.products.filter((product) => product && product.tenantId === identity.tenantId && allowedShops.has(product.shopId)).map(normalizeProduct).filter((product): product is WorkspaceProduct => product !== null);
  // Ambiguous duplicate SKU identities cannot establish a complete official lineup.
  const products = normalized.filter((product) => normalized.filter((other) => productKey(other) === productKey(product)).length === 1);
  const keys = new Set(products.map(productKey));
  const lineups = Array.isArray(source.lineups) ? source.lineups.filter((lineup) => lineup && typeof lineup.id === "string" && allowedShops.has(lineup.shopId) && sessions.some((session) => session.id === lineup.liveSessionId && session.shopId === lineup.shopId) && validTimestamp(lineup.sourceUpdatedAt) && Array.isArray(lineup.items) && lineup.items.length <= 500 && lineup.items.every((item) => validLineupItem(item) && keys.has(itemKey(item)) && products.some((product) => productKey(product) === itemKey(item) && product.shopId === lineup.shopId)) && new Set(lineup.items.map(itemKey)).size === lineup.items.length) : [];
  return { products, lineups, shops, sessions, rejected: false };
}
