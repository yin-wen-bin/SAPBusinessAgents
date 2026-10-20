// Isolated UI acceptance. Every API request is intercepted; no model or SAP.
import assert from 'node:assert/strict';
import {readFile, mkdir, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.SAPBA_PLAYWRIGHT_MODULE || 'playwright');
const dist = path.resolve(process.env.SAPBA_BROWSER_DIST);
const destination = process.env.SAPBA_BROWSER_REPORT;
assert.ok(destination);
await mkdir(destination, {recursive: false});
const browser = await chromium.launch({headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined});
const results = [];
try {
  for (const locale of ['zh', 'en']) for (const width of [1440, 550, 390]) {
    const context = await browser.newContext({viewport: {width, height: 950}});
    const page = await context.newPage();
    const errors = [], escaped = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.synthetic = {writes: [], reads: [], ready: false, release: null};
      const run = () => ({run_id: 'synthetic-run', mode: 'free_query', status: 'planning', query: 'Offline example', input: {},
        started_at: new Date(Date.now() - 60000).toISOString(), plan: null, result: null, error: null,
        progress: {phase: 'preparing', state: 'active'}, runtime: {provider_id: 'workbuddy', version: 'synthetic'}});
      window.fetch = async (url, options = {}) => {
        const reply = data => ({ok: true, json: async () => data});
        if (options.method === 'POST') {
          window.synthetic.writes.push({url, body: JSON.parse(options.body)});
          return new Promise(resolve => {window.synthetic.release = () => resolve(reply({run_id: 'synthetic-run'}));});
        }
        window.synthetic.reads.push(url);
        return reply(url.includes('integration-actions') ? [] : run());
      };
      class SyntheticEvents {
        static CLOSED = 2;
        readyState = 1; listeners = {};
        constructor() {window.synthetic.stream = this;}
        addEventListener(name, fn) {this.listeners[name] = fn;}
        close() {this.readyState = 2;}
        emit(name, data) {this.listeners[name]?.({data: JSON.stringify({sequence: 2, data})});}
      }
      window.EventSource = SyntheticEvents;
    });
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'http://127.0.0.1:4339' || route.request().method() !== 'GET') {
        escaped.push(route.request().url()); return route.abort();
      }
      // Exercise both local '/' and Pages '/SAPBusinessAgents/' builds.
      const relative = url.pathname.replace(/^\/(?:SAPBusinessAgents\/)?/, '');
      const target = path.resolve(dist, relative.endsWith('/') ? relative + 'index.html' : relative);
      if (!target.startsWith(dist + path.sep)) return route.abort();
      try {
        const body = await readFile(target);
        const extension = path.extname(target);
        const types = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.mjs': 'text/javascript', '.woff2': 'font/woff2', '.svg': 'image/svg+xml'};
        return route.fulfill({body, contentType: types[extension] || 'application/octet-stream'});
      } catch {return route.abort();}
    });
    await page.goto(`http://127.0.0.1:4339/SAPBusinessAgents/${locale}/ask/`);
    await page.locator('[data-free-query] textarea').fill('Offline example');
    const submit = page.locator('[data-free-query] button[type=submit]');
    await submit.focus(); await page.keyboard.press('Enter');
    await page.locator('[data-submit-status]:visible').waitFor();
    assert.equal(await submit.isDisabled(), true);
    assert.equal(await page.locator('[data-free-query] form').getAttribute('aria-busy'), 'true');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false,
      `${locale}/${width}: submission feedback must not move the button outside the viewport`);
    await page.screenshot({path: path.join(destination, `${locale}-${width}-submitting.png`), fullPage: true});
    const writes = await page.evaluate(() => window.synthetic.writes.length);
    assert.equal(writes, 1);
    await page.evaluate(() => window.synthetic.release());
    await page.waitForURL(`**/${locale}/run/?run=synthetic-run`);
    await page.waitForFunction(() => Boolean(window.synthetic.stream));
    await page.evaluate(() => window.synthetic.stream.emit('agent_runtime_activity', {assistant_message_count: 0, content: 'private-content'}));
    const activity = page.locator('[data-runtime-activity]');
    await activity.waitFor();
    assert.match(await activity.textContent(), locale === 'zh' ? /等待模型/ : /Waiting for the model/);
    assert.doesNotMatch(await activity.textContent(), /private-content/);
    const before = await page.locator('[data-progress-message]').textContent();
    await page.waitForTimeout(1300);
    assert.notEqual(await page.locator('[data-progress-message]').textContent(), before);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await page.screenshot({path: path.join(destination, `${locale}-${width}-waiting.png`), fullPage: true});
    assert.deepEqual(errors, []); assert.deepEqual(escaped, []);
    results.push({locale, width, passed: true, synthetic_submissions: writes, external_requests: escaped.length});
    await context.close();
  }
  await writeFile(path.join(destination, 'report.json'), JSON.stringify({passed: true, results}, null, 2));
  process.stdout.write(JSON.stringify({passed: true, cases: results.length}) + '\n');
} finally {await browser.close();}
