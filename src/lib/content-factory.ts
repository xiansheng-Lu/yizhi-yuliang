import type { Identity, Product } from "./domain";
import type { AdvertisingProduct } from "./advertising";
import { validateAccountLinks } from "./content-delivery";

export const contentPages = ["planning", "scripts", "graphics", "videos", "editing", "assets"] as const;
export type ContentPage = (typeof contentPages)[number];
export type DocumentKind = Exclude<ContentPage, "assets">;
export const contentNames: Record<ContentPage, string> = { planning: "内容策划", scripts: "脚本工厂", graphics: "图片图文", videos: "视频工厂", editing: "智能剪辑", assets: "素材资产库" };
export function isContentPage(page: string): page is ContentPage { return contentPages.includes(page as ContentPage); }
export const uid = () => crypto.randomUUID();
export interface ContentOwner { mode: Identity["mode"]; tenantId: string; userId: string; shopId: string; }
export function contentOwnerKey(owner: ContentOwner) { return JSON.stringify(["content-v1", owner.mode, owner.tenantId, owner.userId, owner.shopId]); }
export interface GraphicLayer { id: string; type: "text" | "image"; text: string; assetId: string; x: number; y: number; width: number; height: number; fontSize: number; color: string; visible: boolean; locked: boolean; }
export interface Artboard { id: string; name: string; width: number; height: number; background: string; layers: GraphicLayer[]; x?: number; y?: number; }
export interface CreativeMessage { id: string; text: string; createdAt: string; context: string; assetIds?: string[]; }
export interface Scene { id: string; title: string; narration: string; visual: string; duration: number; }
export interface VideoNode { id: string; type: "reference" | "direction" | "generate" | "result"; x: number; y: number; text: string; assetId: string; }
export interface VideoEdge { id: string; sourceNodeId: string; targetNodeId: string; }
export interface Clip { id: string; assetId: string; track: "video" | "audio" | "caption"; start: number; sourceStart: number; duration: number; text: string; volume: number; }
export interface ContentDocument {
  id: string; kind: DocumentKind; title: string; productId: string; parentId: string | null;
  version: number; updatedAt: string; status: "draft" | "review";
  brief: { audience: string; objective: string; sellingPoints: string; channel: string; requirements: string; };
  body: string; scenes: Scene[]; boards: Artboard[]; nodes: VideoNode[]; edges: VideoEdge[]; clips: Clip[];
  settings: { ratio: string; duration: number; resolution: string; skill: string; }; prompt: string; messages?: CreativeMessage[];
}
export interface ContentAsset {
  id: string; name: string; kind: "image" | "video" | "audio" | "text"; productId: string; documentId: string | null;
  mime: string; size: number; duration: number; createdAt: string; text?: string; accountLinks?: ContentAccountLink[];
}
/** Local handoff intent. This is not a platform upload or delivery receipt. */
export interface ContentAccountLink {
  accountId: string; accountName: string; nativeId: string; productLine: AdvertisingProduct; businessEntityId: string;
  tenantId: string; shopId: string; mode: Identity["mode"]; linkedAt: string; linkedBy: string;
}
export interface ContentState { schema: 1; revision: number; documents: ContentDocument[]; assets: ContentAsset[]; }
export const emptyContent = (): ContentState => ({ schema: 1, revision: 0, documents: [], assets: [] });
export function createBoard(name = "画板 01"): Artboard { return { id: uid(), name, width: 1080, height: 1350, background: "#ffffff", layers: [] }; }
export function createDocument(kind: DocumentKind, productId = "", parent?: ContentDocument): ContentDocument {
  return {
    id: uid(), kind, title: `未命名${contentNames[kind]}`, productId: parent?.productId || productId, parentId: parent?.id ?? null,
    version: 1, updatedAt: new Date().toISOString(), status: "draft",
    brief: parent ? { ...parent.brief } : { audience: "", objective: "", sellingPoints: "", channel: "视频号", requirements: "" },
    body: parent?.body ?? "", scenes: parent?.scenes.map(scene => ({ ...scene, id: uid() })) ?? [],
    boards: kind === "graphics" ? [createBoard()] : [],
    nodes: kind === "videos" ? [
      { id: uid(), type: "reference", x: 35, y: 60, text: "商品与参考素材", assetId: "" },
      { id: uid(), type: "generate", x: 365, y: 60, text: parent?.scenes.map(s => `${s.visual}\n${s.narration}`).join("\n\n") || parent?.body || "", assetId: "" },
      { id: uid(), type: "result", x: 695, y: 60, text: "成片输出", assetId: "" },
    ] : [], edges: [], clips: [], settings: { ratio: "9:16", duration: 15, resolution: "1080P", skill: "口播精剪" }, prompt: "",
  };
}
export function flowDocument(source: ContentDocument, kind: DocumentKind): ContentDocument {
  const result = createDocument(kind, source.productId, source);
  result.title = `${source.title.replace(/ · (脚本|视频|图文|剪辑)$/, "")} · ${{ planning: "策划", scripts: "脚本", videos: "视频", graphics: "图文", editing: "剪辑" }[kind]}`;
  if (kind === "graphics") result.prompt = [source.brief.sellingPoints, source.body].filter(Boolean).join("\n");
  if (kind === "editing") result.settings = { ...source.settings };
  return result;
}
export function authorizedProducts(products: Product[], owner: ContentOwner) { return products.filter(p => p.tenantId === owner.tenantId && p.shopId === owner.shopId); }
export function timelineDuration(clips: Clip[]) { return Math.max(0, ...clips.map(c => c.start + c.duration)); }
export function splitClip(clips: Clip[], id: string, at: number): Clip[] {
  const clip = clips.find(c => c.id === id);
  if (!clip || !Number.isFinite(at) || at - clip.start < .05 || clip.start + clip.duration - at < .05) throw new Error("请将播放头移到片段内部，分割后每段至少保留 0.05 秒。");
  const duration = Math.round((at - clip.start) * 1000) / 1000;
  return clips.flatMap(c => c.id !== id ? [c] : [{ ...c, duration }, { ...c, id: uid(), start: at, sourceStart: c.sourceStart + duration, duration: c.duration - duration }]);
}
export function validateContent(state: ContentState, products: Product[]) {
  if (!state || state.schema !== 1 || !Number.isSafeInteger(state.revision) || state.revision < 0 || !Array.isArray(state.documents) || !Array.isArray(state.assets)) throw new Error("草稿格式不正确，请保留原数据后重试。");
  const productIds = new Set(products.map(p => p.id));
  const ids = new Set<string>();
  for (const row of [...state.documents, ...state.assets]) {
    if (!row || typeof row.id !== "string" || ids.has(row.id) || (row.productId && !productIds.has(row.productId))) throw new Error("草稿存在重复记录或未授权的商品关联。");
    ids.add(row.id);
  }
  const assets = new Map(state.assets.map(a => [a.id, a]));
  for (const asset of state.assets) {
    if (typeof asset.name !== "string" || !["image", "video", "audio", "text"].includes(asset.kind) || !Number.isFinite(asset.duration) || asset.duration < 0 || !Number.isFinite(asset.size) || asset.size < 0) throw new Error("素材元数据无效。");
  }
  validateAccountLinks(state);
  for (const doc of state.documents) {
    if (!contentPages.includes(doc.kind) || doc.kind === ("assets" as string) || typeof doc.title !== "string" || !doc.title.trim() || doc.title.length > 120 || !Number.isSafeInteger(doc.version) || doc.version < 1 || !Number.isFinite(Date.parse(doc.updatedAt)) || !Array.isArray(doc.boards) || !Array.isArray(doc.nodes) || !Array.isArray(doc.edges) || !Array.isArray(doc.clips) || !Array.isArray(doc.scenes) || !doc.brief || !doc.settings || typeof doc.body !== "string" || typeof doc.prompt !== "string" || !Object.values(doc.brief).every(v => typeof v === "string")) throw new Error("作品信息不完整。");
    if (!["9:16", "16:9", "1:1", "4:5"].includes(doc.settings.ratio) || !["720P", "1080P", "4K"].includes(doc.settings.resolution) || !Number.isFinite(doc.settings.duration) || doc.settings.duration < 1 || doc.settings.duration > 600) throw new Error("制作规格或时长无效。");
    if (doc.messages && (!Array.isArray(doc.messages) || doc.messages.some(m => !m || typeof m.id !== "string" || typeof m.text !== "string" || typeof m.context !== "string" || (m.assetIds && (!Array.isArray(m.assetIds) || m.assetIds.some(id => !assets.has(id)))) || !Number.isFinite(Date.parse(m.createdAt))))) throw new Error("创作对话记录无效。");
    const nodeIds = new Set(doc.nodes.map(n => n.id));
    if (nodeIds.size !== doc.nodes.length || doc.nodes.some(n => !["reference", "direction", "generate", "result"].includes(n.type) || ![n.x, n.y].every(Number.isFinite) || typeof n.text !== "string" || (n.assetId && !assets.has(n.assetId))) || doc.edges.some(e => !nodeIds.has(e.sourceNodeId) || !nodeIds.has(e.targetNodeId))) throw new Error("视频节点或连线引用无效。");
    for (const scene of doc.scenes) if (![scene.title, scene.narration, scene.visual].every(v => typeof v === "string") || !Number.isFinite(scene.duration) || scene.duration < .1 || scene.duration > 600) throw new Error("镜头信息需完整，时长需要在 0.1–600 秒之间。");
    for (const board of doc.boards) {
      if (![board.width, board.height].every(n => Number.isInteger(n) && n >= 100 && n <= 8192) || !/^#[\da-f]{6}$/i.test(board.background) || !Array.isArray(board.layers)) throw new Error("画板尺寸需为 100–8192 像素。");
      for (const layer of board.layers) if (!["text", "image"].includes(layer.type) || typeof layer.text !== "string" || !/^#[\da-f]{6}$/i.test(layer.color) || ![layer.x, layer.y, layer.width, layer.height, layer.fontSize].every(Number.isFinite) || layer.width <= 0 || layer.height <= 0 || layer.fontSize <= 0 || (layer.type === "image" && assets.get(layer.assetId)?.kind !== "image")) throw new Error("图层参数或图片引用无效。");
    }
    for (const clip of doc.clips) {
      const asset = assets.get(clip.assetId);
      if (![clip.start, clip.sourceStart, clip.duration, clip.volume].every(Number.isFinite) || clip.start < 0 || clip.sourceStart < 0 || clip.duration < .05 || clip.volume < 0 || clip.volume > 1 || !["video", "audio", "caption"].includes(clip.track) || (clip.track !== "caption" && (!asset || asset.kind !== clip.track || clip.sourceStart + clip.duration > asset.duration + .01))) throw new Error("剪辑超出素材边界或引用了不可用素材。");
    }
  }
}
export function formatSeconds(seconds: number) { return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${(seconds % 60).toFixed(1).padStart(4, "0")}`; }
