import { useCallback, useEffect, useRef, useState, type PointerEvent } from "react";
import { assistantWidth } from "../lib/assistantPresentation";
import "../styles/assistant-compact.css";

export function useAssistantWidth(kind: "draft" | "workflow", defaultWidth: number) {
  const [node, setNode] = useState<HTMLElement | null>(null);
  const [preferred, setPreferred] = useState(defaultWidth);
  const [bounds, setBounds] = useState({ available: defaultWidth, viewport: 0 });
  const drag = useRef<{ x: number; width: number } | null>(null);
  const key = `assistant-width:${kind}`;
  useEffect(() => { try { const saved = Number(localStorage.getItem(key)); if (saved >= 320 && saved <= 640) setPreferred(saved); } catch { /* optional preference */ } }, [key]);
  useEffect(() => {
    if (!node) return;
    const host = node.closest<HTMLElement>(kind === "draft" ? ".agent-draft-workspace" : ".workflow-builder-shell");
    const measure = () => {
      const viewport = window.innerWidth;
      const grid = node.parentElement;
      const available = kind === "draft" && viewport > 820
        ? (grid?.clientWidth || viewport) - 220 - 48 - 320
        : viewport - (kind === "draft" ? 24 : 32);
      setBounds({ viewport, available: Math.max(0, available) });
    };
    measure();
    const observer = new ResizeObserver(measure); if (host) observer.observe(host);
    window.addEventListener("resize", measure);
    return () => { observer.disconnect(); window.removeEventListener("resize", measure); };
  }, [node, kind]);
  const size = assistantWidth(preferred, bounds.available, bounds.viewport);
  useEffect(() => {
    if (!node) return;
    const host = node.closest<HTMLElement>(kind === "draft" ? ".agent-draft-workspace" : ".workflow-builder-shell");
    const value = `${size.width}px`;
    node.style.setProperty("--assistant-width", value); host?.style.setProperty("--assistant-width", value);
    return () => { node.style.removeProperty("--assistant-width"); host?.style.removeProperty("--assistant-width"); };
  }, [node, kind, size.width]);
  const change = useCallback((width: number) => {
    const next = Math.max(320, Math.min(size.max, Math.round(width)));
    setPreferred(next); try { localStorage.setItem(key, String(next)); } catch { /* optional preference */ }
  }, [key, size.max]);
  return { ref: setNode, size, reset: () => change(defaultWidth),
    down: (event: PointerEvent<HTMLDivElement>) => {
      if (!size.resizable || event.button !== 0) return;
      event.preventDefault(); event.stopPropagation(); event.currentTarget.setPointerCapture(event.pointerId);
      drag.current = { x: event.clientX, width: size.width };
    },
    move: (event: PointerEvent<HTMLDivElement>) => { if (drag.current) change(drag.current.width + drag.current.x - event.clientX); },
    end: () => { drag.current = null; }, change };
}

export function AssistantResize({ control, locale }: { control: ReturnType<typeof useAssistantWidth>; locale: string }) {
  return <div className="assistant-resize" role="separator" aria-orientation="vertical" tabIndex={control.size.resizable ? 0 : -1}
    hidden={!control.size.resizable} aria-label={locale === "zh" ? "调整 AI 助手宽度" : "Resize AI assistant"}
    title={locale === "zh" ? "拖动或使用方向键调整宽度；双击恢复默认" : "Drag or use arrow keys to resize; double-click to reset"}
    aria-valuemin={320} aria-valuemax={Math.floor(control.size.max)} aria-valuenow={Math.round(control.size.width)}
    onPointerDown={control.down} onPointerMove={control.move} onPointerUp={control.end} onPointerCancel={control.end} onLostPointerCapture={control.end}
    onDoubleClick={control.reset} onKeyDown={event => {
      if (!["ArrowLeft", "ArrowRight", "Home"].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      if (event.key === "Home") control.reset(); else control.change(control.size.width + (event.key === "ArrowLeft" ? 16 : -16));
    }} />;
}
