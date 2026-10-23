import { advertisingProducts, type AdvertisingProduct } from "./advertising";
import type { FrontendSnapshot, Identity } from "./domain";
import type { IntegrationContext } from "./platform-catalog";
import type { ContentAccountLink, ContentOwner, ContentState } from "./content-factory";

export type ContentDeliveryAccount = Omit<ContentAccountLink, "linkedAt" | "linkedBy"> & { expiresAt: string };
export const accountLinkKey = (a: Pick<ContentAccountLink, "productLine" | "accountId">) => JSON.stringify([a.productLine, a.accountId]);
const inScope = (a: Pick<ContentAccountLink, "tenantId" | "shopId" | "mode">, owner: ContentOwner) => a.tenantId === owner.tenantId && a.shopId === owner.shopId && a.mode === owner.mode;

export function contentDeliveryAccounts(owner: ContentOwner, identity: Identity, productLine: AdvertisingProduct, source: FrontendSnapshot | IntegrationContext, now = Date.now()): ContentDeliveryAccount[] {
  if (identity.mode !== owner.mode || identity.tenantId !== owner.tenantId || identity.userId !== owner.userId || !identity.shopIds.includes(owner.shopId)) return [];
  if ("adAccounts" in source) {
    if (owner.mode !== "MOCK" || source.source !== "MOCK") return [];
    const connectors: Partial<Record<AdvertisingProduct, string>> = { adq: "adq", shop: "wxpromo", beans: "beans", qianchuan: "qianchuan", juguang: "juguang" };
    return source.adAccounts.filter(a => a.tenantId === owner.tenantId && a.shopId === owner.shopId && a.connectorId === connectors[productLine]).map(a => ({ accountId: a.id, accountName: a.name, nativeId: a.id, productLine, businessEntityId: "", tenantId: owner.tenantId, shopId: owner.shopId, mode: "MOCK", expiresAt: new Date(now + 300_000).toISOString() }));
  }
  if (owner.mode !== "API" || source.source !== "API" || source.tenantId !== owner.tenantId || source.productLine !== productLine || Date.parse(source.expiresAt) <= now || !Number.isFinite(Date.parse(source.expiresAt))) return [];
  const shop = source.shops.find(s => s.id === owner.shopId);
  return source.accounts.filter(a => a.authorization === "AUTHORIZED" && a.productLine === productLine && a.provider === advertisingProducts[productLine].provider && a.businessEntityId === shop?.businessEntityId && source.links.some(l => l.accountId === a.id && l.shopId === owner.shopId && l.state === "AUTHORIZED")).map(a => ({ accountId: a.id, accountName: a.name, nativeId: a.nativeId, productLine, businessEntityId: a.businessEntityId, tenantId: owner.tenantId, shopId: owner.shopId, mode: "API", expiresAt: source.expiresAt }));
}

export function validateAccountLinks(state: ContentState, owner?: ContentOwner) {
  for (const asset of state.assets) {
    if (asset.accountLinks === undefined) continue;
    if (!Array.isArray(asset.accountLinks)) throw new Error("素材投放账号关联格式无效。");
    const seen = new Set<string>();
    for (const link of asset.accountLinks) {
      if (!link || ![link.accountId, link.accountName, link.nativeId, link.tenantId, link.shopId, link.linkedBy].every(v => typeof v === "string" && v.trim()) || typeof link.businessEntityId !== "string" || !Object.hasOwn(advertisingProducts, link.productLine) || !["MOCK", "API"].includes(link.mode) || !Number.isFinite(Date.parse(link.linkedAt)) || (owner && !inScope(link, owner)) || seen.has(accountLinkKey(link))) throw new Error("素材投放账号关联无效或不属于当前店铺。");
      seen.add(accountLinkKey(link));
    }
  }
}

export function linkContentAccounts(state: ContentState, assetId: string, owner: ContentOwner, additions: ContentDeliveryAccount[], removals: string[], now = Date.now()): ContentState {
  validateAccountLinks(state, owner);
  const asset = state.assets.find(a => a.id === assetId);
  if (!asset || !["image", "video", "text"].includes(asset.kind) || asset.size <= 0) throw new Error("请先选择已入库的成品图片、视频或文稿。");
  if (additions.some(a => !inScope(a, owner) || !Number.isFinite(Date.parse(a.expiresAt)) || Date.parse(a.expiresAt) <= now)) throw new Error("账号授权信息已过期或不在当前店铺，请刷新账号后重试。");
  const links = new Map((asset.accountLinks ?? []).filter(a => !removals.includes(accountLinkKey(a))).map(a => [accountLinkKey(a), a]));
  for (const candidate of additions) {
    const account = { accountId: candidate.accountId, accountName: candidate.accountName, nativeId: candidate.nativeId, productLine: candidate.productLine, businessEntityId: candidate.businessEntityId, tenantId: candidate.tenantId, shopId: candidate.shopId, mode: candidate.mode };
    const key = accountLinkKey(account);
    if (!links.has(key)) links.set(key, { ...account, linkedAt: new Date(now).toISOString(), linkedBy: owner.userId });
  }
  const next = { ...state, assets: state.assets.map(a => a.id === assetId ? { ...a, accountLinks: [...links.values()] } : a) };
  validateAccountLinks(next, owner);
  return next;
}
