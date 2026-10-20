// Component-only interaction/layout checks. Every API response is synthetic.
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import ts from "typescript";
import { build } from "vite";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.SAPBA_PLAYWRIGHT_MODULE || "playwright");
const destination = process.env.SAPBA_BROWSER_REPORT;
assert.ok(destination, "Specify a fresh isolated report directory");
await mkdir(destination, { recursive: false });
const entry = `import React from 'react'; import {createRoot} from 'react-dom/client';
import Assistant from '${path.resolve('src/components/WorkflowAssistant.tsx').replaceAll('\\', '/')}';
import Diagnostic from '${path.resolve('src/components/RoleRuntimeDiagnostic.tsx').replaceAll('\\', '/')}';
let props={apiBase:'',locale:'zh',draftId:'test',revision:1,step:'review',fieldPath:'/title',fieldLabel:'名称',published:false,dirty:false,busy:false,prepare:async()=>({draft_id:'test',revision:props.revision}),refresh:()=>{}};
const root=createRoot(document.getElementById('root'));
window.updateProps=p=>{props={...props,...p};root.render(React.createElement(Assistant,props));};
window.showDiagnostic=locale=>root.render(React.createElement(Diagnostic,{locale,code:'workbuddy_deadline_exceeded',detail:{phase:'role_understanding',budget_seconds:3600,elapsed_ms:3600120,message_count:4,terminal_result_received:false,raw_log:'PRIVATE_DO_NOT_RENDER'}}));
window.updateProps({});`;
const output = await build({ configFile: false, root: process.cwd(), logLevel: "error",
  plugins: [{ name: "synthetic-entry", resolveId: id => id === "virtual:scope-test" ? "\0scope-test" : null,
    load: id => id === "\0scope-test" ? entry : null,
    transform(code, id) { if (id.endsWith('.tsx')) return ts.transpileModule(code, { compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022,
    } }).outputText; } }],
  build: { write: false, minify: false, rollupOptions: { input: "virtual:scope-test" } } });
const script = output.output.filter(item => item.type === 'chunk').map(item => item.code).join('\n');
const styles = await readFile('src/styles/global.css', 'utf8');
const browser = await chromium.launch({ headless: true, channel: process.platform === 'win32' ? 'msedge' : undefined });
const records = [];
try {
  for (const locale of ['zh', 'en']) for (const width of [1440, 550, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 800 } });
    const page = await context.newPage();
    await page.route('**/*', route => route.request().url() === 'http://127.0.0.1:4338/'
      ? route.fulfill({ contentType: 'text/html', body: `<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>${styles}</style></head><body><div id="root"></div></body></html>` }) : route.abort());
    await page.addInitScript(() => {
      window.posts = []; window.failNext = false;
      window.EventSource = class { addEventListener() {} close() {} };
      window.fetch = async (url, options) => {
        if (options?.method === 'POST') {
          window.posts.push(JSON.parse(options.body));
          if (window.failNext) { window.failNext = false; throw new Error('synthetic_transport_failure'); }
          return { ok: true, json: async () => ({ draft_id: 'test' }) };
        }
        return { ok: true, json: async () => ({ rounds: [], events: [] }) };
      };
    });
    await page.goto('http://127.0.0.1:4338/');
    await page.addScriptTag({ type: 'module', content: script });
    await page.evaluate(locale => window.updateProps({ locale, fieldLabel: locale === 'zh' ? '名称' : 'Name' }), locale);
    const toggle = page.getByRole('button', { name: locale === 'zh' ? 'AI 助手' : 'AI assistant', exact: true });
    if (width < 1200) await toggle.click();
    const checkbox = page.getByRole('checkbox');
    await checkbox.waitFor();
    assert.equal(await checkbox.isChecked(), false);
    await page.locator('textarea').fill('Rename title');
    await checkbox.focus(); await page.keyboard.press('Space');
    assert.equal(await checkbox.isChecked(), true);
    await page.getByRole('button', { name: locale === 'zh' ? '修改工作流' : 'Revise workflow', exact: true }).click();
    await page.waitForFunction(() => window.posts.length === 1);
    const first = await page.evaluate(() => window.posts[0]);
    assert.deepEqual(first.allowedChangePaths, ['/title']); assert.equal(first.intent, 'revise');
    await page.locator('textarea').fill('Explain current title');
    await page.getByRole('button', { name: locale === 'zh' ? '解释问题' : 'Explain', exact: true }).click();
    await page.waitForFunction(() => window.posts.length === 2);
    assert.equal(await page.evaluate(() => window.posts[1].allowedChangePaths), undefined);
    await page.evaluate(() => window.updateProps({ revision: 2 }));
    await page.waitForFunction(() => !document.querySelector('input[type=checkbox]').checked);
    await checkbox.check(); await page.locator('textarea').fill('Scoped retry');
    await page.evaluate(() => { window.failNext = true; });
    const revise = page.getByRole('button', { name: locale === 'zh' ? '修改工作流' : 'Revise workflow', exact: true });
    await revise.click(); await page.getByRole('alert').waitFor();
    assert.equal(await checkbox.isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: locale === 'zh' ? '解释问题' : 'Explain', exact: true }).isDisabled(), true);
    await revise.click();
    await page.waitForFunction(() => window.posts.length === 4);
    const retry = await page.evaluate(() => window.posts.slice(2));
    assert.deepEqual(retry[0], retry[1]);
    await checkbox.check(); await page.locator('textarea').fill('Recover scoped retry');
    await page.evaluate(() => { window.failNext = true; }); await revise.click();
    await page.getByRole('alert').waitFor();
    const beforeReload = await page.evaluate(() => window.posts.at(-1));
    await page.reload(); await page.addScriptTag({ type: 'module', content: script });
    await page.evaluate(locale => window.updateProps({ locale, revision: 2 }), locale);
    if (width < 1200) await page.getByRole('button', { name: locale === 'zh' ? 'AI 助手' : 'AI assistant', exact: true }).click();
    await page.getByText(locale === 'zh' ? '待重传请求保留原修改范围：' : 'Pending retry retains its original scope: ', { exact: false }).waitFor();
    await page.getByRole('button', { name: locale === 'zh' ? '修改工作流' : 'Revise workflow', exact: true }).click();
    await page.waitForFunction(() => window.posts.length === 1);
    assert.deepEqual(await page.evaluate(() => window.posts[0]), beforeReload);
    await page.evaluate(() => window.updateProps({ dirty: true }));
    await page.waitForFunction(() => document.querySelector('input[type=checkbox]').disabled);
    assert.equal(await checkbox.isChecked(), false);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(destination, `${locale}-${width}.png`), fullPage: true });
    await page.evaluate(locale => window.showDiagnostic(locale), locale);
    const summary = page.locator('details summary');
    await summary.waitFor(); await summary.focus(); await page.keyboard.press('Enter');
    assert.equal(await page.locator('details').evaluate(node => node.open), true);
    assert.equal(await page.getByRole('alert').textContent().then(text => text.includes('3600 s')), true);
    assert.equal(await page.getByRole('alert').textContent().then(text => text.includes('PRIVATE_DO_NOT_RENDER')), false);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(destination, `role-${locale}-${width}.png`), fullPage: true });
    records.push({ locale, width, scoped_revise: true, explain_unscoped: true, keyboard: true, uncertain_retry_identical: true, reload_retry_identical: true, dirty_reset: true, role_diagnostic_keyboard: true, no_overflow: true, synthetic_only: true });
    await context.close();
  }
} finally { await browser.close(); }
await writeFile(path.join(destination, 'report.json'), JSON.stringify(records, null, 2));
console.log(JSON.stringify(records));
