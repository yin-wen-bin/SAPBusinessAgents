import { useRef, useState, type ReactNode } from "react";

export default function AssistantHistory({ entries, locale }: { locale: string; entries: {
  id: string; latest: boolean; label: string; attention?: boolean; audit?: boolean; content: ReactNode;
}[] }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const old = entries.filter(entry => !entry.latest);
  const locate = (id: string) => {
    setOpen(true);
    requestAnimationFrame(() => {
      const target = Array.from(root.current?.querySelectorAll<HTMLElement>("[data-history-id]") || []).find(node => node.dataset.historyId === id);
      const disclosure = target?.querySelector<HTMLDetailsElement>("details.assistant-round");
      if (disclosure && !disclosure.open) disclosure.querySelector<HTMLElement>("summary")?.click();
      target?.scrollIntoView({ block: "nearest" }); target?.focus();
    });
  };
  return <div ref={root} className="assistant-history">
    {old.length > 0 && <button type="button" className="assistant-history-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
      {open ? "▾ " : "▸ "}{locale === "zh" ? "历史对话" : "Conversation history"} ({old.filter(entry => !entry.audit).length})
    </button>}
    {old.some(entry => entry.attention) && <div className="assistant-history-attention" role="status">
      {locale === "zh" ? "历史中有待处理事项：" : "Items needing attention: "}
      {old.filter(entry => entry.attention).map(entry => <button type="button" key={entry.id} onClick={() => locate(entry.id)}>{entry.label}</button>)}
    </div>}
    {entries.map(entry => <div key={entry.id} data-history-id={entry.id} tabIndex={-1}
      className={entry.latest ? "assistant-history-latest" : "assistant-history-older"} hidden={!entry.latest && !open}>
      {entry.content}
    </div>)}
  </div>;
}
