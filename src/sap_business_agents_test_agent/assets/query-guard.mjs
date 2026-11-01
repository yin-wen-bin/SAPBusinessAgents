// Query-only access to an existing platform. No account/lifecycle mutations.
export class QueryWriteGuard {
  runs = new Set(); sessions = new Set(); submitted = false; sessionSource = null;
  allow(method, path, body = {}) {
    if (['GET','HEAD','OPTIONS'].includes(method)) return true;
    if (method !== 'POST') return false;
    if (!body || typeof body !== 'object' || Array.isArray(body)) return false;
    if (path === '/api/runs') {
      if (this.submitted || body.mode !== 'free_query' || typeof body.query !== 'string'
          || Object.keys(body).some(key => !['mode','query'].includes(key))) return false;
      this.submitted = true;
      return true;
    }
    if (path === '/api/free-query-sessions') {
      this.sessionSource = body.sourceRunId || body.runId || body.run_id;
      return this.runs.has(this.sessionSource);
    }
    const run = path.match(/^\/api\/runs\/([^/]+)\/(input|cancel)$/);
    if (run) return this.runs.has(decodeURIComponent(run[1]));
    const session = path.match(/^\/api\/free-query-sessions\/([^/]+)\/(feedback|feedback-input|feedback-requests\/[^/]+\/cancel)$/);
    return !!session && this.sessions.has(decodeURIComponent(session[1]));
  }
  observe(path, body) {
    if (!body || typeof body !== 'object') return;
    const session = path.match(/^\/api\/free-query-sessions\/([^/]+)(?:\/|$)/);
    const run = path.match(/^\/api\/runs\/([^/]+)(?:\/|$)/);
    const initial = path === '/api/runs' && this.submitted;
    const trusted = initial || session && this.sessions.has(decodeURIComponent(session[1]))
      || run && this.runs.has(decodeURIComponent(run[1]))
      || path === '/api/free-query-sessions' && this.runs.has(this.sessionSource);
    if (!trusted) return;
    if (typeof body.run_id === 'string') this.runs.add(body.run_id);
    if (typeof body.current_run_id === 'string') this.runs.add(body.current_run_id);
    if (typeof body.session_id === 'string') this.sessions.add(body.session_id);
    if (typeof body.free_query_session?.session_id === 'string') this.sessions.add(body.free_query_session.session_id);
    for (const iteration of body.iterations || []) if (typeof iteration.run_id === 'string') this.runs.add(iteration.run_id);
  }
}
