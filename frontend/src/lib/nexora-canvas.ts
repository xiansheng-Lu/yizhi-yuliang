// Ported from NEXORA graphic-workspace-logic.js and video-canvas-state.js.
// Keep the source workspace unchanged; coordinates and cycle checks retain its behavior.
import type { VideoEdge } from "./content-factory";
export const clampZoom = (value: number) => Math.min(1.6, Math.max(.45, value));
export function calculateCoverSourceRect(image: { naturalWidth: number; naturalHeight: number }, crop: { x: number; y: number; width: number; height: number }, targetWidth: number, targetHeight: number) {
  let x = crop.x * image.naturalWidth, y = crop.y * image.naturalHeight, width = crop.width * image.naturalWidth, height = crop.height * image.naturalHeight;
  if (width / height > targetWidth / targetHeight) { const nextWidth = height * targetWidth / targetHeight; x += (width - nextWidth) / 2; width = nextWidth; }
  else { const nextHeight = width * targetHeight / targetWidth; y += (height - nextHeight) / 2; height = nextHeight; }
  return { x, y, width, height };
}
export function connectionPath(from: { x: number; y: number }, to: { x: number; y: number }) {
  const bend = Math.max(70, Math.abs(to.x - from.x) * .42);
  return `M ${from.x} ${from.y} C ${from.x + bend} ${from.y}, ${to.x - bend} ${to.y}, ${to.x} ${to.y}`;
}
export function wouldCreateCycle(edges: VideoEdge[], sourceNodeId: string, targetNodeId: string) {
  const pending = [targetNodeId], visited = new Set<string>();
  while (pending.length) { const id = pending.pop()!; if (id === sourceNodeId) return true; if (visited.has(id)) continue; visited.add(id); for (const edge of edges) if (edge.sourceNodeId === id) pending.push(edge.targetNodeId); }
  return false;
}
