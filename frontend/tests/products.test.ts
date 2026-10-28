import test from "node:test";
import assert from "node:assert/strict";
import { createMockSnapshot } from "../src/services/mock-data";
import { buildProductWorkspace } from "../src/services/products-adapter";
import { demoProductWorkspace } from "../src/services/products-demo";
import { arrangeSequentially, conversionRate, draftKey, itemKey, parseDraft, productKey, productPerformance, productTotals, reorderItems, ruleAdvice, safeProductImage, skuRoi, stockRisk, timelineIssues, type DraftOwner, type LineupDraft, type ProductLineup, type ProductPerformance, type WorkspaceProduct } from "../src/lib/products-workspace";
import type { FrontendSnapshot, Identity } from "../src/lib/domain";

const identity: Identity = { userId: "operator", tenantId: "tenant-demo", displayName: "运营", mode: "API", shopIds: ["shop-tea"], permissions: ["snapshot.read"], workspacePages: ["products"] };
function fixture() {
  const snapshot: FrontendSnapshot & { productLineups?: ProductLineup[] } = createMockSnapshot();
  snapshot.source = "API";
  const product = snapshot.products[0] as WorkspaceProduct;
  const row: ProductPerformance = { scope: "LIVE_SESSION", shopId: product.shopId, liveSessionId: snapshot.liveSessions[0].id, skuId: product.skuId, date: snapshot.scope.date, timezone: "Asia/Shanghai", currency: "CNY", clicks: 100, paidOrders: 12, paidUnits: 15, paidGmvMinor: 100001, sourceUpdatedAt: "2026-10-22T10:00:00+08:00" };
  product.performance = [row];
  product.sourceUpdatedAt = row.sourceUpdatedAt;
  const item = { productId: product.id, skuId: product.skuId, startMinute: 0, durationMinutes: 12, note: "说明规格" };
  snapshot.productLineups = [{ id: "lineup-1", shopId: product.shopId, liveSessionId: row.liveSessionId!, sourceUpdatedAt: row.sourceUpdatedAt, items: [item] }];
  return { snapshot, product, row, item };
}
test("products: production never supplements fixtures or treats unscoped orders as paid GMV", () => {
  const { snapshot, product, row } = fixture();
  delete product.performance;
  const model = buildProductWorkspace(snapshot, identity);
  assert.equal(model.products.length, 2);
  assert.equal(model.products[0].imageUrl, undefined);
  assert.equal(productPerformance(model.products[0], "LIVE_SESSION", row.date, row.liveSessionId!), null);
  assert.equal(productPerformance(model.products[0], "SHOP", row.date, row.liveSessionId!), null);
  assert.equal(productTotals(model.products, row.date, row.liveSessionId!, true).gmv, null);
  assert.deepEqual(demoProductWorkspace(snapshot), { products: [], lineups: [] });
});
test("products: shop, session, date, SKU, currency and timezone are independent metric boundaries", () => {
  const { product, row } = fixture();
  assert.equal(productPerformance(product, "LIVE_SESSION", row.date, row.liveSessionId!), row);
  for (const patch of [{ shopId: "other-shop" }, { liveSessionId: "other-live" }, { skuId: "other-sku" }, { date: "2026-01-01" }, { currency: "USD" }, { timezone: "UTC" }, { scope: "SHOP" }]) {
    const value = { ...product, performance: [{ ...row, ...patch } as ProductPerformance] };
    assert.equal(productPerformance(value, "LIVE_SESSION", row.date, row.liveSessionId!), null);
  }
  product.performance = [row, { ...row, scope: "SHOP", liveSessionId: undefined, paidGmvMinor: 900000 }];
  assert.equal(productPerformance(product, "SHOP", row.date, row.liveSessionId!)?.paidGmvMinor, 900000);
  product.performance.push(row);
  assert.equal(productPerformance(product, "LIVE_SESSION", row.date, row.liveSessionId!), null);
});
test("products: official lineup totals use actual paid units and GMV; drafts and plan attribution cannot inflate them", () => {
  const { product, row } = fixture();
  assert.deepEqual(productTotals([product], row.date, row.liveSessionId!, true), { count: 1, gmv: 100001, sold: 1, risk: 0 });
  assert.deepEqual(productTotals([product], row.date, row.liveSessionId!, false), { count: null, gmv: null, sold: null, risk: null });
  row.paidGmvMinor = 0; row.paidUnits = 0;
  assert.equal(productTotals([product], row.date, row.liveSessionId!, true).gmv, 0);
  assert.equal(productTotals([product], row.date, row.liveSessionId!, true).sold, 0);
  row.paidGmvMinor = null;
  assert.equal(productTotals([product], row.date, row.liveSessionId!, true).gmv, null);
  row.paidGmvMinor = 100001; row.paidUnits = null;
  assert.equal(productTotals([product], row.date, row.liveSessionId!, true).gmv, 100001);
  assert.equal(productTotals([product], row.date, row.liveSessionId!, true).sold, null);
  assert.deepEqual(productTotals([], row.date, row.liveSessionId!, true), { count: 0, gmv: 0, sold: 0, risk: 0 });
});
test("products: SKU advertising ROI requires explicit same-SKU attributed spend and paid GMV", () => {
  const { row } = fixture();
  assert.equal(skuRoi(row), null);
  row.adAttribution = { skuId: row.skuId, spendMinor: 10000, paidGmvMinor: 45000, attributionWindow: "点击后 24 小时", sourceUpdatedAt: row.sourceUpdatedAt };
  assert.equal(skuRoi(row), 4.5);
  row.adAttribution.skuId = "other"; assert.equal(skuRoi(row), null);
  row.adAttribution.skuId = row.skuId; row.adAttribution.spendMinor = 0; assert.equal(skuRoi(row), null);
  row.adAttribution.spendMinor = 10000; row.adAttribution.attributionWindow = ""; assert.equal(skuRoi(row), null);
});
test("products: invalid conversion denominators never become zero or impossible conversion rates", () => {
  const { row } = fixture();
  assert.equal(conversionRate(row), .12);
  row.clicks = 0; assert.equal(conversionRate(row), null);
  row.clicks = 5; assert.equal(conversionRate(row), null);
  row.clicks = 100; row.paidOrders = 0; assert.equal(conversionRate(row), 0);
});
test("products: tenant, authorized shops and mode gate every product and lineup", () => {
  const { snapshot, product } = fixture();
  snapshot.products.push({ ...product, id: "other-tenant", tenantId: "tenant-other" });
  const model = buildProductWorkspace(snapshot, identity);
  assert.equal(model.shops.length, 1); assert.equal(model.products.length, 2); assert.equal(model.lineups.length, 1);
  assert.ok(model.products.every((product) => product.shopId === "shop-tea"));
  assert.equal(buildProductWorkspace(snapshot, { ...identity, shopIds: [] }).products.length, 0);
  snapshot.source = "MOCK";
  assert.equal(buildProductWorkspace(snapshot, identity).rejected, true);
  const demo = buildProductWorkspace(snapshot, { ...identity, mode: "MOCK" });
  assert.equal(demo.products.length, 7); assert.equal(demo.lineups[0].items.length, 6);
});
test("products: malformed and cross-shop lineups are unavailable rather than partially complete", () => {
  const { snapshot, product, item } = fixture();
  snapshot.productLineups![0].items.push({ ...item, skuId: "missing-sku" });
  assert.equal(buildProductWorkspace(snapshot, identity).lineups.length, 0);
  snapshot.productLineups![0].items = [item, item];
  assert.equal(buildProductWorkspace(snapshot, identity).lineups.length, 0);
  snapshot.productLineups![0].items = [item];
  snapshot.products.push({ ...product });
  assert.equal(buildProductWorkspace(snapshot, identity).lineups.length, 0);
});
test("products: malformed metrics and out-of-date-range trend points are ignored", () => {
  const { snapshot, product, row } = fixture();
  row.trend = [
    { at: `${row.date}T09:00:00+08:00`, paidGmvMinor: 3000, paidUnits: 1 },
    { at: `${row.date}T09:15:00+08:00`, paidGmvMinor: 4000, paidUnits: 2 },
    { at: "2026-01-01T09:30:00+08:00", paidGmvMinor: 5000, paidUnits: 3 },
    { at: `${row.date}T09:45:00+08:00`, paidGmvMinor: -1, paidUnits: 1 },
  ];
  assert.equal(buildProductWorkspace(snapshot, identity).products[0].performance![0].trend?.length, 2);
  product.performance = [{ ...row, paidOrders: -1 }, { ...row, timezone: "UTC" } as unknown as ProductPerformance];
  assert.equal(buildProductWorkspace(snapshot, identity).products[0].performance?.length, 0);
});
test("products: draft namespace and payload isolate source, tenant, user, shop, session and day", () => {
  const { item } = fixture();
  const owner: DraftOwner = { source: "API", tenantId: "tenant-demo", userId: "operator", shopId: "shop-tea", liveSessionId: "live-tea", date: "2026-10-22" };
  const draft: LineupDraft = { version: 1, owner, name: "晚场货盘", savedAt: "2026-10-22T12:00:00+08:00", items: [item] };
  const raw = JSON.stringify(draft);
  assert.deepEqual(parseDraft(raw, owner), draft);
  for (const patch of [{ source: "MOCK" }, { tenantId: "another" }, { userId: "other-user" }, { shopId: "other-shop" }, { liveSessionId: "other-live" }, { date: "2026-10-23" }]) {
    const other = { ...owner, ...patch } as DraftOwner;
    assert.notEqual(draftKey(other), draftKey(owner)); assert.throws(() => parseDraft(raw, other));
  }
  assert.equal(parseDraft(null, owner), null);
  assert.throws(() => parseDraft("broken", owner));
  assert.throws(() => parseDraft(JSON.stringify({ ...draft, items: [item, item] }), owner));
  assert.throws(() => parseDraft(JSON.stringify({ ...draft, items: [{ ...item, durationMinutes: 0 }] }), owner));
});
test("products: reordering retains SKU links, reschedules and detects overlap", () => {
  const { item } = fixture();
  const second = { ...item, productId: "second", skuId: "second-sku", startMinute: 10 };
  assert.deepEqual(timelineIssues([item, second]), [itemKey(second)]);
  const result = arrangeSequentially(reorderItems([item, second], 0, 1));
  assert.equal(result[0].productId, "second"); assert.equal(result[1].startMinute, 12);
  assert.deepEqual(timelineIssues(result), []);
  assert.equal(productKey({ id: item.productId, skuId: item.skuId }), itemKey(item));
  assert.equal(item.startMinute, 0); assert.equal(second.startMinute, 10);
});
test("products: missing, zero and low stocks stay distinct and unsafe image URLs do not render", () => {
  const { product, row } = fixture();
  product.stock = null; assert.equal(stockRisk(product), "unknown");
  product.stock = 0; assert.equal(stockRisk(product), "out");
  product.stock = 30; assert.equal(stockRisk(product), "low");
  product.stock = 31; assert.equal(stockRisk(product), "normal");
  assert.equal(safeProductImage("javascript:alert(1)"), null);
  assert.equal(safeProductImage("http://images.example/p.png"), null);
  assert.equal(safeProductImage("https://images.example/p.png"), "https://images.example/p.png");
  assert.ok(ruleAdvice(product, row).some((advice) => advice.includes("SKU 广告归因")));
});
