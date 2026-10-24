import test from 'node:test';
import assert from 'node:assert/strict';
import {installQueryLifecycle, rememberQueryOwner, ownsQuery} from '../src/lib/free-query-lifecycle.mjs';

const tick = () => new Promise(resolve => setImmediate(resolve));
function harness({owned = true, status = 'running', pending = false, feedback = false, storage: savedStorage} = {}) {
  const storage = savedStorage || new Map(), timers = new Map(), calls = [], messages = [];
  const events = {}, docEvents = {}, navEvents = {};
  const sessionStorage = {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value)};
  if (owned) rememberQueryOwner('run-a', 'session-a', sessionStorage);
  let next = 0, requestActive = 0, peak = 0, runId = 'run-a', current = {run_id: 'run-a', mode: 'free_query', status};
  let respond = async () => ({ok: true, status: 200});
  const window = {location: {href: 'http://local/run/?run=run-a'},
    setInterval(fn, ms) {timers.set(++next, {fn, ms}); return next;}, clearInterval(id) {timers.delete(id);},
    addEventListener(name, fn) {events[name] = fn;},
    navigation: {addEventListener(name, fn) {navEvents[name] = fn;}},
  };
  const document = {hidden: false, addEventListener(name, fn) {docEvents[name] = fn;}};
  const browser = {window, document, sessionStorage, fetch: async (url, body) => {
    calls.push({url, ...body});
    requestActive++; peak = Math.max(peak, requestActive);
    try {return await respond(url, body);} finally {requestActive--;}
  }};
  const ui = installQueryLifecycle({apiBase: 'http://api', getRunId: () => runId, getSessionId: () => 'session-a',
    getRun: () => current, feedbackId: () => feedback ? 'review-a' : null, reloadRun: async () => {
      current = {...current, status: pending ? 'running' : 'cancelled', cancel_requested: true,
        cancellation: {status: pending ? 'cleanup_pending' : 'complete'}};
      return current;
    }, error: value => messages.push(value), backUrl: '/ask/'}, browser);
  return {ui, window, document, events, docEvents, navEvents, calls, messages, timers, storage,
    setRun: value => {current = value;},
    setRunId: value => {runId = value;},
    respond: fn => {respond = fn;}, peak: () => peak,
    tickTimers() {for (const timer of timers.values()) timer.fn();},
  };
}

test('owned page sends no heartbeat or business requests even after the former expiry', async () => {
  const h = harness(); await tick();
  for (let n = 0; n < 10; n++) h.tickTimers();
  await tick();
  assert.equal(h.timers.size, 0);
  assert.equal(h.calls.length, 0);
});

test('switching tabs and closing the page send no cancellation or lease requests', async () => {
  const h = harness(); await tick();
  h.document.hidden = true;
  h.tickTimers(); await tick();
  assert(!h.calls.some(c => c.url.endsWith('/cancel')));
  h.events.pagehide?.(); await tick();
  assert.equal(h.timers.size, 0);
  assert.equal(h.calls.length, 0);
});

test('browser Back cancels exactly the owned run; reload and hash navigation do not', async () => {
  const h = harness(); await tick();
  h.navEvents.navigate({navigationType: 'reload', destination: {url: h.window.location.href}});
  h.navEvents.navigate({navigationType: 'replace', destination: {url: h.window.location.href + '&session=same', sameDocument: true}});
  h.navEvents.navigate({navigationType: 'push', hashChange: true});
  assert(!h.calls.some(c => c.url.endsWith('/cancel')));
  h.navEvents.navigate({navigationType: 'traverse', destination: {url: 'http://local/ask/'}});
  await tick();
  assert.equal(h.calls.filter(c => c.url.endsWith('/run-a/cancel')).length, 1);
  h.events.pagehide?.(); await tick();
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
});

test('same-tab navigation cancels but opening another query tab keeps current query', async () => {
  const h = harness(); await tick();
  const anchor = {href: 'http://local/ask/', target: '_blank', hasAttribute: () => false};
  h.docEvents.click({target: {closest: () => anchor}, button: 0});
  assert(!h.calls.some(c => c.method === 'POST'));
  anchor.target = '';
  h.docEvents.click({target: {closest: () => anchor}, button: 0});
  await tick();
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
});

