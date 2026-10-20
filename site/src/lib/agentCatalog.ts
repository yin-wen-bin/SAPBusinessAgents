import type { ResourceState } from './agentManagement';

export type CatalogSnapshot = { id: number; instance: string; available: boolean; checked_at: string | null;
  stale: boolean; refreshing: boolean; error_code: string | null };
type CatalogResponse = { items: any[]; snapshot: CatalogSnapshot };
const retained = new Map<string, { data: any[]; snapshot: CatalogSnapshot }>();

/** A display snapshot never contains operation authorization. */
export class CatalogResource {
  state: ResourceState = { data: [], loading: false, loaded: false, error: '', updatedAt: null };
  snapshot?: CatalogSnapshot;
  private pending?: Promise<void>;
  private generation = 0;
  private controller?: AbortController;
  private key: string;
  private fetcher: (signal: AbortSignal, force: boolean) => Promise<CatalogResponse>;
  private changed: () => void;
  private clock: () => number;
  constructor(key: string, fetcher: (signal: AbortSignal, force: boolean) => Promise<CatalogResponse>,
    changed: () => void, clock = Date.now) {
    this.key = key; this.fetcher = fetcher; this.changed = changed; this.clock = clock;
    const previous = retained.get(key);
    if (previous) {
      this.snapshot = { ...previous.snapshot };
      this.state = { ...this.state, data: previous.data, loaded: true, updatedAt: Date.parse(previous.snapshot.checked_at || '') || null };
    }
  }
  fresh() {
    return this.state.loaded && !this.state.error && !this.snapshot?.stale && !this.snapshot?.refreshing
      && this.state.updatedAt !== null && this.clock() - this.state.updatedAt < 60000;
  }
  refresh(force = false): Promise<void> {
    if (this.pending) return this.pending;
    if (!force && this.fresh()) return Promise.resolve();
    const generation = ++this.generation;
    this.controller = new AbortController();
    const signal = this.controller.signal;
    this.state = { ...this.state, loading: true };
    this.changed();
    this.pending = Promise.resolve().then(async () => {
      try {
        const result = await this.fetcher(signal, force);
        if (!Array.isArray(result.items) || typeof result.snapshot?.id !== 'number') throw Error('Invalid catalog response');
        if (generation !== this.generation) return;
        if (this.snapshot?.instance === result.snapshot.instance && result.snapshot.id < this.snapshot.id) return;
        this.snapshot = result.snapshot;
        this.state = { data: result.snapshot.available ? result.items : this.state.data,
          loaded: result.snapshot.available || this.state.loaded, loading: false,
          updatedAt: result.snapshot.checked_at ? Date.parse(result.snapshot.checked_at) : this.state.updatedAt,
          error: result.snapshot.error_code || '' };
        if (result.snapshot.available) retained.set(this.key, { data: this.state.data, snapshot: { ...result.snapshot } });
      } catch (failure: any) {
        if (generation === this.generation) this.state = { ...this.state, error: failure?.message || 'Catalog load failed' };
      } finally {
        if (generation === this.generation) {
          this.pending = undefined;
          this.state = { ...this.state, loading: false };
          this.changed();
        }
      }
    });
    return this.pending;
  }
  invalidate() {
    if (this.snapshot) this.snapshot = { ...this.snapshot, stale: true };
  }
  dispose() {
    ++this.generation;
    this.controller?.abort();
    this.pending = undefined;
    this.state = { ...this.state, loading: false };
  }
}

export const catalogPollingDelay = (attempt: number) => [1000, 2000, 5000][Math.min(attempt, 2)];
