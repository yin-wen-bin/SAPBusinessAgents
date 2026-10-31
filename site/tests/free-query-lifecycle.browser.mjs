// Rendered pages with local static files and synthetic APIs only. No live SDK/SAP.
import assert from 'node:assert/strict';
import {readFile, mkdir, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const {chromium} = createRequire(import.meta.url)(process.env.SAPBA_PLAYWRIGHT_MODULE || 'C:/Users/wenbi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const dist = path.resolve(process.env.SAPBA_BROWSER_DIST || 'dist');
const out = process.env.SAPBA_BROWSER_REPORT;
assert(out); await mkdir(out, {recursive: true});
const origin = 'http://127.0.0.1:4339';
const base = process.env.SAPBA_BROWSER_BASE ?? '/SAPBusinessAgents';
const browser = await chromium.launch({headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined});
const results = [];
try {
  for (const locale of ['zh', 'en']) for (const width of [1440, 642, 550, 390]) {
    const context = await browser.newContext({viewport: {width, height: 950}});
    const page = await context.newPage(), calls = [], errors = [], denied = [];
    let stopShouldWait = false;
    let run = {run_id: 'run-a', mode: 'free_query', status: 'queued', query: 'Synthetic offline query', input: {},
      created_at: new Date().toISOString(), started_at: null, cancel_requested: false,
      plan: null, result: null, progress: {phase: 'preparing', state: 'active', event_sequence: 1},
      page_lease: {enabled: false}, queue_position: 1, can_create_draft: false,
      runtime: {provider_id: 'workbuddy', version: 'synthetic', model: 'unchanged'}};
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => {
      if (location.pathname.includes('/run')) sessionStorage.setItem('sapba-query-page:run-a', 'a'.repeat(32));
      window.EventSource = class {
        static CLOSED = 2;
        readyState = 1;
        handlers = {};
        constructor() {window.syntheticStream = this; queueMicrotask(() => this.onopen?.());}
        addEventListener(name, handler) {this.handlers[name] = handler;}
        close() {this.readyState = 2;}
      };
    });
    await context.route('**/*', async route => {
      const req = route.request(), u = new URL(req.url());
      if (u.pathname.startsWith('/api/')) {
        const headers = {'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Methods': 'GET,POST,PUT,OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type'};
        if (req.method() === 'OPTIONS') return route.fulfill({status: 204, headers});
        calls.push({path: u.pathname, method: req.method(), body: req.postData()});
        let payload;
        if (req.method() === 'GET' && u.pathname === '/api/runs/run-a') payload = run;
        else if (req.method() === 'POST' && u.pathname === '/api/runs/run-a/cancel') {
          run = {...run, status: stopShouldWait ? 'planning' : 'cancelled', cancel_requested: true,
            cleanup_pending: stopShouldWait, cancellation: {status: stopShouldWait ? 'cleanup_pending' : 'complete'},
            progress: {...run.progress, state: stopShouldWait ? 'active' : 'cancelled', event_sequence: 2}};
          payload = {run_id: 'run-a', status: 'cancellation_requested'};
        } else {denied.push(req.method() + ' ' + u.pathname); return route.abort();}
        return route.fulfill({status: 200, headers, contentType: 'application/json', body: JSON.stringify(payload)});
      }
      if (u.origin !== origin) {denied.push(u.origin); return route.abort();}
      const clean = u.pathname.replace(/^\/SAPBusinessAgents\//, '/');
      const file = path.resolve(dist, (clean.endsWith('/') ? clean + 'index.html' : clean).replace(/^\//, ''));
      if (!file.startsWith(dist + path.sep)) return route.abort();
      try {
        const contentType = /\.(m?js)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.html') ? 'text/html' : undefined;
        return route.fulfill({status: 200, body: await readFile(file), ...(contentType ? {contentType} : {})});
      } catch {return route.fulfill({status: 404, body: 'Missing static fixture'});}
    });
    await page.goto(`${origin}${base}/${locale}/run/?run=run-a`);
    const back = page.locator('[data-stop-back]');
    await back.waitFor({state: 'visible'}); await page.waitForFunction(() => !document.querySelector('[data-stop-back]').disabled);
    await page.waitForFunction(() => document.querySelector('[data-runtime-activity]').textContent.includes(document.documentElement.lang.startsWith('zh') ? '排队中' : 'Queued'));
    assert.match(await page.locator('[data-runtime-activity]').innerText(), locale === 'zh' ? /队列位置 1/ : /Queue position 1/);
    const another = page.locator('[data-new-query]');
    assert.equal(await another.getAttribute('target'), '_blank');
    await page.evaluate(() => history.replaceState({}, '', location.href + '&view=progress'));
    assert(!calls.some(c => c.method === 'POST'));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.screenshot({path: path.join(out, `${locale}-${width}.png`), fullPage: true});
    // Reload restores the original run without heartbeats or business writes.
    await page.reload(); await back.waitFor({state: 'visible'});
    await page.waitForFunction(() => !document.querySelector('[data-stop-back]').disabled);
    assert(!calls.some(c => c.method === 'POST'));
    run = {...run, status: 'running', query: 'restored_running', queue_position: null,
      progress: {...run.progress, phase: 'reading_sap', event_sequence: 2}};
    await page.reload();
    await page.waitForFunction(() => document.querySelector('[data-session-original-preview]').textContent.includes('restored_running'));
    assert.equal(await page.locator('[data-progress-disclosure]').evaluate(node => node.open), true);
    run = {...run, status: 'completed', progress: {...run.progress, state: 'completed', event_sequence: 3},
      result: {summary: {zh: 'Saved offline result', en: 'Saved offline result'}, source_complete: true,
        presentation: {title: {zh: 'Saved result', en: 'Saved result'},
          blocks: [{type: 'text', text: {zh: 'Saved offline result', en: 'Saved offline result'}}]}}};
    await page.reload();
    await page.locator('[data-presentation-blocks]').getByText('Saved offline result', {exact: true}).waitFor();
    run = {...run, status: 'cancelled', result: null, cancel_requested: true,
      cancellation: {status: 'complete'}, progress: {...run.progress, state: 'cancelled', event_sequence: 4}};
    await page.reload();
    await page.waitForFunction(() => /取消|cancelled/i.test(document.querySelector('[data-run-status]').textContent));
    assert(!calls.some(c => c.method !== 'GET'));
    run = {...run, status: 'running', cancellation: {status: 'cleanup_pending'},
      progress: {...run.progress, state: 'active', event_sequence: 5}};
    await page.reload();
    await page.waitForFunction(() => /清理尚未确认|cleanup is not confirmed/.test(document.querySelector('[data-runtime-activity]').textContent));
    assert(!calls.some(c => c.method !== 'GET'));
    assert.equal(await page.locator('[data-cancel]').isVisible(), false);
    await back.focus(); await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelector('[data-run-alert]').textContent.includes(document.documentElement.lang.startsWith('zh') ? '清理尚未确认' : 'cleanup is not confirmed'));
    assert.match(page.url(), /\/run\//);
    assert(!calls.some(c => c.method !== 'GET'));
    // GET confirmation of the original stop, not another POST or business run.
    run = {...run, status: 'cancelled', cleanup_pending: false, cancellation: {status: 'complete'},
      progress: {...run.progress, state: 'cancelled', event_sequence: 6}};
    await back.click();
    await page.waitForURL(new RegExp(`/${locale}/ask/`));
    assert.equal(calls.filter(c => c.path.endsWith('/cancel')).length, 0);
    // HTTP acknowledgement is not cleanup confirmation. A later event reloads
    // the same run and completes the already-requested return without a retry.
    stopShouldWait = true;
    run = {...run, status: 'planning', cancel_requested: false, cleanup_pending: false, cancellation: null,
      progress: {...run.progress, state: 'active', event_sequence: 7}};
    await page.goto(`${origin}${base}/${locale}/run/?run=run-a`);
    await page.waitForFunction(() => document.querySelector('[data-run-console]').dataset.runInitialState === 'ready');
    await back.click();
    await page.waitForFunction(() => /清理尚未确认|cleanup is not confirmed/.test(document.querySelector('[data-run-alert]').textContent));
    assert.match(page.url(), /\/run\//);
    assert.equal(calls.filter(c => c.path.endsWith('/cancel')).length, 1);
    assert.match(await page.locator('[data-run-status]').innerText(), locale === 'zh' ? /清理尚未确认/ : /cleanup is not confirmed/);
    run = {...run, status: 'cancelled', cleanup_pending: false, cancellation: {status: 'complete'},
      progress: {...run.progress, state: 'cancelled', event_sequence: 8}};
    await page.evaluate(() => window.syntheticStream.handlers.run_cancelled({data: JSON.stringify({sequence: 8})}));
    await page.waitForURL(new RegExp(`/${locale}/ask/`));
    assert.equal(calls.filter(c => c.path.endsWith('/cancel')).length, 1);
    assert(!calls.some(c => c.path === '/api/runs' && c.method === 'POST'));
    assert.equal(denied.length, 0, JSON.stringify(denied));
    assert.deepEqual(errors, []);
    results.push({locale, width, queued_not_admission: true, reload_not_resubmit: true, stop_back: true,
      restored_running: true, restored_result: true, restored_cancelled: true, restored_cleanup_pending: true, no_heartbeats: true,
      late_cleanup_returns_automatically: true, acknowledgement_not_stop: true,
      requests: calls.length, model_calls: 0, sap_calls: 0});
    await context.close();
  }
  // Native browser Back, not the page's explicit button.
  const context = await browser.newContext(), page = await context.newPage();
  let cancellations = 0;
  await page.addInitScript(() => {
    if (location.pathname.endsWith('/run/')) sessionStorage.setItem('sapba-query-page:run-a', 'a'.repeat(32));
    window.EventSource = class {static CLOSED = 2; readyState = 1; addEventListener() {} close() {this.readyState = 2;}};
  });
  await context.route('**/*', async route => {
    const req = route.request(), u = new URL(req.url());
    if (u.pathname.startsWith('/api/')) {
      const headers = {'Access-Control-Allow-Origin': origin, 'Access-Control-Allow-Methods': 'GET,POST,PUT,OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type'};
      if (req.method() === 'OPTIONS') return route.fulfill({status: 204, headers});
      if (u.pathname.endsWith('/cancel')) cancellations++;
      const body = req.method() === 'GET' ? {run_id: 'run-a', mode: 'free_query', status: 'planning', query: 'offline', input: {},
        created_at: new Date().toISOString(), progress: {phase: 'preparing', state: 'active'}} : {};
      return route.fulfill({status: 200, headers, contentType: 'application/json', body: JSON.stringify(body)});
    }
    if (u.origin !== origin) return route.abort();
    const relative = u.pathname.replace(/^\/SAPBusinessAgents\//, '/').replace(/^\//, '');
    const file = path.resolve(dist, relative.endsWith('/') ? relative + 'index.html' : relative);
    if (!file.startsWith(dist + path.sep)) return route.abort();
    try {return route.fulfill({status: 200, body: await readFile(file), contentType: /\.m?js$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});}
    catch {return route.fulfill({status: 404, body: ''});}
  });
  await page.goto(`${origin}${base}/zh/ask/`);
  await page.goto(`${origin}${base}/zh/run/?run=run-a`);
  await page.waitForFunction(() => !document.querySelector('[data-stop-back]').disabled);
  assert.equal(await page.evaluate(() => 'navigation' in window), true);
  await page.goBack(); await page.waitForURL(/\/ask\//);
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(cancellations, 1);
  results.push({native_browser_back: true, cancellations});
  await context.close();
} finally {await browser.close();}
await writeFile(path.join(out, 'results.json'), JSON.stringify(results, null, 2));
console.log(JSON.stringify({passed: results.length, model_calls: 0, SAP_calls: 0}));
