export interface CanvasView { x: number; y: number; scale: number; }
export interface CanvasBounds { x: number; y: number; width: number; height: number; }
export const canvasScale = (value: number) => Math.max(.05, Math.min(4, value));
export function zoomCanvas(view: CanvasView, next: number, point: { x: number; y: number }): CanvasView {
  const scale = canvasScale(next), ratio = scale / view.scale;
  return { x: point.x - (point.x - view.x) * ratio, y: point.y - (point.y - view.y) * ratio, scale };
}
export function fitCanvas(bounds: CanvasBounds, width: number, height: number): CanvasView {
  const scale = canvasScale(Math.min((Math.max(100, width) - 96) / Math.max(1, bounds.width), (Math.max(100, height) - 96) / Math.max(1, bounds.height), 1));
  return { x: (width - bounds.width * scale) / 2 - bounds.x * scale, y: (height - bounds.height * scale) / 2 - bounds.y * scale, scale };
}
