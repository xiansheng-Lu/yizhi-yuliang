import test from "node:test";
import assert from "node:assert/strict";
import { authorizedProducts, contentDocumentMatchesPage, contentNames, contentOwnerKey, createDocument, emptyContent, flowDocument, splitClip, timelineDuration, validateContent, type Clip, type ContentAsset } from "../src/lib/content-factory";
import { calculateCoverSourceRect, clampZoom, wouldCreateCycle } from "../src/lib/nexora-canvas";
import { createMockSnapshot } from "../src/services/mock-data";
import { canvasScale, fitCanvas, zoomCanvas } from "../src/lib/canvas-viewport";

const products = createMockSnapshot().products;
test("planning opens legacy script drafts without changing saved IDs, versions, or asset references", () => {
  const planning = createDocument("planning"), script = flowDocument(planning, "scripts"), graphic = createDocument("graphics");
  script.version = 3; script.body = "已有脚本正文";
  script.scenes.push({ id: "shot", title: "开场", narration: "已有口播", visual: "商品特写", duration: 5 });
  const state = emptyContent(); state.documents.push(planning, script, graphic);
  state.assets.push({ id: "manuscript", name: "已有文稿", kind: "text", documentId: script.id, productId: "", text: script.body, mime: "text/plain", size: 18, duration: 0, createdAt: new Date().toISOString() });
  const restored = JSON.parse(JSON.stringify(state));
  assert.doesNotThrow(() => validateContent(restored, products));
  assert.deepEqual(restored.documents.filter((doc: typeof planning) => contentDocumentMatchesPage(doc, "planning")), [planning, script]);
  assert.equal(restored.documents.find((doc: typeof script) => doc.id === script.id && contentDocumentMatchesPage(doc, "planning")).version, 3);
  assert.equal(restored.assets[0].documentId, script.id);
  assert.equal(contentDocumentMatchesPage(script, "videos"), false);
  assert.deepEqual(restored, state);
});
test("planning sends its script and scenes directly to video and graphic creation", () => {
  const planning = createDocument("planning", products[0].id); planning.body = "开场、商品讲解和行动引导"; planning.brief.sellingPoints = "真实商品卖点";
  planning.scenes.push({ id: "shot", title: "开场", narration: "脚本口播", visual: "商品特写", duration: 5 });
  const video = flowDocument(planning, "videos"), graphics = flowDocument(planning, "graphics");
  assert.equal(video.parentId, planning.id); assert.equal(graphics.parentId, planning.id);
  assert.equal(video.body, planning.body); assert.equal(video.scenes[0].narration, "脚本口播");
  assert.match(video.nodes.find(node => node.type === "generate")!.text, /商品特写\n脚本口播/);
  assert.match(graphics.prompt, /真实商品卖点/); assert.match(graphics.prompt, /开场、商品讲解和行动引导/);
  video.scenes[0].narration = "改稿"; assert.equal(planning.scenes[0].narration, "脚本口播");
  assert.equal(contentNames.videos, "视频创作"); assert.equal(contentNames.graphics, "平面创作"); assert.equal(contentNames.assets, "素材库");
});
test("free canvas zoom preserves the world position under the cursor and fits negative coordinates", () => {
  const view = { x: -120, y: 50, scale: .5 }, point = { x: 300, y: 200 };
  const next = zoomCanvas(view, 2, point);
  assert.equal((point.x - view.x) / view.scale, (point.x - next.x) / next.scale);
  assert.equal((point.y - view.y) / view.scale, (point.y - next.y) / next.scale);
  const bounds = { x: -800, y: -400, width: 2000, height: 1500 }, fit = fitCanvas(bounds, 900, 700);
  assert.ok(bounds.x * fit.scale + fit.x >= 47);
  assert.ok((bounds.x + bounds.width) * fit.scale + fit.x <= 853);
  assert.ok(bounds.y * fit.scale + fit.y >= 47);
  assert.equal(canvasScale(0), .05); assert.equal(canvasScale(100), 4);
});
test("creative conversation persists with its document and remains compatible with older drafts", () => {
  const state = emptyContent(), doc = createDocument("graphics"); state.documents.push(doc);
  assert.doesNotThrow(() => validateContent(state, products));
  doc.messages = [{ id: "message", text: "突出秋日氛围", context: "当前画板", createdAt: new Date().toISOString() }];
  assert.doesNotThrow(() => validateContent(JSON.parse(JSON.stringify(state)), products));
  doc.messages[0].createdAt = "bad"; assert.throws(() => validateContent(state, products));
});
test("content drafts isolate mode, tenant, user, and shop without delimiter collisions", () => {
  const owner = { mode: "MOCK" as const, tenantId: "t:a", userId: "u", shopId: "s" };
  const keys = [owner, { ...owner, mode: "API" as const }, { ...owner, tenantId: "t", userId: "a:u" }, { ...owner, userId: "u2" }, { ...owner, shopId: "s2" }].map(contentOwnerKey);
  assert.equal(new Set(keys).size, keys.length);
  assert.ok(authorizedProducts(products, { ...owner, tenantId: "tenant-demo", shopId: "shop-tea" }).every(p => p.shopId === "shop-tea"));
});
test("planning to script to production carries the actual brief and script without mutating its source", () => {
  const planning = createDocument("planning", products[0].id); planning.title = "秋茶上新"; planning.brief.audience = "办公室人群"; planning.body = "产品使用场景";
  const script = flowDocument(planning, "scripts"); script.scenes.push({ id: "scene", title: "开场", narration: "一杯茶的时间", visual: "冲泡特写", duration: 5 });
  const video = flowDocument(script, "videos"), graphics = flowDocument(script, "graphics");
  assert.equal(script.parentId, planning.id); assert.equal(video.parentId, script.id); assert.equal(video.productId, planning.productId); assert.equal(video.brief.audience, "办公室人群"); assert.equal(video.scenes[0].narration, "一杯茶的时间"); assert.match(video.nodes.find(n => n.type === "generate")!.text, /冲泡特写/); assert.match(graphics.prompt, /产品使用场景/); assert.equal(planning.scenes.length, 0);
  video.brief.audience = "其他"; assert.equal(script.brief.audience, "办公室人群");
});
test("split keeps source in/out positions and total timeline duration", () => {
  const clips: Clip[] = [{ id: "clip", assetId: "video", track: "video", start: 2, sourceStart: 10, duration: 8, text: "片段", volume: 1 }];
  const split = splitClip(clips, "clip", 5);
  assert.equal(split.length, 2); assert.equal(split[0].duration, 3); assert.equal(split[1].sourceStart, 13); assert.equal(split[1].duration, 5); assert.equal(timelineDuration(split), 10); assert.equal(clips[0].duration, 8);
  assert.throws(() => splitClip(clips, "clip", 2)); assert.throws(() => splitClip(clips, "clip", 10)); assert.throws(() => splitClip(clips, "missing", 5));
});
test("NEXORA canvas geometry crops symmetrically and rejects cyclic graph connections", () => {
  assert.deepEqual(calculateCoverSourceRect({ naturalWidth: 1920, naturalHeight: 1080 }, { x: 0, y: 0, width: 1, height: 1 }, 1080, 1080), { x: 420, y: 0, width: 1080, height: 1080 });
  const edges = [{ id: "e1", sourceNodeId: "a", targetNodeId: "b" }, { id: "e2", sourceNodeId: "b", targetNodeId: "c" }];
  assert.equal(wouldCreateCycle(edges, "c", "a"), true); assert.equal(wouldCreateCycle(edges, "a", "c"), false); assert.equal(wouldCreateCycle(edges, "a", "a"), true); assert.equal(clampZoom(99), 1.6); assert.equal(clampZoom(0), .45);
});
test("draft validation rejects unauthorized references and clips outside source media", () => {
  const state = emptyContent(), doc = createDocument("editing", products[0].id);
  const asset: ContentAsset = { id: "video", name: "测试.mp4", productId: products[0].id, documentId: doc.id, kind: "video", mime: "video/mp4", size: 100, duration: 10, createdAt: new Date().toISOString() };
  doc.clips.push({ id: "clip", assetId: asset.id, track: "video", start: 0, sourceStart: 2, duration: 8, text: "测试", volume: 1 }); state.documents.push(doc); state.assets.push(asset);
  assert.doesNotThrow(() => validateContent(state, products)); doc.clips[0].duration = 9; assert.throws(() => validateContent(state, products)); doc.clips[0].duration = 8; doc.productId = "foreign"; assert.throws(() => validateContent(state, products));
});
test("invalid image geometry cannot be saved or exported as a valid project", () => {
  const state = emptyContent(), doc = createDocument("graphics"); state.documents.push(doc); assert.doesNotThrow(() => validateContent(state, products)); doc.boards[0].width = 0; assert.throws(() => validateContent(state, products));
});
