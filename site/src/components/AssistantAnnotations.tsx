import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { AnnotationHost, AnnotationItem } from "../lib/assistantAnnotations";
import { annotationCommentLimit, annotationLimit, upsertAnnotation } from "../lib/assistantAnnotations";
import type { Locale } from "../lib/types";
import "../styles/assistant-annotations.css";

type Options<R> = { host: AnnotationHost<R>; locale: Locale; enabled: boolean; disabledReason?: string;
  active: boolean; onActive: (active: boolean) => void; items: AnnotationItem<R>[];
  onItems: (items: AnnotationItem<R>[]) => void; locked?: boolean; canSend: boolean;
  onStart?: () => void; onExit?: () => void; onSend: () => void };
type Rect = { left: number; top: number; width: number; height: number };
const rectOf = (element: Element): Rect => {
  const { left, top, width, height } = element.getBoundingClientRect();
  return { left, top, width, height };
};

/** The same controller and overlay serve every host. No SDK or business authorization lives here. */
export function useAssistantAnnotations<R>(options: Options<R>) {
  const latest = useRef(options); latest.current = options;
  const [editor, setEditor] = useState<AnnotationItem<R> | null>(null);
  const [comment, setComment] = useState("");
  const [review, setReview] = useState(false);
  const [notice, setNotice] = useState("");
  const [hover, setHover] = useState<Rect | null>(null);
  const [bounds, setBounds] = useState<{ key: string; rect: Rect }[]>([]);
  const [anchor, setAnchor] = useState<Rect | null>(null);
  const scope = useRef(options.host.scopeKey);
  // A page may render the same business field both in a summary and an editor.
  // Remember the explicitly clicked view locally, never in the submitted reference.
  const preferred = useRef(new Map<string, Element>());
  const editorRef = useRef(editor); editorRef.current = editor;
  const commentRef = useRef(comment); commentRef.current = comment;
  const tr = (zh: string, en: string) => options.locale === "zh" ? zh : en;
  const hasUnsent = options.items.length > 0 || Boolean(editor && comment.trim());
  const stale = options.items.some(item => options.host.stale(item.reference));

  const closeEditor = () => { setEditor(null); setComment(""); };
  const find = (item: Pick<AnnotationItem<R>, "key" | "reference">) => {
    const o = latest.current, selected = preferred.current.get(item.key);
    return selected && o.host.root()?.contains(selected) && o.host.resolve(selected)?.key === item.key
      ? selected : o.host.find(item.reference);
  };
  const edit = (item: AnnotationItem<R>) => {
    if (latest.current.locked) return;
    setEditor(item); setComment(item.comment); setReview(false); setNotice("");
    const target = find(item);
    setAnchor(target ? rectOf(target) : null);
  };
  const select = (element: Element) => {
    const o = latest.current;
    if (!o.enabled || o.locked) return;
    const target = o.host.resolve(element);
    if (!target) { setNotice(tr("此对象不能通过当前助手修改。", "This object cannot be revised by this assistant.")); return; }
    if (editorRef.current?.key === target.key) return;
    if (editorRef.current && commentRef.current.trim() && commentRef.current !== editorRef.current.comment) {
      setNotice(tr("请先保存或取消当前意见，再选择其他对象。", "Save or cancel the current comment before selecting another target.")); return;
    }
    if (target.element) preferred.current.set(target.key, target.element);
    const old = o.items.find(item => item.key === target.key);
    if (!old && o.items.length >= annotationLimit) { setNotice(tr("每批最多标注20处。", "At most 20 annotations per batch.")); return; }
    // Explicitly selecting a stale object starts a fresh reference, without migrating its old comment.
    edit(old && !o.host.stale(old.reference) ? old : { key: target.key, label: target.label, reference: target.reference, comment: "" });
  };
  const start = (reviewOnly = false) => {
    if ((!options.enabled && !reviewOnly) || options.locked) return;
    setNotice(""); options.onActive(true); options.onStart?.();
  };
  const exit = () => { setReview(false); setHover(null); options.onActive(false); options.onExit?.(); };
  const save = () => {
    if (!editor || latest.current.locked) return;
    if (latest.current.host.stale(editor.reference)) { setNotice(tr("对象已变化，请重新选择并核对意见。", "The target changed. Select it again and review the comment.")); return; }
    try { latest.current.onItems(upsertAnnotation(latest.current.items, { ...editor, comment })); closeEditor(); setNotice(""); }
    catch { setNotice(tr("请填写1至2000字的意见，每批最多20处。", "Enter 1–2000 characters, with at most 20 targets.")); }
  };
  const locate = (item: AnnotationItem<R>) => {
    const o = latest.current;
    if (o.host.stale(item.reference)) { setNotice(tr("标注已过期，请在当前修订重新选择。", "This annotation is stale. Select its target in the current revision.")); return; }
    o.host.reveal?.(item.reference);
    requestAnimationFrame(() => {
      const target = find(item);
      target?.scrollIntoView({ block: "center", behavior: "smooth" });
      if (target instanceof HTMLElement) target.focus({ preventScroll: true });
    });
  };
  const confirmLeave = () => !hasUnsent || window.confirm(tr("还有未发送的标注意见，确定离开？", "There are unsent annotations. Leave this object?"));

  useEffect(() => {
    if (scope.current === options.host.scopeKey) return;
    scope.current = options.host.scopeKey;
    preferred.current.clear();
    options.onItems([]); options.onActive(false); closeEditor(); setReview(false); setNotice("");
  }, [options.host.scopeKey]);

  useEffect(() => {
    if (!hasUnsent) return;
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    const leavingLink = (event: MouseEvent) => {
      const link = (event.target as Element)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!link || link.target === "_blank" || link.download || link.href === location.href || link.hash && link.pathname === location.pathname) return;
      if (!confirmLeave()) { event.preventDefault(); event.stopImmediatePropagation(); }
    };
    window.addEventListener("beforeunload", unload); document.addEventListener("click", leavingLink, true);
    return () => { window.removeEventListener("beforeunload", unload); document.removeEventListener("click", leavingLink, true); };
  }, [hasUnsent, options.locale]);

  useEffect(() => {
    if (!options.active) return;
    const root = options.host.root();
    if (!root) return;
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const o = latest.current;
        setBounds(o.items.flatMap(item => {
          const element = !o.host.stale(item.reference) && find(item);
          return element ? [{ key: item.key, rect: rectOf(element) }] : [];
        }));
        const selected = editorRef.current && find(editorRef.current);
        setAnchor(selected ? rectOf(selected) : null);
      });
    };
    const targetOf = (event: Event) => event.target instanceof Element ? event.target : null;
    const ours = (target: Element | null) => Boolean(target && root.contains(target)
      && !target.closest("[data-assistant-annotation-ui], [data-annotation-navigation], summary"));
    const move = (event: PointerEvent) => {
      const target = targetOf(event);
      if (!ours(target) || editorRef.current) { setHover(null); return; }
      const item = latest.current.host.resolve(target!);
      const element = item?.element || (item && latest.current.host.find(item.reference));
      setHover(element ? rectOf(element) : null);
    };
    const suppress = (event: Event) => {
      if (!ours(targetOf(event))) return;
      event.preventDefault(); event.stopImmediatePropagation();
    };
    const click = (event: MouseEvent) => {
      const target = targetOf(event);
      if (!ours(target)) return;
      suppress(event); select(target!);
    };
    const keyboard = (event: KeyboardEvent) => {
      const target = targetOf(event);
      if (target?.closest("[data-assistant-annotation-ui]")) return;
      if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); exit(); return; }
      if (ours(target) && (event.key === "Enter" || event.key === " ")) { suppress(event); select(target!); return; }
      // Picking must not type into inputs, delete graph nodes, paste, or trigger native shortcuts.
      if (ours(target) && !["Tab", "Shift", "Control", "Alt", "Meta", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "PageUp", "PageDown", "Home", "End"].includes(event.key)) suppress(event);
    };
    root.classList.add("assistant-annotation-surface");
    document.addEventListener("pointermove", move, true);
    document.addEventListener("pointerdown", suppress, true);
    document.addEventListener("dblclick", suppress, true);
    document.addEventListener("click", click, true);
    document.addEventListener("keydown", keyboard, true);
    window.addEventListener("scroll", update, true); window.addEventListener("resize", update);
    const resize = new ResizeObserver(update); resize.observe(root);
    const mutations = new MutationObserver(update); mutations.observe(root, { childList: true, subtree: true });
    update();
    return () => {
      root.classList.remove("assistant-annotation-surface"); cancelAnimationFrame(frame);
      document.removeEventListener("pointermove", move, true); document.removeEventListener("pointerdown", suppress, true);
      document.removeEventListener("dblclick", suppress, true); document.removeEventListener("click", click, true);
      document.removeEventListener("keydown", keyboard, true);
      window.removeEventListener("scroll", update, true); window.removeEventListener("resize", update);
      resize.disconnect(); mutations.disconnect();
    };
  }, [options.active, options.host.scopeKey, options.items, options.host.revision, options.locale]);

  return { ...options, editor, comment, setComment, review, setReview, notice, hover, bounds, anchor,
    stale, hasUnsent, start, exit, save, edit, locate, closeEditor, confirmLeave,
    remove: (key: string) => { if (!options.locked) { preferred.current.delete(key); options.onItems(options.items.filter(item => item.key !== key)); } },
    clear: () => { if (!options.locked) { preferred.current.clear(); options.onItems([]); closeEditor(); } },
    send: () => { if (options.canSend && !options.locked && !stale && options.items.length && !editor) options.onSend(); } };
}
export type AnnotationController<R> = ReturnType<typeof useAssistantAnnotations<R>>;

