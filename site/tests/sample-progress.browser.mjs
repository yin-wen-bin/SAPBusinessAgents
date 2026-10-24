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
const runtimeChoice = { status: 'ready', provider_id: 'codex', model_id: 'gpt-5.6-sol', binding_digest: '1'.repeat(64) };
try {
  for (const locale of ['zh', 'en']) for (const width of [1440, 690, 390]) for (const uncertain of [false, true]) {
    const context = await browser.newContext({ viewport: { width, height: 850 } });
    const page = await context.newPage(); const zh = locale === 'zh';
    const currentChoice = { status: 'ready', provider_id: 'workbuddy', model_id: 'glm-5.3-flash', service_site: 'china', binding_digest: '2'.repeat(64) };
    let posts = 0, saved = null;
    const draft = { draft_id: 'browser-runtime', agent_id: 'browser-runtime', revision: 5, status: 'draft', target_version: '0.1.0',
      metadata: { runtime_snapshot: { provider_id: 'workbuddy', model: 'deepseek-v4.1-flash', authentication_binding_revision: 8, environment_digest: 'old-worker' } },
      technical_identity: { kind: 'new_agent', agent_id: 'browser-runtime', confirmed: true, can_rename: true },
      conversation: [], revisions: [], publishability: { can_publish: false }, package: { readme: '', manifest: {
        slug: 'browser-runtime', module: 'MM', version: '0.1.0', title: { zh: '合成草稿', en: 'Synthetic draft' },
        execution: { steps: [], inputSchema: { type: 'object', required: ['supplier'], properties: { supplier: { type: 'string', title: { zh: '供应商', en: 'Supplier' } } } } },
      } } };
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.pathname.startsWith('/api/')) {
        if (req.method() === 'OPTIONS') { await route.fulfill({ status: 204, headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': '*' } }); return; }
        let value = {}, status = 200;
        if (url.pathname.endsWith('/sample-discovery/preflight')) {
          assert.equal(req.postDataJSON().prepareRuntime, true);
          value = { revision: 5, can_start: true, blocking_fields: [], runtime_selection: currentChoice,
            fields: [{ field: 'supplier', status: 'discoverable', reason: 'declared_odata_binding' }] };
        } else if (url.pathname.endsWith('/sample-discovery') && req.method() === 'POST') {
          posts++; assert.equal(posts, 1);
          const payload = req.postDataJSON();
          assert.equal(payload.runtimePolicy, 'system_default');
          assert.equal(payload.runtimeSelection, undefined);
          saved = { run_id: 'browser-start-rejected', revision: 5, status: 'unavailable', execution_started: false,
            phase: 'runtime_admission', timeout_seconds: 600, startup_elapsed_seconds: .2,
            requested_runtime: { provider_id: 'workbuddy', model_id: 'glm-5.3-flash' }, request_id: payload.requestId,
            runtime_binding_source: 'system_default', codes: ['runtime_authentication_binding_changed'] };
          if (uncertain) { await route.abort('failed'); return; }
          status = 409; value = { detail: { code: saved.codes[0], detail: { sample_discovery: saved } } };
        } else if (url.pathname.endsWith('/browser-runtime')) value = { ...draft, sample_discovery: saved };
        else if (url.pathname.includes('/sample-discovery/')) value = saved;
        else if (url.pathname.endsWith('/catalog') || url.pathname.endsWith('/agents')) value = [];
        await route.fulfill({ status, json: value, headers: { 'Access-Control-Allow-Origin': '*' } });
      } else if (url.origin === origin) await route.continue();
      else await route.abort();
    });
    await page.goto(`${origin}/SAPBusinessAgents/${locale}/agent-management/?draft=browser-runtime&step=trial`);
    await page.getByRole('checkbox', { name: zh ? '自动查找验证数据' : 'Find validation data automatically' }).check();
    const modal = page.locator('dialog[open]'); await modal.waitFor();
    const start = modal.getByRole('button', { name: zh ? '开始查找' : 'Start discovery', exact: true });
    assert.equal(await modal.getByRole('combobox').count(), 0);
    await page.waitForFunction(() => ![...document.querySelectorAll('dialog button')].find(x => /^(开始查找|Start discovery)$/.test(x.textContent))?.disabled);
    assert.ok((await modal.textContent()).includes(zh ? '新任务自动采用系统默认 Runtime' : 'New tasks automatically use the system default Runtime'));
    await start.click();
    if (uncertain) {
      const reconnect = modal.getByRole('button', { name: zh ? '重新连接' : 'Reconnect', exact: true });
      await reconnect.waitFor(); await reconnect.click();
    }
    await modal.getByText('runtime_authentication_binding_changed', { exact: false }).waitFor();
    const text = await modal.textContent();
    assert.ok(text.includes(zh ? '查样未启动，未调用模型或读取 SAP' : 'Discovery did not start. No model or SAP read'));
    assert.ok(!text.includes(zh ? '正在通过SAP只读查询' : 'Finding suitable samples through read-only SAP'));
    assert.equal(posts, 1);
    assert.equal(draft.metadata.runtime_snapshot.authentication_binding_revision, 8);
    const box = await modal.boundingBox(); assert.ok(box.x >= 0 && box.x + box.width <= width);
    results.push({ locale, width, automatic_default: true, uncertain, posts, passed: true });
    await context.close();
  }
  // Required inputs are not automatically discoverable. Manual scope and
  // cutoff fields must remain explicit conditions, not disabled checkboxes.
  for (const locale of ['zh', 'en']) for (const width of [1440, 690, 550, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 850 } });
    const page = await context.newPage();
    const zh = locale === 'zh';
    let starts = 0;
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const fields = [
      { field: 'company_code', status: 'manual', reason: 'organizational_scope' },
      { field: 'supplier', status: 'discoverable', reason: 'declared_odata_binding', sources: [] },
      { field: 'posting_date_to', status: 'manual', reason: 'range_boundary' },
    ];
    const draft = { draft_id: 'browser-manual', agent_id: 'browser-manual', revision: 5, status: 'draft', target_version: '0.1.0',
      technical_identity: { kind: 'new_agent', agent_id: 'browser-manual', confirmed: true, locked: false, can_rename: true },
      conversation: [], revisions: [], sample_readiness: { fields, can_start: false, blocking_fields: ['company_code', 'posting_date_to'] },
      publishability: { can_publish: false }, package: { readme: '', manifest: {
        slug: 'browser-manual', module: 'MM', version: '0.1.0', title: { zh: '合成草稿', en: 'Synthetic draft' },
        execution: { steps: [], inputSchema: { type: 'object', additionalProperties: false,
          required: ['company_code', 'supplier', 'posting_date_to'], properties: {
            company_code: { type: 'string', title: { zh: '公司代码', en: 'Company code' } },
            supplier: { type: 'string', title: { zh: '供应商', en: 'Supplier' } },
            posting_date_to: { type: 'string', format: 'date', title: { zh: '过账截止日', en: 'Posting cutoff' } },
          } } },
      } } };
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.pathname.startsWith('/api/')) {
        if (req.method() === 'OPTIONS') { await route.fulfill({ status: 204, headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': '*' } }); return; }
        let value = {};
        if (url.pathname.endsWith('/sample-discovery/preflight')) value = { ...draft.sample_readiness, runtime_selection: runtimeChoice };
        else if (url.pathname.endsWith('/sample-discovery') && req.method() === 'POST') { starts++; throw new Error('manual_boundary_must_not_dispatch'); }
        else if (url.pathname.endsWith('/browser-manual')) value = draft;
        else if (url.pathname.endsWith('/catalog') || url.pathname.endsWith('/agents')) value = [];
        await route.fulfill({ json: value, headers: { 'Access-Control-Allow-Origin': '*' } });
      } else if (url.origin === origin) await route.continue();
      else await route.abort();
    });
    await page.goto(`${origin}/SAPBusinessAgents/${locale}/agent-management/?draft=browser-manual&step=trial`);
    await page.getByRole('checkbox', { name: zh ? '自动查找验证数据' : 'Find validation data automatically' }).check();
    const modal = page.locator('dialog[open]'); await modal.waitFor();
    assert.equal(await modal.getByRole('checkbox').count(), 1);
    assert.equal(await modal.getByRole('checkbox', { name: zh ? '供应商' : 'Supplier', exact: false }).isEnabled(), true);
    assert.equal(await modal.getByRole('button', { name: zh ? '开始查找' : 'Start discovery', exact: true }).isDisabled(), true);
    assert.ok((await modal.textContent()).includes(zh ? '范围边界需手工指定' : 'Enter the query boundary manually'));
    const manual = modal.locator('.sample-manual-conditions li').filter({ hasText: zh ? '公司代码' : 'Company code' });
    await manual.getByRole('button').click();
    await modal.waitFor({ state: 'hidden' });
    await page.waitForFunction(() => document.activeElement?.id === 'draft-input-company_code');
    assert.equal(starts, 0); assert.deepEqual(errors, []);
    results.push({ locale, width, manual_boundary: true, passed: true, starts });
    await context.close();
  }
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
      if (url.pathname.endsWith('/sample-discovery/preflight')) {
        value = { revision: 1, can_start: true, blocking_fields: [], runtime_selection: runtimeChoice, fields: [
          { field: 'date', status: 'discoverable', reason: 'declared_odata_binding', sources: [] },
          { field: 'plant', status: 'discoverable', reason: 'declared_odata_binding', sources: [] },
        ] };
      } else if (url.pathname.endsWith('/sample-discovery') && req.method() === 'POST') {
        assert.deepEqual(req.postDataJSON().selectedFields, ['date']);
        assert.equal(req.postDataJSON().runtimePolicy, 'system_default');
        assert.equal(req.postDataJSON().runtimeSelection, undefined);
        posts++; await gate;
        sample = { run_id: `browser-run-${posts}`, revision: 1, status: 'running', phase: 'reading_candidates', timeout_seconds: 600,
          started_at: new Date().toISOString(), created_at: new Date().toISOString(), model: 'gpt-5.6-sol', reasoning_effort: 'max',
          runtime_binding_source: 'system_default', runtime_snapshot: { provider_id: 'codex', model: 'gpt-5.6-sol' } };
        value = sample;
      } else if (url.pathname.endsWith('/cancel')) { cancels++; sample = { ...sample, status: 'cancelled', elapsed_seconds: 2 }; value = sample; }
      else if (url.pathname.includes('/sample-discovery/')) value = sample;
      else if (url.pathname.endsWith('/browser-sample')) value = { ...draft, sample_discovery: sample,
        active_operation: sample && ['running', 'cancelling'].includes(sample.status) ? { ...sample, kind: 'sample_discovery' } : null };
      else if (url.pathname.endsWith('/catalog') || url.pathname.endsWith('/agents')) value = [];
      await route.fulfill({ json: value, headers: { 'Access-Control-Allow-Origin': '*' } });
    });
    const zh = locale === 'zh';
    await page.goto(`${origin}/SAPBusinessAgents/${locale}/agent-management/?draft=browser-sample&step=trial`);
    const check = page.getByRole('checkbox', { name: zh ? '自动查找验证数据' : 'Find validation data automatically' });
    await check.check();
    const modal = page.locator('dialog[open]');
    await modal.waitFor();
    assert.ok((await modal.textContent()).includes(zh ? '选择需要查找测试数据的参数' : 'Choose parameters to find test data for'));
    assert.equal(posts, 0);
    assert.equal(await modal.getByRole('combobox').count(), 0);
    await page.waitForFunction(() => ![...document.querySelectorAll('dialog button')].find(x => /^(开始查找|Start discovery)$/.test(x.textContent))?.disabled);
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
    assert.equal(await modal.getByRole('combobox').count(), 0);
    await page.waitForFunction(() => ![...document.querySelectorAll('dialog button')].find(x => /^(开始查找|Start discovery)$/.test(x.textContent))?.disabled);
    await modal.getByRole('button', { name: zh ? '开始查找' : 'Start discovery', exact: true }).click();
    await modal.getByText('gpt-5.6-sol', { exact: false }).waitFor(); assert.equal(posts, 2);
    sample = { ...sample, status: 'ready', phase: 'completed', elapsed_seconds: 3, input: { date: '2026-09-01' }, suggested_inputs: { date: '2026-09-01' },
      field_sources: { date: { source_field: 'Date', evidence_ref: 'ev_browser', row_indices: [0] } } };
    await modal.getByRole('button', { name: zh ? '查看并确认参数' : 'Review and confirm inputs' }).click();
    await modal.waitFor({ state: 'hidden' });
    await page.waitForFunction(() => document.activeElement.classList.contains('draft-discovery'));
    assert.equal(await page.getByRole('checkbox', { name: zh ? '已核对回填参数，确认用于本次操作' : 'I reviewed the proposed parameters and confirm this use' }).isChecked(), false);
    await page.reload(); await page.getByRole('button', { name: zh ? '查看查找进度' : 'View discovery progress' }).waitFor();
    assert.equal(await modal.count(), 0);
    assert.deepEqual(errors, []);
    results.push({ locale, width, passed: true, posts, cancels });
    await context.close();
  }
  console.log(JSON.stringify({ synthetic_browser_checks: results, sap_calls: 0 }));
} finally { await browser.close(); server.close(); }
