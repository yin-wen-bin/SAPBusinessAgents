import { useEffect, useRef, useState } from "react";

// Display preparation only. Campaign admission always revalidates the digest.
export function useAcceptanceRuntimePreview(base: string, draftId: string, revision: number, enabled: boolean) {
  const key = `${base}:${draftId}:${revision}`;
  const identity = useRef({ key, generation: 0 });
  if (identity.current.key !== key) identity.current = { key, generation: identity.current.generation + 1 };
  const generation = identity.current.generation;
  const pending = useRef<{ generation: number; promise: Promise<void> } | null>(null);
  const checked = useRef({ key: "", at: 0 });
  const mounted = useRef(true);
  const [state, setState] = useState<any>({ key, checking: false });
  const read = (force = true): Promise<void> => {
    if (pending.current?.generation === generation) return pending.current.promise;
    if (!force && checked.current.key === key && Date.now() - checked.current.at < 5000) return Promise.resolve();
    setState((old: any) => ({ ...(old.key === key ? old : {}), key, checking: true, error: "", startedAt: Date.now() }));
    const ticket = { generation, promise: Promise.resolve() };
    pending.current = ticket;
    const promise = (async () => {
      try {
        const response = await fetch(`${base}?view=acceptance_setup`, { cache: "no-store" });
        const value = await response.json();
        if (!response.ok) throw new Error(value.detail?.code || "acceptance_runtime_read_failed");
        if (value.draft_id !== draftId || value.revision !== revision) throw new Error("agent_draft_revision_conflict");
        if (!value.formal_acceptance_runtime_digest || value.formal_acceptance_runtime?.available === false) {
          throw new Error(value.formal_acceptance_runtime?.error?.code || "runtime_not_selectable");
        }
        if (!mounted.current || identity.current.generation !== generation) return;
        checked.current = { key, at: Date.now() };
        setState({ key, checking: false, error: "", runtime: value.formal_acceptance_runtime, digest: value.formal_acceptance_runtime_digest });
      } catch (failure) {
        if (!mounted.current || identity.current.generation !== generation) return;
        checked.current = { key: "", at: 0 };
        setState((old: any) => ({ ...(old.key === key ? old : {}), key, checking: false,
          error: failure instanceof Error ? failure.message : "acceptance_runtime_read_failed" }));
      } finally {
        if (pending.current === ticket) pending.current = null;
      }
    })();
    ticket.promise = promise;
    return promise;
  };
  useEffect(() => {
    if (!enabled) return;
    void read(false);
    const visible = () => { if (!document.hidden) void read(false); };
    window.addEventListener("focus", visible);
    document.addEventListener("visibilitychange", visible);
    return () => { window.removeEventListener("focus", visible); document.removeEventListener("visibilitychange", visible); };
  }, [key, enabled]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const current = state.key === key ? state : { checking: enabled };
  return { ...current, ready: Boolean(current.digest && !current.checking && !current.error), read };
}
