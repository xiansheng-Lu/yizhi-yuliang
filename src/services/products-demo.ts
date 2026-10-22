import type { FrontendSnapshot } from "../lib/domain";
import type { ProductLineup, ProductPerformance, WorkspaceProduct } from "../lib/products-workspace";

/** Explicitly isolated fixture: API snapshots never enter this function's demo branch.
 * Photos are illustrative demo assets, never presented as merchant product photos. */
export function demoProductWorkspace(snapshot: FrontendSnapshot): { products: WorkspaceProduct[]; lineups: ProductLineup[] } {
  if (snapshot.source !== "MOCK") return { products: [], lineups: [] };
  const tea = snapshot.products.find((product) => product.id === "product-tea");
  if (!tea) return { products: snapshot.products, lineups: [] };
  const live = snapshot.liveSessions.find((session) => session.shopId === tea.shopId);
  if (!live) return { products: snapshot.products, lineups: [] };
  const photo = (id: string) => `https://images.unsplash.com/${id}?auto=format&fit=crop&w=240&q=80`;
  const rows = [
    { base: tea, specification: "清香型 · 250g × 2 罐", category: "茶叶", imageUrl: photo("photo-1544787219-7f47ccb76574"), units: 86, orders: 72, clicks: 914, gmv: 2385400, stock: tea.stock },
    { base: snapshot.products.find((p) => p.id === "product-gift") ?? tea, specification: "红茶 + 白茶 · 双罐礼盒", category: "礼盒", imageUrl: photo("photo-1515823064-d6e0c04616a7"), units: 31, orders: 28, clicks: 346, gmv: 1452300, stock: 26 },
    ...[
      { id: "demo-white", name: "高山白茶 · 清甜日饮装", skuId: "DEMO-BC-100", priceMinor: 12900, specification: "寿眉 · 100g / 罐", category: "茶叶", imageUrl: photo("photo-1571934811356-5cc061b6821f"), units: 67, orders: 62, clicks: 703, gmv: 798100, stock: 184 },
      { id: "demo-cup", name: "手作白瓷 · 品茗双杯", skuId: "DEMO-CUP-02", priceMinor: 7900, specification: "白瓷 · 60ml × 2", category: "茶具", imageUrl: photo("photo-1544787219-7f47ccb76574"), units: 42, orders: 40, clicks: 610, gmv: 301800, stock: 18 },
      { id: "demo-oolong", name: "桂花乌龙 · 三角茶包", skuId: "DEMO-WL-20", priceMinor: 5900, specification: "3g × 20 包", category: "茶包", imageUrl: photo("photo-1544787219-7f47ccb76574"), units: 38, orders: 35, clicks: 526, gmv: 212800, stock: 0 },
      { id: "demo-taste", name: "好茶初见 · 四味尝鲜装", skuId: "DEMO-TEA-04", priceMinor: 3990, specification: "4 口味 · 5g × 8 袋", category: "茶叶", imageUrl: photo("photo-1571934811356-5cc061b6821f"), units: 29, orders: 27, clicks: 458, gmv: 108710, stock: 216 },
      { id: "demo-travel", name: "随行茶具 · 一壶两杯", skuId: "DEMO-SET-03", priceMinor: 16900, specification: "旅行套装 · 雾白", category: "茶具", imageUrl: photo("photo-1515823064-d6e0c04616a7"), units: 0, orders: 0, clicks: 0, gmv: 0, stock: 62 },
    ].map((row) => ({ ...row, base: { ...tea, id: row.id, name: row.name, skuId: row.skuId, priceMinor: row.priceMinor } })),
  ];
  const updatedAt = `${snapshot.scope.date}T11:00:00+08:00`;
  const products: WorkspaceProduct[] = rows.map((row) => {
    const performance: ProductPerformance = { scope: "LIVE_SESSION", shopId: tea.shopId, liveSessionId: live.id, skuId: row.base.skuId, date: snapshot.scope.date, timezone: "Asia/Shanghai", currency: "CNY", clicks: row.clicks, paidOrders: row.orders, paidUnits: row.units, paidGmvMinor: row.gmv, sourceUpdatedAt: updatedAt,
      trend: [5, 8, 12, 10, 16, 11, 20, 18].map((weight, index) => ({ at: `${snapshot.scope.date}T${String(9 + Math.floor(index / 4)).padStart(2, "0")}:${String(index % 4 * 15).padStart(2, "0")}:00+08:00`, paidGmvMinor: Math.round(row.gmv * weight / 100), paidUnits: Math.round(row.units * weight / 100) })),
    };
    return { ...row.base, stock: row.stock, imageUrl: row.imageUrl, specification: row.specification, category: row.category, sourceUpdatedAt: updatedAt, stockWarningThreshold: 30,
      performance: [performance, { ...performance, scope: "SHOP", liveSessionId: undefined, clicks: row.clicks * 3, paidOrders: row.orders * 3, paidUnits: row.units * 3, paidGmvMinor: row.gmv * 3, trend: undefined }],
    };
  });
  const retained = snapshot.products.filter((product) => product.shopId !== tea.shopId);
  return { products: [...products, ...retained], lineups: [{ id: "demo-lineup", shopId: tea.shopId, liveSessionId: live.id, sourceUpdatedAt: updatedAt, items: products.slice(0, 6).map((product, index) => ({ productId: product.id, skuId: product.skuId, startMinute: index * 12, durationMinutes: 12, note: ["开场主推，说明产地与冲泡方式", "礼赠场景，展示包装与组合规格", "日常口粮，现场冲泡展示", "搭配演示，明确材质与容量", "先核对补货，库存为零时不排正式讲解", "收尾尝鲜，说明组合内容"][index] })) }] };
}
