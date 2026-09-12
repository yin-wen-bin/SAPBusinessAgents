// Explicit browser acceptance with synthetic API responses. No SAP calls or
// production API requests. Run after build with SAPBA_PLAYWRIGHT_MODULE set.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.SAPBA_PLAYWRIGHT_MODULE || 'playwright');
const root = process.env.SAPBA_BROWSER_DIST ? path.resolve(process.env.SAPBA_BROWSER_DIST) + path.sep : fileURLToPath(new URL('../dist/', import.meta.url));
const server = createServer(async (req, res) => {
  let relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/SAPBusinessAgents\/?/, '').replace(/^\//, '');
  if (!relative || relative.endsWith('/')) relative += 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root)) { res.writeHead(403).end(); return; }
  try {
    const type = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[path.extname(file)] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': type }); res.end(await readFile(file));
  } catch { res.writeHead(404).end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
const results = [];
try {
  for (const locale of ['zh', 'en']) for (const width of [1440, 772, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 850 } });
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const draft = { draft_id: 'browser-sample', agent_id: 'browser-sample', revision: 1, status: 'draft', target_version: '0.1.0',
      technical_identity: { kind: 'new_agent', agent_id: 'browser-sample', confirmed: true, locked: false, can_rename: true },
      conversation: [], revisions: [], publishability: { can_publish: false }, package: { readme: '', manifest: {
        slug: 'browser-sample', module: 'MM', version: '0.1.0', title: { zh: '测试草稿', en: 'Test draft' },
        summary: { zh: '浏览器合成测试', en: 'Synthetic browser test' }, execution: { steps: [], inputSchema: {
          type: 'object', required: ['date'], properties: { date: { type: 'string', format: 'date', title: { zh: '计划日期', en: 'Planned date' } }, plant: { type: 'string', title: { zh: '工厂', en: 'Plant' } } }, additionalProperties: false,
        } },
      } },
    };
    let sample = null, posts = 0, cancels = 0, resolveStart;
    const gate = new Promise(resolve => { resolveStart = resolve; });
    await page.route('**/api/**', async route => {
      const req = route.request(), url = new URL(req.url());
      let value = {};
      if (req.method() === 'OPTIONS') { await route.fulfill({ status: 204, headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': '*' } }); return; }
      if (url.pathname.endsWith('/sample-discovery') && req.method() === 'POST') {
        assert.deepEqual(req.postDataJSON().selectedFields, ['date']);
        posts++; await gate;
        sample = { run_id: `browser-run-${posts}`, revision: 1, status: 'running', phase: 'reading_candidates', timeout_seconds: 600,
          started_at: new Date().toISOString(), created_at: new Date().toISOString(), model: 'gpt-5.6-sol', reasoning_effort: 'max' };
        value = sample;
      } else if (url.pathname.endsWith('/cancel')) { cancels++; sample = { ...sample, status: 'cancelled', elapsed_seconds: 2 }; value = sample; }
      else if (url.pathname.includes('/sample-discovery/')) value = sample;
      else if (url.pathname.endsWith('/browser-sample')) value = { ...draft, sample_discovery: sample,
        active_operation: sample && ['running', 'cancelling'].includes(sample.status) ? { ...sample, kind: 'sample_discovery' } : null };
      else if (url.pathname.endsWith('/catalog') || url.pathname.endsWith('/agents')) value = [];
      await route.fulfill({ json: value, headers: { 'Access-Control-Allow-Origin': '*' } });
    });
    const zh = locale === 'zh';
    await page.goto(`${origin}/SAPBusinessAgents/${locale}/agent-management/?draft=browser-sample`);
    const check = page.getByRole('checkbox', { name: zh ? '自动查找验证数据' : 'Find validation data automatically' });
    await check.check();
    const modal = page.locator('dialog[open]');
    await modal.waitFor();
    assert.ok((await modal.textContent()).includes(zh ? '选择需要查找测试数据的参数' : 'Choose parameters to find test data for'));
    assert.equal(posts, 0);
    const optionalField = modal.getByRole('checkbox', { name: zh ? '工厂' : 'Plant', exact: false });
    assert.equal(await optionalField.isChecked(), false);
    await optionalField.check(); await optionalField.uncheck();
    await modal.getByRole('button', { name: zh ? '开始查找' : 'Start discovery', exact: true }).click();
    assert.ok((await modal.textContent()).includes(zh ? '正在查找测试样本数据' : 'Finding test sample data'));
    assert.equal(await modal.getByRole('button', { name: zh ? '取消查找' : 'Cancel discovery', exact: true }).isDisabled(), true);
    assert.equal(posts, 1);
    resolveStart();
    await modal.getByText('gpt-5.6-sol', { exact: false }).waitFor();
    const box = await modal.boundingBox(); assert.ok(box.x >= 0 && box.x + box.width <= width);
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.querySelector('dialog').contains(document.activeElement)), true);
    await page.keyboard.press('Escape'); await modal.waitFor({ state: 'hidden' });
    assert.equal(cancels, 0);
    await page.reload();
    await page.getByRole('button', { name: zh ? '查看查找进度' : 'View discovery progress' }).waitFor();
    assert.equal(await modal.count(), 0);
    assert.equal(posts, 1);
    await page.getByRole('button', { name: zh ? '查看查找进度' : 'View discovery progress' }).click();
    await modal.waitFor(); assert.equal(posts, 1);
    await modal.getByRole('button', { name: zh ? '取消查找' : 'Cancel discovery', exact: true }).click();
    await modal.getByRole('button', { name: zh ? '重新查找' : 'Retry discovery', exact: true }).waitFor();
    assert.equal(cancels, 1);
    await page.waitForFunction(() => ![...document.querySelectorAll('dialog button')].find(x => /^(重新查找|Retry discovery)$/.test(x.textContent))?.disabled);
    await modal.getByRole('button', { name: zh ? '重新查找' : 'Retry discovery', exact: true }).click();
    assert.equal(posts, 1);
    await modal.getByRole('button', { name: zh ? '开始查找' : 'Start discovery', exact: true }).click();
    await modal.getByText('gpt-5.6-sol', { exact: false }).waitFor(); assert.equal(posts, 2);
    sample = { ...sample, status: 'ready', phase: 'completed', elapsed_seconds: 3, input: { date: '2026-09-01' }, suggested_inputs: { date: '2026-09-01' },
      field_sources: { date: { source_field: 'Date', evidence_ref: 'ev_browser', row_indices: [0] } } };
    await modal.getByRole('button', { name: zh ? '查看并确认参数' : 'Review and confirm inputs' }).click();
    await modal.waitFor({ state: 'hidden' });
    await page.waitForFunction(() => document.activeElement.classList.contains('draft-discovery'));
    assert.equal(await page.getByRole('checkbox', { name: zh ? '已核对回填参数，确认用于本次试运行' : 'I reviewed the proposed parameters and confirm this trial' }).isChecked(), false);
    await page.reload(); await page.getByRole('button', { name: zh ? '查看查找进度' : 'View discovery progress' }).waitFor();
    assert.equal(await modal.count(), 0);
    assert.deepEqual(errors, []);
    results.push({ locale, width, passed: true, posts, cancels });
    await context.close();
  }
  console.log(JSON.stringify({ synthetic_browser_checks: results, sap_calls: 0 }));
} finally { await browser.close(); server.close(); }
