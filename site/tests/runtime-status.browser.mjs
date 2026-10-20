// Synthetic responses only: no SDK, authentication, model, or SAP operation.
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
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.synthetic = {phase: 'ready', lists: 0, catalogs: 0, writes: []};
      const authentication = {status: 'authenticated', authenticated: true, capabilities: {check: true, login: true, logout: true, switch_account: true},
        blockers: [], method: 'browser', source: 'official_cli'};
      const common = {installed: true, platform_supported: true, current_version: 'synthetic', cli_version: 'synthetic',
        check_status: 'checked', blockers: [], capabilities: [], update_enabled: false, model_discovery_supported: true, authentication};
      const codex = {...common, provider_id: 'codex', name: {zh: 'Codex', en: 'Codex'}, enabled: true, selected: true, selectable: true, can_enable: true};
      window.fetch = async (url, options = {}) => {
        if (options.method && options.method !== 'GET') {window.synthetic.writes.push(url); throw new Error('Unexpected mutation');}
        if (url.includes('/models')) {
          window.synthetic.catalogs++;
          return {ok: true, json: async () => ({items: [{model_id: 'deepseek-v4.1-flash', display_name: 'deepseek-v4.1-flash',
            selected: true, selectable: true, check_status: 'compatible'}], catalog_status: 'incomplete'})};
        }
        window.synthetic.lists++;
        const pending = window.synthetic.phase !== 'ready';
        const unknown = window.synthetic.phase === 'unknown';
        const workbuddy = {...common, provider_id: 'workbuddy', sdk_id: 'codebuddy-agent-sdk', name: {zh: 'WorkBuddy', en: 'WorkBuddy'},
          enabled: unknown ? null : true, selected: false, can_enable: !pending, selectable: !pending, authenticated: true,
          default_model_id: 'deepseek-v4.1-flash', service_site: 'china', service_site_revision: 1,
          service_site_digest: 'a'.repeat(64), service_site_switch_allowed: false,
          control_status: {status: unknown ? 'unknown' : 'ready', revision: unknown ? null : 299, running: unknown},
          status_refresh: {status: pending ? 'refreshing' : 'ready', stale: pending, running: pending,
            wait_exceeded: pending, checked_at: '2026-10-16T00:00:00Z'}};
        return {ok: true, json: async () => ({items: [codex, workbuddy], default_provider_id: 'codex'})};
      };
    });
    const html = await readFile(path.join(dist, locale, 'settings/index.html'), 'utf8');
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'http://127.0.0.1:4339') return route.abort();
      if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: html});
      const file = path.resolve(dist, url.pathname.replace(/^\/SAPBusinessAgents\//, '').replace(/^\//, ''));
      if (!file.startsWith(dist + path.sep)) return route.abort();
      try {return route.fulfill({body: await readFile(file), contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'application/octet-stream'});}
      catch {return route.abort();}
    });
    await page.goto('http://127.0.0.1:4339/');
    // Navigation is view-only; the hidden Provider is initialized on first visit.
    await page.locator('[data-runtime-tab="codex"]').waitFor({state: 'attached'});
    if (width <= 820) await page.locator('[data-runtime-picker]').selectOption('workbuddy');
    else await page.locator('[data-runtime-tab="workbuddy"]').click();
    const card = page.locator('.sdk-card').filter({has: page.getByRole('heading', {name: 'WorkBuddy', exact: true})});
    await card.locator('.settings-maintenance > summary').click();
    await card.getByRole('combobox', {name: locale === 'zh' ? 'WorkBuddy · 选择模型' : 'WorkBuddy · Choose model', exact: true}).selectOption('__custom__');
    const input = card.locator('.sdk-model-section input').first();
    await input.waitFor();
    await input.focus(); await page.keyboard.press('Control+A'); await page.keyboard.type('unsaved-model');
    await page.evaluate(() => {window.preservedInput = document.activeElement; window.synthetic.phase = 'pending'; document.dispatchEvent(new Event('visibilitychange'));});
    await card.getByRole('status').filter({hasText: locale === 'zh' ? '资格核对中' : 'qualification pending'}).waitFor();
    const disable = card.getByRole('button', {name: locale === 'zh' ? '停用 Runtime' : 'Disable Runtime', exact: true});
    assert.equal(await disable.isEnabled(), true);
    assert.equal(await card.getByRole('button', {name: locale === 'zh' ? '启用 Runtime' : 'Enable Runtime', exact: true}).count(), 0);
    for (let i = 0; i < 12; i++) {
      const reads = await page.evaluate(() => {const reads = window.synthetic.lists; document.dispatchEvent(new Event('visibilitychange')); return reads;});
      await page.waitForFunction(reads => window.synthetic.lists > reads, reads);
    }
    assert.equal(await input.inputValue(), 'unsaved-model');
    assert.equal(await page.evaluate(() => window.preservedInput === document.activeElement && window.preservedInput.isConnected), true);
    assert.equal(await page.evaluate(() => window.synthetic.catalogs), 2); // one per Provider, never per status poll
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({path: path.join(destination, `${locale}-${width}.png`), fullPage: true});
    await page.evaluate(() => {window.synthetic.phase = 'unknown'; document.dispatchEvent(new Event('visibilitychange'));});
    const enable = card.getByRole('button', {name: locale === 'zh' ? '启用 Runtime' : 'Enable Runtime', exact: true});
    await enable.waitFor(); assert.equal(await enable.isDisabled(), true);
    assert.deepEqual(await page.evaluate(() => window.synthetic.writes), []);
    assert.deepEqual(errors, []);
    results.push({locale, width, enabled_while_pending: true, unknown_guarded: true,
      input_focus_preserved: true, no_repeated_catalogs: true, no_overflow: true, zero_writes: true});
    await context.close();
  }
} finally {await browser.close();}
await writeFile(path.join(destination, 'report.json'), JSON.stringify(results, null, 2));
console.log(JSON.stringify(results));
