"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowsOut, Cursor, Hand, Minus, Plus } from "@phosphor-icons/react";
import { fitCanvas, zoomCanvas, type CanvasBounds, type CanvasView } from "@/lib/canvas-viewport";

export function FreeCanvas({ label, bounds, children, overlay, fitKey }: { label: string; bounds: CanvasBounds; children: (scale: number) => ReactNode; overlay?: ReactNode; fitKey?: string | number }) {
  const element = useRef<HTMLDivElement>(null), dragging = useRef<{ id: number; x: number; y: number; view: CanvasView } | null>(null), initialized = useRef(false), space = useRef(false), lastFitKey = useRef(fitKey);
  const [view, setView] = useState<CanvasView>({ x: 60, y: 60, scale: .4 }), [hand, setHand] = useState(false), [panning, setPanning] = useState(false);
  function fit() { if (element.current) setView(fitCanvas(bounds, element.current.clientWidth, element.current.clientHeight)); }
  function zoom(multiplier: number) { if (element.current) setView(v => zoomCanvas(v, v.scale * multiplier, { x: element.current!.clientWidth / 2, y: element.current!.clientHeight / 2 })); }
  useEffect(() => {
    const node = element.current;
    if (!node) return;
    if (lastFitKey.current !== fitKey) { initialized.current = false; lastFitKey.current = fitKey; }
    const observer = new ResizeObserver(() => { if (!initialized.current && node.clientWidth && node.clientHeight) { initialized.current = true; setView(fitCanvas(bounds, node.clientWidth, node.clientHeight)); } });
    observer.observe(node);
    const wheel = (event: WheelEvent) => {
      if ((event.target as HTMLElement).closest("textarea, input, select, [data-canvas-toolbar]")) return;
      event.preventDefault();
      if (event.shiftKey) { setView(v => ({ ...v, x: v.x - event.deltaX - event.deltaY })); return; }
      const rect = node.getBoundingClientRect();
      setView(v => zoomCanvas(v, v.scale * Math.exp(-event.deltaY * .002), { x: event.clientX - rect.left, y: event.clientY - rect.top }));
    };
    node.addEventListener("wheel", wheel, { passive: false });
    const keyDown = (e: KeyboardEvent) => { if (e.code === "Space" && (node.contains(document.activeElement) || node.matches(":hover")) && !(e.target as HTMLElement).closest("input,textarea,select,button,a,summary,[contenteditable=true]")) { space.current = true; setPanning(true); e.preventDefault(); } };
    const keyUp = (e: KeyboardEvent) => { if (e.code === "Space") { space.current = false; setPanning(false); } };
    const blur = () => { space.current = false; dragging.current = null; setPanning(false); };
    window.addEventListener("keydown", keyDown); window.addEventListener("keyup", keyUp); window.addEventListener("blur", blur);
    return () => { observer.disconnect(); node.removeEventListener("wheel", wheel); window.removeEventListener("keydown", keyDown); window.removeEventListener("keyup", keyUp); window.removeEventListener("blur", blur); };
  }, [bounds, fitKey]);
  return <div ref={element} className={`cf-free-canvas ${hand || panning ? "pan-mode" : ""}`} role="region" aria-label={label} tabIndex={0}
    style={{ backgroundSize: `${Math.max(8, 24 * view.scale)}px ${Math.max(8, 24 * view.scale)}px`, backgroundPosition: `${view.x}px ${view.y}px` }}
    onKeyDown={e => { if ((e.target as HTMLElement).closest("input,textarea,select")) return; if (["+", "=", "-", "0"].includes(e.key)) { e.preventDefault(); if (e.key === "0") fit(); else zoom(e.key === "-" ? 1 / 1.2 : 1.2); } }}
    onPointerDownCapture={e => { const target = e.target as HTMLElement; if (target.closest("[data-canvas-toolbar]") || dragging.current) return; const pan = e.button === 1 || space.current || hand || !target.closest("[data-canvas-object]"); if (!pan || (e.button !== 0 && e.button !== 1)) return; e.preventDefault(); e.stopPropagation(); e.currentTarget.focus({ preventScroll: true }); e.currentTarget.setPointerCapture(e.pointerId); dragging.current = { id: e.pointerId, x: e.clientX, y: e.clientY, view }; setPanning(true); }}
    onPointerMoveCapture={e => { const origin = dragging.current; if (!origin || origin.id !== e.pointerId) return; e.stopPropagation(); setView({ ...origin.view, x: origin.view.x + e.clientX - origin.x, y: origin.view.y + e.clientY - origin.y }); }}
    onPointerUpCapture={e => { if (dragging.current) { dragging.current = null; setPanning(space.current); e.stopPropagation(); } }} onPointerCancel={() => { dragging.current = null; setPanning(false); }}>
    <div className="cf-free-world" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` }} data-scale={view.scale}>{children(view.scale)}</div>
    {overlay && <div className="cf-canvas-overlay" data-canvas-toolbar>{overlay}</div>}
    <div className="cf-canvas-help" data-canvas-toolbar>滚轮缩放 · 空格 / 中键拖动 · 0 适应视图</div>
    <div className="cf-viewport-tools" data-canvas-toolbar><button className="cf-tool" aria-label="选择工具" aria-pressed={!hand} onClick={() => setHand(false)}><Cursor size={17} /></button><button className="cf-tool" aria-label="平移画布" aria-pressed={hand} onClick={() => setHand(!hand)}><Hand size={17} /></button><i /><button className="cf-tool" aria-label="缩小画布" onClick={() => zoom(1 / 1.2)}><Minus size={16} /></button><button className="cf-tool cf-scale-value" onClick={() => { if (element.current) setView(v => zoomCanvas(v, 1, { x: element.current!.clientWidth / 2, y: element.current!.clientHeight / 2 })); }}>{Math.round(view.scale * 100)}%</button><button className="cf-tool" aria-label="放大画布" onClick={() => zoom(1.2)}><Plus size={16} /></button><button className="cf-tool" aria-label="适应视图" onClick={fit}><ArrowsOut size={16} /></button></div>
  </div>;
}
