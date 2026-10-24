import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
function load(file) {
  const filename = path.resolve(file);
  const code = ts.transpileModule(readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020, esModuleInterop: true } }).outputText;
  const mod = { exports: {} };
  const local = name => name.startsWith('.') ? load(path.resolve(path.dirname(filename), name + (name.endsWith('.ts') || name.endsWith('.tsx') ? '' : '.ts'))) : require(name);
  vm.runInNewContext(`(function(require,module,exports){${code}\n})`, { console })(local, mod, mod.exports);
  return mod.exports;
}
const { default: Progress, SampleProgressSummary } = load('src/components/AgentSampleProgress.tsx');
for (const locale of ['zh', 'en']) {
  test(`startup rejection is not SAP progress (${locale})`, () => {
    const value = { status: 'unavailable', execution_started: false, codes: ['runtime_authentication_binding_changed'],
      phase: 'runtime_admission', startup_elapsed_seconds: .2, requested_runtime: { model_id: 'glm-5.3-flash' } };
    const html = renderToStaticMarkup(React.createElement(Progress, { open: true, value, locale, canRetry: true,
      onClose() {}, onCancel() {}, onRetry() {}, onReview() {} }));
    assert.match(html, /runtime_authentication_binding_changed/);
    assert.match(html, locale === 'zh' ? /查样未启动，未调用模型或读取 SAP/ : /Discovery did not start. No model or SAP read/);
    assert.doesNotMatch(html, /正在通过SAP只读查询|Finding suitable samples through read-only SAP|历史时限未记录|Historical limit not recorded/);
    assert.match(html, /glm-5.3-flash/);
  });
  test(`system default preview is informational and has no manual selector (${locale})`, () => {
    const props = { open: true, locale, value: null, canRetry: true, fields: [{ key: 'supplier', label: 'Supplier', requirement: 'required', disabled: false, reason: '' }],
      selectedFields: ['supplier'], runtimeUsage: { status: 'ready', provider_id: 'workbuddy', model_id: 'glm-5.3-flash', service_site: 'china', binding_digest: '1'.repeat(64) },
      onClose() {}, onCancel() {}, onRetry() {}, onReview() {} };
    const unselected = renderToStaticMarkup(React.createElement(Progress, props));
    assert.doesNotMatch(unselected, /<select/);assert.match(unselected, /WorkBuddy/);assert.match(unselected, /glm-5.3-flash/);
    assert.doesNotMatch(unselected, /<button[^>]*disabled=""[^>]*>(开始查找|Start discovery)<\/button>/);
    assert.match(unselected, locale === 'zh' ? /新任务自动采用系统默认 Runtime/ : /New tasks automatically use the system default Runtime/);
    assert.match(unselected, locale === 'zh' ? /不修改草稿历史、系统默认或模型配置/ : /draft history, system defaults and model settings are unchanged/);
    const busy = renderToStaticMarkup(React.createElement(Progress, { ...props, preflightBusy: true }));
    assert.match(busy, /<button[^>]*disabled=""[^>]*>(开始查找|Start discovery)<\/button>/);
  });
  test(`accepted task shows frozen usage instead of a later default preview (${locale})`, () => {
    const value = { status: 'queued', model: 'glm-5.3-flash', runtime_binding_source: 'system_default',
      runtime_snapshot: { provider_id: 'workbuddy', model: 'glm-5.3-flash', service_site: 'china' } };
    const html = renderToStaticMarkup(React.createElement(Progress, { locale, value, runtimeUsage: { status: 'ready', provider_id: 'codex', model_id: 'LATER-DEFAULT' } }));
    assert.match(html, /WorkBuddy/);assert.match(html, /glm-5.3-flash/);
    assert.doesNotMatch(html, /LATER-DEFAULT|<select/);
    assert.match(html, locale === 'zh' ? /受理时的默认绑定，已冻结/ : /Default binding at acceptance, frozen/);
  });
  test(`uncertain startup is not claimed to have stopped (${locale})`, () => {
    const html = renderToStaticMarkup(React.createElement(SampleProgressSummary, { locale, value: { status: 'start_uncertain', codes: ['sample_start_unconfirmed'] } }));
    assert.match(html, locale === 'zh' ? /尚未确认任务是否启动/ : /Task startup is unconfirmed/);
    assert.doesNotMatch(html, /查样未启动|Discovery did not start/);
  });
}
