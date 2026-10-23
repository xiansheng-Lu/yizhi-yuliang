import test from "node:test";
import assert from "node:assert/strict";
import { authorizedProducts, contentOwnerKey, createDocument, emptyContent, flowDocument, splitClip, timelineDuration, validateContent, type Clip, type ContentAsset } from "../src/lib/content-factory";
import { calculateCoverSourceRect, clampZoom, wouldCreateCycle } from "../src/lib/nexora-canvas";
import { createMockSnapshot } from "../src/services/mock-data";

const products = createMockSnapshot().products;
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