test('history view has no automatic ownership or cancellation', async () => {
  const h = harness({owned: false});
  h.events.pagehide?.(); h.navEvents.navigate({navigationType: 'traverse', destination: {url: '/ask/'}});
  await tick();
  assert.equal(h.calls.length, 0);
  assert.equal(h.timers.size, 0);
});

test('terminal back only navigates, active back confirms cleanup, repeated clicks cancel once', async () => {
  const h = harness(); await tick();
  await Promise.all([h.ui.back(), h.ui.back()]);
  assert.equal(h.calls.filter(c => c.url.endsWith('/cancel')).length, 1);
  assert.equal(h.window.location.href, '/ask/');
  const terminal = harness({status: 'completed'});
  await terminal.ui.back();
  assert.equal(terminal.calls.length, 0);
  assert.equal(terminal.window.location.href, '/ask/');
});

test('cleanup pending never reports stopped or leaves using a false success', async () => {
  const h = harness({pending: true}); await tick();
  await h.ui.back();
  assert.equal(h.window.location.href, 'http://local/run/?run=run-a');
  assert.match(h.messages.at(-1), /清理尚未确认/);
});

test('reload preserves ownership without submitting, renewing or cancelling', async () => {
  const original = harness();
  const restored = harness({owned: false, storage: original.storage});
  assert.equal(restored.calls.length, 0);
  restored.navEvents.navigate({navigationType: 'traverse', destination: {url: 'https://outside.invalid/'}});
  await tick();
  assert.equal(restored.calls.length, 1);
  assert.equal(restored.calls[0].url, 'http://api/api/runs/run-a/cancel');
  assert.equal(restored.calls[0].keepalive, true);
});

test('old tab markers remain usable without a token or expiry', async () => {
  const storage = new Map([['sapba-query-page:run-a', 'old-token']]);
  assert(ownsQuery('run-a', null, {getItem: key => storage.get(key)}));
  const h = harness({owned: false, storage});
  h.navEvents.navigate({navigationType: 'traverse', destination: {url: '/ask/'}});
  await tick();
  assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
});

test('explicit feedback claims a historical view and follows the session iteration', async () => {
  const h = harness({owned: false, feedback: true});
  h.ui.claim();
  h.setRunId('run-b');
  h.navEvents.navigate({navigationType: 'traverse', destination: {url: '/ask/'}});
  await tick();
  assert.equal(h.calls.filter(c => c.url.includes('feedback-requests')).length, 1);
  assert.equal(h.calls.filter(c => c.url.endsWith('/run-b/cancel')).length, 1);
});

test('repeated navigation and link events cancel only once even while the request is pending', async () => {
  const h = harness();
  let finish;
  h.respond(() => new Promise(resolve => {finish = resolve;}));
  h.navEvents.navigate({navigationType: 'traverse', destination: {url: '/ask/'}});
  h.navEvents.navigate({navigationType: 'push', destination: {url: 'https://outside.invalid/'}});
  assert.equal(h.calls.length, 1);
  finish({ok: true, status: 202}); await tick();
});

test('a cancelled or completed page can reopen without restarting or cancelling work', async () => {
  for (const status of ['cancelled', 'completed', 'inconclusive', 'failed']) {
    const h = harness({status});
    h.navEvents.navigate({navigationType: 'traverse', destination: {url: '/ask/'}});
    await h.ui.back();
    assert.equal(h.calls.length, 0);
    assert.equal(h.window.location.href, '/ask/');
  }
});

test('feedback cleanup failure cannot prevent cancellation of the current run or claim success', async () => {
  const h = harness({feedback: true}); await tick();
  h.respond(async url => ({ok: true, status: 200, json: async () => ({status: url.includes('feedback-requests') ? 'cleanup_pending' : 'cancellation_requested'})}));
  await h.ui.back();
  assert.equal(h.calls.filter(c => c.url.endsWith('/run-a/cancel')).length, 1);
  assert.equal(h.calls.filter(c => c.url.includes('feedback-requests')).length, 1);
  assert.equal(h.window.location.href, 'http://local/run/?run=run-a');
  assert.match(h.messages.at(-1), /未能确认/);
});

test('native Back before the initial status arrives still cancels an owned query', async () => {
  const h = harness(); await tick(); h.setRun(undefined);
  h.navEvents.navigate({navigationType: 'traverse', destination: {url: '/ask/', sameDocument: false}});
  await tick();
  assert.equal(h.calls.filter(c => c.url.endsWith('/run-a/cancel')).length, 1);
});