export function AnnotationButton<R>({ controller: c }: { controller: AnnotationController<R> }) {
  const label = c.locale === "zh" ? "页面标注" : "Annotate page";
  return <button type="button" className="assistant-annotation-trigger" aria-label={label} aria-pressed={c.active}
    disabled={!c.enabled || c.locked} title={!c.enabled ? c.disabledReason || label : label} onClick={() => c.start()}>
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M8 3H5a2 2 0 0 0-2 2v3m13-5h3a2 2 0 0 1 2 2v3M3 16v3a2 2 0 0 0 2 2h3m8-8 5 2-3 1-1 3-2-5Z" /></svg>
    <span>{label}</span>{c.items.length > 0 && <span>{c.items.length}</span>}
  </button>;
}

export function AnnotationPending<R>({ controller: c }: { controller: AnnotationController<R> }) {
  if (!c.hasUnsent) return null;
  return <button type="button" className="assistant-annotation-pending" disabled={c.locked} onClick={() => { c.start(true); c.setReview(true); }}>
    {!c.items.length ? c.locale === "zh" ? "有未保存的标注意见 · 继续编辑" : "Unfinished annotation · continue editing"
      : c.locale === "zh" ? `${c.items.length} 处标注${c.stale ? " · 有过期引用" : " · 查看与编辑"}` : `${c.items.length} annotations${c.stale ? " · stale targets" : " · review"}`}
  </button>;
}

