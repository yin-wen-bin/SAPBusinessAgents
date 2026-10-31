/** Query-page ownership controls cancellation only, never SAP authorization. */
const terminal = new Set(['completed', 'inconclusive', 'failed', 'cancelled']);
const runKey = id => `sapba-query-owner:${id}`;
const sessionKey = id => `sapba-query-session-owner:${id}`;

export function rememberQueryOwner(run, session, storage) {
  try {
    storage ??= globalThis.sessionStorage;
    if (run) storage.setItem(runKey(run), '1');
    if (session) storage.setItem(sessionKey(session), '1');
  } catch { /* Ownership is a UI hint; manual cancellation remains available. */ }
}

export function ownsQuery(run, session, storage) {
  try {
    storage ??= globalThis.sessionStorage;
    // Existing tabs may still have the old ownership markers. No TTL is applied.
    return Boolean(run && (storage.getItem(runKey(run)) || storage.getItem(`sapba-query-page:${run}`))
      || session && (storage.getItem(sessionKey(session)) || storage.getItem(`sapba-query-session:${session}`)));
  } catch { return false; }
}

export function installQueryLifecycle(options, browser = globalThis) {
  const {apiBase, getRunId, getSessionId, getRun, reloadRun, feedbackId, backUrl, error, locale = 'zh'} = options;
  const window = browser.window || browser;
  const document = browser.document;
  let storage;
  try { storage = browser.sessionStorage; } catch { /* Denied storage cannot block manual actions. */ }
  let cancellation;
  let backPending;
  let backRequested;
  let feedbackStopped = false;
  let claimedSession;
  const claimedRuns = new Set();
  const owner = () => claimedRuns.has(getRunId()) || Boolean(claimedSession && claimedSession === getSessionId())
    || ownsQuery(getRunId(), getSessionId(), storage);
  const active = () => !getRun() ? Boolean(owner())
    : getRun().mode === 'free_query' && (!terminal.has(getRun().status)
      || getRun().cleanup_pending || ['stopping','cleanup_pending'].includes(getRun().cancellation?.status) || Boolean(feedbackId?.()));
  const cancel = () => {
    if (cancellation) return cancellation;
    const run = getRunId();
    if (!run || !active()) return Promise.resolve();
    const pending = (async () => {
      const feedback = feedbackId?.();
      const requests = [];
      if (feedback && getSessionId()) {
        requests.push(browser.fetch(`${apiBase}/api/free-query-sessions/${encodeURIComponent(getSessionId())}/feedback-requests/${encodeURIComponent(feedback)}/cancel`, {
          method: 'POST', headers: {'Content-Type': 'application/json'}, keepalive: true,
          body: JSON.stringify({reason: 'user_cancelled'}),
        }).then(async response => {
          if (!response.ok) throw new Error('cancellation_unconfirmed');
          const state = (await response.json()).status;
          feedbackStopped = ['cancelled','completed','failed'].includes(state);
          if (!feedbackStopped) throw new Error('cancellation_unconfirmed');
        }));
      }
      requests.push(browser.fetch(`${apiBase}/api/runs/${encodeURIComponent(run)}/cancel`, {method: 'POST', keepalive: true})
        .then(response => {if (!response.ok) throw new Error('cancellation_unconfirmed');}));
      await Promise.all(requests);
    })();
    cancellation = pending;
    // A failed/unknown HTTP response cannot poison every later manual attempt.
    // A retry still targets only the same idempotent cancellation, never /runs.
    pending.catch(() => { if (cancellation === pending) cancellation = undefined; });
    return cancellation;
  };
  const stopped = run => Boolean(run && run.run_id === getRunId() && terminal.has(run.status)
    && !run.cleanup_pending
    && !['stopping','cleanup_pending'].includes(run.cancellation?.status)
    && (!feedbackId?.() || feedbackStopped));
  const observe = run => {
    if (backRequested?.run === run?.run_id && backRequested.session === getSessionId() && stopped(run)) {
      backRequested = undefined;
      window.location.href = backUrl;
    }
  };
  const depart = () => { if (owner() && active()) void cancel().catch(() => {}); };
  const navigate = event => {
    if (event.navigationType === 'reload' || event.destination?.sameDocument || event.hashChange || event.downloadRequest != null) return;
    const destination = event.destination?.url;
    if (destination && destination !== window.location.href) depart();
  };
  Reflect.get(window, 'navigation')?.addEventListener('navigate', navigate);
  const click = event => {
    const anchor = event.target?.closest?.('a[href]');
    if (!anchor || event.defaultPrevented || event.button > 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey
        || anchor.target === '_blank' || anchor.hasAttribute('download')) return;
    const destination = new URL(anchor.href, window.location.href);
    if (destination.origin === window.location.origin && destination.pathname === window.location.pathname && destination.search === window.location.search) return;
    depart();
  };
  document.addEventListener('click', click);
  // pagehide also fires on reload. Only explicit navigation cancels work;
  // unobserved tasks remain bounded by their existing execution deadline.
  return {
    claim() {
      if (getRunId()) claimedRuns.add(getRunId());
      claimedSession = getSessionId();
      rememberQueryOwner(getRunId(), claimedSession, storage);
      cancellation = undefined;
      backRequested = undefined;
      feedbackStopped = false;
    },
    isActive: active,
    observe,
    cancel,
    back() {
      if (backPending) return backPending;
      const pending = (async () => {
      if (!active()) { window.location.href = backUrl; return; }
      backRequested = {run:getRunId(),session:getSessionId()};
      error(locale === 'zh' ? '正在停止当前查询并清理进程…' : 'Stopping this query and cleaning up its process…');
      try {
        // Inspect an already-requested stop rather than posting again.
        let run = getRun();
        if (!run?.cancel_requested) await cancel();
        run = await reloadRun();
        if (!stopped(run)) {
          error(locale === 'zh' ? '停止请求已受理，但进程清理尚未确认。不会重发查询，请保留此页核对。' : 'Stopping was accepted, but process cleanup is not confirmed. Keep this page open; the query will not be resubmitted.');
          return;
        }
        observe(run);
      } catch {
        error(locale === 'zh' ? '未能确认查询已停止，正在核对原任务；不会重复执行。' : 'Could not confirm the query stopped. Check the original task; it will not be resubmitted.');
        void reloadRun().catch(() => {});
      }
      })();
      backPending = pending;
      const release = () => { if (backPending === pending) backPending = undefined; };
      pending.then(release, release);
      return pending;
    },
  };
}
