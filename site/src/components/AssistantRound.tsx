import { useState, type ReactNode } from "react";
/** Uncontrolled user choice survives SSE updates; a newly latest round opens by default. */
export default function AssistantRound({ latest, summary, children, className = "" }: {
  latest: boolean; summary: ReactNode; children: ReactNode; className?: string;
}) {
  const [choice, setChoice] = useState<boolean | null>(null);
  const open = choice ?? latest;
  return <details className={`assistant-round ${className}`} open={open}>
    <summary onClick={event => { event.preventDefault(); setChoice(!open); }}>{summary}</summary>
    <div className="assistant-round-body">{children}</div>
  </details>;
}