export function AssistantAnnotationOverlay<R>({ controller: c }: { controller: AnnotationController<R> }) {
  const input = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => { if (c.editor && c.active) input.current?.focus(); }, [c.editor?.key, c.active]);
  if (!c.active || typeof document === "undefined") return null;
  const tr = (zh: string, en: string) => c.locale === "zh" ? zh : en;
  const small = window.matchMedia("(max-width: 720px)").matches;
  const top = Math.max(12, Math.min((c.anchor?.top || 80) + (c.anchor?.height || 0) + 8, window.innerHeight - 300));
  const left = Math.max(12, Math.min(c.anchor?.left || 12, window.innerWidth - 372));
  return createPortal(<div className="assistant-annotation-layer" data-assistant-annotation-ui>
    {c.hover && !c.editor && <div className="assistant-annotation-outline is-hover" style={c.hover} aria-hidden="true" />}
    {c.bounds.map(bound => { const index = c.items.findIndex(item => item.key === bound.key); return <div key={bound.key} className="assistant-annotation-outline" style={bound.rect}>
      <button type="button" aria-label={tr(`编辑标注 ${index + 1}`, `Edit annotation ${index + 1}`)} onClick={() => c.edit(c.items[index])}>{index + 1}</button>
    </div>; })}
    {c.editor && <section className="assistant-annotation-editor" role="dialog" aria-modal="false" aria-label={tr("填写标注意见", "Annotation comment")} style={small ? undefined : { top, left }}>
      <strong>{c.editor.label}</strong><label>{tr("修改意见", "Requested change")}<textarea ref={input} rows={4} maxLength={annotationCommentLimit} value={c.comment}
        disabled={c.locked} onChange={event => c.setComment(event.target.value)} onKeyDown={event => {
          if (event.key === "Escape") { event.stopPropagation(); c.closeEditor(); }
          if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); c.save(); }
        }} /></label>
      <div><button type="button" onClick={c.closeEditor}>{tr("取消", "Cancel")}</button><button type="button" disabled={!c.comment.trim() || c.locked} onClick={c.save}>{tr("保存意见", "Save comment")}</button></div>
    </section>}
    {c.review && !c.editor && <section className="assistant-annotation-review" role="dialog" aria-label={tr("本批标注意见", "Batch annotations")}>
      <header><strong>{tr("本批标注意见", "Batch annotations")}</strong><button type="button" onClick={() => c.setReview(false)} aria-label={tr("关闭列表", "Close list")}>×</button></header>
      {!c.items.length && <p>{tr("选择页面中的草稿内容，然后填写意见。", "Select draft content on the page and add a comment.")}</p>}
      <ol>{c.items.map(item => <li key={item.key}><strong>{item.label}</strong>{c.host.stale(item.reference) && <span role="status">{tr("已过期，请重新选择", "Stale; select again")}</span>}<p>{item.comment}</p><div>
        <button type="button" onClick={() => c.locate(item)}>{tr("定位", "Locate")}</button><button type="button" disabled={c.locked} onClick={() => c.edit(item)}>{tr("编辑", "Edit")}</button><button type="button" disabled={c.locked} onClick={() => c.remove(item.key)}>{tr("删除", "Remove")}</button>
      </div></li>)}</ol>
    </section>}
    <div className="assistant-annotation-toolbar" role="toolbar" aria-label={tr("页面标注操作", "Page annotation actions")}>
      <strong>{tr(`标注中 · ${c.items.length}`, `Annotating · ${c.items.length}`)}</strong>
      <button type="button" onClick={() => c.setReview(!c.review)}>{tr("查看列表", "Review")}</button>
      <button type="button" disabled={!c.items.length || c.locked} onClick={c.clear}>{tr("清空", "Clear")}</button>
      <button type="button" className="annotation-send" disabled={!c.items.length || Boolean(c.editor) || c.stale || !c.canSend || c.locked} onClick={c.send}>{tr("发送", "Send")}</button>
      <button type="button" onClick={c.exit} aria-label={tr("退出标注，保留意见", "Exit annotation; keep comments")}>×</button>
      {(c.notice || c.stale || !c.enabled) && <p role="status">{c.notice || (c.stale ? tr("存在过期标注，请重新选择后发送。", "Some annotations are stale. Select their targets again before sending.") : c.disabledReason)}</p>}
    </div>
  </div>, document.body);
}
