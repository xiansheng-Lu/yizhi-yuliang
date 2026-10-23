import type { ContentOwner, ContentState } from "../lib/content-factory";
import { contentOwnerKey, emptyContent } from "../lib/content-factory";

let connection: Promise<IDBDatabase> | undefined;
function database() {
  return connection ??= new Promise((resolve, reject) => {
    const request = indexedDB.open("yuliang-content-v1", 1);
    request.onupgradeneeded = () => { request.result.createObjectStore("drafts"); request.result.createObjectStore("files"); };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => { connection = undefined; reject(new Error("无法打开浏览器草稿存储，请检查浏览器权限。")); };
    request.onblocked = () => { connection = undefined; reject(new Error("草稿存储正在被其他页面更新，请关闭旧页面后重试。")); };
  });
}
export async function readContentDraft(owner: ContentOwner): Promise<ContentState> {
  const db = await database();
  return new Promise((resolve, reject) => { const request = db.transaction("drafts").objectStore("drafts").get(contentOwnerKey(owner)); request.onsuccess = () => resolve(request.result ?? emptyContent()); request.onerror = () => reject(new Error("读取草稿失败，原数据未被覆盖。")); });
}
/** Revision and file writes share one transaction, including quota failures. */
export async function saveContentDraft(owner: ContentOwner, state: ContentState, files = new Map<string, Blob>()): Promise<ContentState> {
  const db = await database(), key = contentOwnerKey(owner);
  return new Promise((resolve, reject) => {
    const tx = db.transaction(["drafts", "files"], "readwrite"), drafts = tx.objectStore("drafts");
    let conflict = false;
    const next = { ...state, revision: state.revision + 1 };
    const read = drafts.get(key);
    read.onsuccess = () => {
      if ((read.result?.revision ?? 0) !== state.revision) { conflict = true; tx.abort(); return; }
      drafts.put(next, key);
      for (const [id, file] of files) tx.objectStore("files").put(file, JSON.stringify([key, id]));
    };
    tx.oncomplete = () => resolve(next);
    tx.onabort = tx.onerror = () => reject(new Error(conflict ? "另一页面已更新此工作区。请先下载草稿备份，再刷新核对。" : "草稿未保存，浏览器空间或权限不足。请下载备份后重试。"));
  });
}
export async function readContentFile(owner: ContentOwner, id: string): Promise<Blob | undefined> {
  const db = await database();
  return new Promise((resolve, reject) => { const request = db.transaction("files").objectStore("files").get(JSON.stringify([contentOwnerKey(owner), id])); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(new Error("读取本地素材失败，请重新导入。")); });
}
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob), link = document.createElement("a"); link.href = url; link.download = filename.replace(/[<>:"/\\|?*]/g, "_"); document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export async function inspectContentFile(file: File) {
  if (!file.size || file.size > 300 * 1024 * 1024) throw new Error("请选择非空且不超过 300 MB 的图片、视频或音频。");
  const kind: "image" | "video" | "audio" | null = file.type.startsWith("image/") ? "image" : file.type.startsWith("video/") ? "video" : file.type.startsWith("audio/") ? "audio" : null;
  if (!kind || file.type === "image/svg+xml") throw new Error("支持 PNG、JPEG、WebP 等位图，以及浏览器可解码的视频和音频。");
  const url = URL.createObjectURL(file);
  try {
    if (kind === "image") { const image = new Image(); image.src = url; await image.decode(); return { kind, duration: 0 } as const; }
    const media = document.createElement(kind);
    const duration = await new Promise<number>((resolve, reject) => {
      const timer = setTimeout(() => { media.removeAttribute("src"); media.load(); reject(new Error("素材读取超时，请更换文件后重试。")); }, 15000);
      media.preload = "metadata";
      media.onloadedmetadata = () => { clearTimeout(timer); if (Number.isFinite(media.duration) && media.duration > 0) resolve(media.duration); else reject(new Error("无法识别素材时长。")); media.removeAttribute("src"); media.load(); };
      media.onerror = () => { clearTimeout(timer); reject(new Error("浏览器无法解码此素材，请转换为 MP4 / H.264 或常见音频格式。")); };
      media.src = url;
    });
    return { kind, duration } as const;
  } finally { URL.revokeObjectURL(url); }
}
