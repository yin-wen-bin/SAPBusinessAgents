export type ResourceState = {
  data: any[]; loading: boolean; loaded: boolean; error: string; updatedAt: number | null;
};

export class ListResource {
  state: ResourceState = { data: [], loading: false, loaded: false, error: "", updatedAt: null };
  private generation = 0;
  private controller?: AbortController;
  private pending?: Promise<void>;
  private fetcher: (signal: AbortSignal) => Promise<any[]>;
  private changed: (state: ResourceState) => void;
  constructor(fetcher: (signal: AbortSignal) => Promise<any[]>, changed: (state: ResourceState) => void) {
    this.fetcher = fetcher; this.changed = changed;
  }
  refresh(): Promise<void> {
    if (this.pending) return this.pending;
    const generation = ++this.generation;
    this.controller = new AbortController();
    const signal = this.controller.signal;
    this.state = { ...this.state, loading: true };
    this.changed(this.state);
    const task = async () => {
      try {
        const data = await this.fetcher(signal);
        if (!Array.isArray(data)) throw new Error("Invalid list response");
        if (generation !== this.generation) return;
        this.state = { data, loading: false, loaded: true, error: "", updatedAt: Date.now() };
      } catch (error: any) {
        if (generation !== this.generation) return;
        this.state = { ...this.state, loading: false, error: error?.message || "Load failed" };
      } finally {
        if (generation === this.generation) {
          this.pending = undefined;
          this.changed(this.state);
        }
      }
    };
    // Keep the pending handle set even for a synchronous fetcher failure.
    this.pending = Promise.resolve().then(task);
    return this.pending;
  }
  dispose() {
    ++this.generation;
    this.controller?.abort();
    this.pending = undefined;
    this.state = { ...this.state, loading: false };
  }
}

export function acceptanceState(item: any): string {
  if (item.sync_error) return "UNRECORDED";
  const verdict = String(item.validation?.verdict || "").toUpperCase();
  if (verdict === "PENDING" || item.validation?.status === "running") return "PENDING";
  return ["PASS", "PARTIAL", "BLOCKED", "FAIL", "INCONCLUSIVE", "NOT_TESTED"].includes(verdict) ? verdict : "UNRECORDED";
}

export function managementRows(catalog: any[], drafts: any[], locale: string) {
  const local = (value: any) => String(value?.[locale] || value?.zh || value?.en || value || "");
  return [
    ...catalog.map((item) => ({ ...item, kind: "agent", rowKey: `agent:${item.id}`, state: item.lifecycle?.state || "unknown" })),
    ...drafts.map((item) => ({ ...item, kind: "draft", rowKey: `draft:${item.draft_id}`, state: "unpublished" })),
  ].map((item) => ({ ...item, acceptance: acceptanceState(item) })).sort((a, b) =>
    String(a.module || "").localeCompare(String(b.module || "")) ||
    local(a.title).localeCompare(local(b.title), locale) ||
    Number(a.kind === "draft") - Number(b.kind === "draft") ||
    (a.kind === "draft" ? String(b.updated_at || "").localeCompare(String(a.updated_at || "")) : 0) ||
    a.rowKey.localeCompare(b.rowKey)
  );
}

export function filterManagementRows(rows: any[], filters: { module: string; state: string; acceptance: string }) {
  return rows.filter((item) =>
    (!filters.module || (item.module || "unknown") === filters.module) &&
    (!filters.state || item.state === filters.state) &&
    (!filters.acceptance || item.acceptance === filters.acceptance)
  );
}

export function pollingDelay(failures: number): number { return Math.min(30000, 5000 * 2 ** failures); }
