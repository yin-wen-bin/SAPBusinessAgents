/** Serialize read-only refreshes and retain the latest event during a burst. */
type Read = (signal: AbortSignal) => Promise<void>;

export function createWorkflowRefresh(onError: (error: unknown) => void) {
  let pending: { key: string; read: Read } | undefined;
  let active: Promise<void> | undefined;
  let controller: AbortController | undefined;
  let activeKey: string | undefined;
  const drain = async () => {
    while (pending) {
      const next = pending;
      pending = undefined;
      const owned = new AbortController();
      controller = owned;
      activeKey = next.key;
      try { await next.read(owned.signal); }
      catch (error) { if (!owned.signal.aborted) onError(error); }
      finally { if (controller === owned) controller = undefined; }
    }
  };
  const start = (): Promise<void> => {
    active = Promise.resolve().then(drain).finally(() => {
      active = undefined;
      if (pending) return start();
    });
    return active;
  };
  return {
    request(key: string, read: Read): Promise<void> {
      pending = { key, read };
      if (controller && key !== activeKey) controller.abort();
      return active ?? start();
    },
    cancel() { pending = undefined; controller?.abort(); },
  };
}
