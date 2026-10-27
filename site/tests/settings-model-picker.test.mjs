import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createContext, runInContext } from "node:vm";
import { randomUUID } from "node:crypto";

const source = await readFile(new URL("../src/components/SystemSettingsPanel.astro", import.meta.url), "utf8");
const copyDefinition = source.slice(source.indexOf("const copy ="), source.indexOf("\n---", source.indexOf("const copy =")));
const script = source.match(/<script is:inline[^>]*>([\s\S]*?)<\/script>/)[1];

// Exercise the actual inline settings script without a browser, network or SDK.
class Node {
  children = []; attributes = {}; listeners = {}; dataset = {}; disabled = false; hidden = false; value = ""; ownText = "";
  constructor(tag, className = "") { this.tagName = tag; this.className = className; }
  set textContent(text) { this.ownText = String(text); this.children = []; }
  get textContent() { return this.ownText + this.children.map((child) => child.textContent).join(""); }
  append(...children) {
    for (const child of children) {
      if (child.parent) child.parent.children = child.parent.children.filter((item) => item !== child);
      child.parent = this; this.children.push(child);
      if (this.tagName === "select" && this.children.length === 1) this.value = child.value;
    }
  }
  replaceChildren(...children) { for (const child of this.children) child.parent = null; this.children = []; this.append(...children); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(name, listener) { (this.listeners[name] ||= []).push(listener); }
  async fire(name) {
    if (name === "click" && this.disabled) return;
    for (const listener of this.listeners[name] || []) await listener({ currentTarget: this, target: this });
  }
  focus() { this.focused = true; }
  matches(selector) {
    if (selector.startsWith(".")) return this.className.split(" ").includes(selector.slice(1));
    if (selector.startsWith("[")) return Object.hasOwn(this.attributes, selector.slice(1, -1));
    return this.tagName === selector;
  }
  querySelectorAll(selector) {
    return this.children.flatMap((child) => [
      ...(selector.split(",").some((item) => child.matches(item.trim())) ? [child] : []),
      ...child.querySelectorAll(selector),
    ]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector) || null; }
}

const settle = () => new Promise((resolve) => setImmediate(resolve));
async function harness(locale = "zh") {
  const root = new Node("section"); root.dataset.apiBase = "http://test";
  for (const attribute of ["data-sdk-grid", "data-sdk-alert", "data-no-default"]) {
    const child = new Node("div"); child.setAttribute(attribute, ""); root.append(child);
  }
  const calls = []; let response = async () => ({ items: [], default_provider_id: "codex" });
  const stored = new Map(); const timers = new Map(); let timerId = 0; let confirmed = false;
  const documentEvents = {}; const windowEvents = {};
  const document = {hidden: false, querySelector: () => root, createElement: (tag) => new Node(tag), addEventListener: (name, fn) => {documentEvents[name] = fn;}};
  const context = createContext({ locale, document,
    crypto: { randomUUID }, AbortController,
    setTimeout: (fn, ms) => { timers.set(++timerId, {fn, ms}); return timerId; }, clearTimeout: (id) => timers.delete(id),
    sessionStorage: {getItem: (key) => stored.get(key), setItem: (key, value) => stored.set(key, value), removeItem: (key) => stored.delete(key)},
    fetch: async (url, options = {}) => { calls.push({ url, ...options }); return { ok: true, json: () => response(url, options) }; },
    window: { confirm: () => confirmed, prompt: () => null, addEventListener: (name, fn) => {windowEvents[name] = fn;} },
  });
  runInContext(`${copyDefinition}\n${script}\nglobalThis.settingsUI = { renderModels, renderAuthentication, loadModels, render, load, applyRuntimeResult, maybeLoadAuthentication, refreshViewForms, runtimeViews, maintenanceSDK };`, context);
  await settle(); calls.length = 0;
  return { root, calls, stored, timeouts: () => [...timers.values()].map((item) => item.ms), confirm: (value) => { confirmed = value; }, respond: (fn) => { response = fn; },
    renderRuntimes: (items) => context.settingsUI.render(items, "codex"),
    maintenance: provider => context.settingsUI.runtimeViews.get(provider).versionSection.controls,
    maintenanceValue: sdk => context.settingsUI.maintenanceSDK(sdk),
    loadRuntimes: (options = {incremental: true}) => context.settingsUI.load(false, options),
    applyRuntimeResult: (payload) => context.settingsUI.applyRuntimeResult(payload),
    updateAuthentication: (sdk, view) => context.settingsUI.maybeLoadAuthentication(sdk, view),
    refreshForms: provider => context.settingsUI.refreshViewForms(context.settingsUI.runtimeViews.get(provider)),
    visible: async (visible) => {document.hidden = !visible; documentEvents.visibilitychange?.(); await settle();},
    hidePage: () => windowEvents.pagehide?.(),
    loadModels: (sdk, section) => context.settingsUI.loadModels(sdk, section),
    renderAuthentication: (sdk, payload) => { const section = new Node("section"); context.settingsUI.renderAuthentication(sdk, section, payload); return section; },
    poll: async () => { const scheduled = [...timers.entries()].find(([, v]) => [2000, 5000, 15000].includes(v.ms)); if (scheduled) { timers.delete(scheduled[0]); await scheduled[1].fn(); } },
    render: (sdk, payload) => {
    const section = new Node("section"); context.settingsUI.renderModels(sdk, section, payload); return section;
  } };
}
const sdk = { provider_id: "codex", name: { zh: "Codex", en: "Codex" }, default_model_id: "model-a" };
const model = (id, selected = false) => ({ model_id: id, display_name: id, selected, check_status: "compatible", selectable: true,
  supported_reasoning_efforts: ["low", "high"], reasoning_effort: "high", saved_reasoning_effort: "high", default_reasoning_effort: "low" });
const catalog = { catalog_status: "ready", model_catalog_complete: true, items: [model("model-a", true), model("model-b")] };

test('maintenance checks finish, keep controls stable, and read versions without authentication requests', async () => {
  const h = await harness();
  const value = {...sdk, sdk_id:'codex-python-sdk', enabled:true, update_enabled:true, current_version:'1.0.0', latest_version:null};
  h.renderRuntimes([value]);
  const controls=h.maintenance('codex');
  h.respond(async(url) => url.endsWith('/check?scope=versions') ? {item:{current_version:'1.0.0',latest_version:'1.1.0',update_enabled:true,update_available:true,version_check:{status:'checked',checked_at:'2026-10-23'}}} : {items:[value],default_provider_id:'codex'});
  await controls.versions.fire('click');
  assert.equal(controls.versions.disabled,false);
  assert.equal(controls.update.disabled,false);
  assert.equal(h.maintenance('codex'),controls);
  assert.equal(h.calls.filter(c=>c.method==='POST').length,1);
  assert.ok(h.calls[0].url.endsWith('/check?scope=versions'));
  h.respond(async(url) => url.endsWith('/codex/check') ? {item:value} : {items:[value],default_provider_id:'codex'});
  await controls.check.fire('click');
  assert.equal(controls.check.textContent,'检查安装与登录');
  assert.equal(controls.check.disabled,false);
});

test('WorkBuddy version checks remain independent when online activation is unavailable', async () => {
  const h=await harness();
  const value={...sdk,provider_id:'workbuddy',sdk_id:'codebuddy-agent-sdk',can_update:true,update_enabled:false,
    update_supported:false,version_check_supported:true,current_version:'1.0.0'};
  h.renderRuntimes([value]);const controls=h.maintenance('workbuddy');
  assert.equal(controls.versions.disabled,false);assert.equal(controls.update.hidden,true);
  assert.match(controls.note.textContent,/独立检查可用版本/);
  assert.match(h.root.textContent,/旧版固定版本.*环境及认证绑定/);
  h.respond(async(url,options)=>options.method==='POST'?{item:{...value,latest_version:'1.1.0',version_check:{status:'checked',checked_at:'2026-10-27'}}}
    :{items:[value],default_provider_id:'workbuddy'});
  await controls.versions.fire('click');await controls.update.fire('click');
  const posts=h.calls.filter(c=>c.method==='POST');assert.equal(posts.length,1);
  assert.ok(posts[0].url.endsWith('/check?scope=versions'));
  assert.equal(h.calls.some(c=>/authentication|models\/check|\/update\?/.test(c.url)),false);
});

for (const locale of ['zh','en']) test(`${locale}: WorkBuddy accepted update stays running until activation is confirmed`, async () => {
  const h=await harness(locale);h.confirm(true);
  const value={...sdk,provider_id:'workbuddy',sdk_id:'codebuddy-agent-sdk',can_update:true,update_enabled:true,
    update_supported:true,version_check_supported:true,update_preparation_supported:true,update_available:true,
    environment_digest:'old',current_version:'1.0.0',latest_version:'1.1.0'};
  h.renderRuntimes([value]);const controls=h.maintenance('workbuddy');
  assert.equal(controls.versions.disabled,false);assert.equal(controls.update.hidden,false);
  assert.equal(controls.update.textContent,locale==='zh'?'更新 SDK':'Update SDK');
  const stamp=new Date().toISOString();
  const status={operation_id:'online-update-test',status:'running',phase:'downloading',target_version:'1.1.0',started_at:stamp,updated_at:stamp};
  h.respond(async(url,options)=>options.method==='POST'?{operation_id:status.operation_id,item:{...value,maintenance_kind:'online_upgrade',activated:false,update_status:status}}
    :{items:[{...value,update_status:status}],default_provider_id:'workbuddy'});
  await controls.update.fire('click');
  assert.match(h.maintenance('workbuddy').status.textContent,locale==='zh'?/正在下载/:/Downloading/);
  assert.equal(h.maintenanceValue(value).current_version,'1.0.0');
  assert.equal(h.maintenance('workbuddy').update.disabled,true);
  await h.maintenance('workbuddy').update.fire('click');
  assert.equal(h.calls.filter(c=>c.method==='POST').length,1);
  assert.equal(h.calls.filter(c=>c.method==='POST')[0].url,'http://test/api/system/sdks/codebuddy-agent-sdk/update?expectedTargetVersion=1.1.0');
  const current={...value,environment_digest:'new',current_version:'1.1.0',cli_version:'2.1.0',update_available:false,
    update_status:{...status,status:'completed',phase:'completed',outcome:'activated',updated_at:new Date(Date.now()+1000).toISOString()}};
  h.renderRuntimes([current]);
  assert.equal(h.maintenanceValue(current).current_version,'1.1.0');
  assert.doesNotMatch(h.maintenance('workbuddy').status.textContent,/正在下载|Downloading/);
  assert.equal(h.calls.filter(c=>c.method==='POST').length,1);
  assert.equal(h.calls.some(c=>/authentication|models\/check/.test(c.url)),false);
});

test('unknown update freezes installation across rerender and subsequent actions only read status', async () => {
  const h=await harness();h.confirm(true);
  const value={...sdk,sdk_id:'codex-python-sdk',enabled:true,update_enabled:true,update_available:true,latest_version:'1.1.0',current_version:'1.0.0'};
  h.renderRuntimes([value]);
  h.respond(async(url,options)=>{if(options.method==='POST')throw new Error('response lost');return {items:[value],default_provider_id:'codex'};});
  await h.maintenance('codex').update.fire('click');
  h.renderRuntimes([value]);
  const controls=h.maintenance('codex');
  assert.equal(controls.update.disabled,true);assert.equal(controls.reread.hidden,false);
  assert.match(controls.status.textContent,/结果待核对/);
  assert.match([...h.stored.values()].join(''),/unknown/);
  await controls.update.fire('click');await controls.reread.fire('click');await settle();
  assert.equal(h.calls.filter(c=>c.method==='POST').length,1);
});

test('WorkBuddy old version-read cache cannot overwrite a controlled environment change', async () => {
  const h=await harness();
  const old={...sdk,provider_id:'workbuddy',sdk_id:'codebuddy-agent-sdk',environment_digest:'old',current_version:'1.0.0',
    update_enabled:true,update_preparation_supported:true};
  h.renderRuntimes([old]);
  h.respond(async url=>url.includes('scope=versions')?{item:{...old,latest_version:'1.1.0',version_check:{checked_at:'2026-10-25T01:00:00Z'}}}
    :{items:[old],default_provider_id:'workbuddy'});
  await h.maintenance('workbuddy').versions.fire('click');
  const current={...old,environment_digest:'new',current_version:'1.1.0',cli_version:'2.1.0'};
  h.renderRuntimes([current]);
  assert.equal(h.maintenance('workbuddy').versions.disabled,false);
  assert.equal(h.maintenanceValue(current).current_version,'1.1.0');
  assert.equal(h.maintenanceValue(current).cli_version,'2.1.0');
  assert.equal(h.calls.filter(c=>c.method==='POST').length,1);
});

test('confirmed update shows restart pending rather than claiming the running SDK switched', async () => {
  const h=await harness();h.confirm(true);
  const value={...sdk,sdk_id:'codex-python-sdk',enabled:true,update_enabled:true,update_available:true,latest_version:'1.1.0',current_version:'1.0.0'};
  h.renderRuntimes([value]);
  h.respond(async(url,options)=>options.method==='POST'?{item:{...value,current_version:'1.1.0',update_available:false},restart_required:true}:{items:[value],default_provider_id:'codex'});
  await h.maintenance('codex').update.fire('click');
  assert.match(h.maintenance('codex').status.textContent,/待重启生效/);
  assert.equal(h.maintenance('codex').check.disabled,false);
});

for (const locale of ['zh','en']) test(`${locale}: staged installer progress uses GET, survives late responses and reports cleanup`,async()=>{
  const h=await harness(locale), now=Date.now();
  const base={...sdk,sdk_id:'codex-python-sdk',enabled:true,update_enabled:true,update_available:true,current_version:'1.0.0',latest_version:'1.1.0'};
  const status={operation_id:'update_synthetic',target_version:'1.1.0',started_at:new Date(now).toISOString(),updated_at:new Date(now+100).toISOString(),status:'running',phase:'downloading',filename:'openai_codex_cli_bin-1.1.0-py3-none-win_amd64.whl',downloaded_bytes:80000000,total_bytes:154000000};
  h.respond(async()=>({items:[{...base,update_status:status}],default_provider_id:'codex'}));
  await h.loadRuntimes();
  let controls=h.maintenance('codex');
  assert.match(controls.status.textContent,/80.0 \/ 154.0 MB/);
  assert.ok(controls.update.disabled && controls.versions.disabled);
  assert.ok(h.timeouts().includes(2000));
  h.respond(async()=>({items:[{...base,update_status:{...status,updated_at:new Date(now+50).toISOString(),downloaded_bytes:1000}}],default_provider_id:'codex'}));
  await h.loadRuntimes();assert.match(h.maintenance('codex').status.textContent,/80.0 \/ 154.0 MB/);
  h.respond(async()=>({items:[{...base,update_status:{...status,updated_at:new Date(now+200).toISOString(),status:'cleanup_pending',phase:'cleanup'}}],default_provider_id:'codex'}));
  await h.loadRuntimes();controls=h.maintenance('codex');
  assert.match(controls.status.textContent,locale==='zh'?/清理尚未确认/:/cleanup is unconfirmed/);
  assert.ok(controls.update.disabled && controls.check.disabled);
  assert.equal(h.calls.some(c=>c.method==='POST'),false);
});

test('a previous completed operation cannot finish a newly submitted update',async()=>{
  const h=await harness();h.confirm(true);
  const base={...sdk,sdk_id:'codex-python-sdk',enabled:true,update_enabled:true,update_available:true,current_version:'1.0.0',latest_version:'1.1.0'};
  const old={operation_id:'old',status:'completed',target_version:'1.1.0',phase:'restart_pending',started_at:'2020-01-01T00:00:00Z',updated_at:'2020-01-01T00:00:00Z'};
  let finish;
  h.renderRuntimes([base]);
  h.respond(async(url,options)=>options.method==='POST'?await new Promise(resolve=>{finish=resolve;}):{items:[{...base,update_status:old}],default_provider_id:'codex'});
  const submitted=h.maintenance('codex').update.fire('click');await settle();await settle();
  assert.ok(h.maintenance('codex').update.disabled);
  assert.doesNotMatch(h.maintenance('codex').status.textContent,/待重启生效/);
  finish({item:{...base,current_version:'1.1.0'},restart_required:true});await submitted;
  assert.equal(h.calls.filter(c=>c.method==='POST').length,1);
});

test('summary loads saved WorkBuddy models before full eligibility and only issues GET', async () => {
  const ui = await harness();
  const runtime = {...sdk, provider_id:'workbuddy', enabled:true, selectable:false, authenticated:null,
    environment_digest:'env', service_site_digest:'site', runtime_configuration_revision:1,
    display_status:{snapshot_id:1, instance_id:'boot', binding:'metadata', stale:false},
    status_refresh:{status:'refreshing', running:true, stale:true},
    authentication:{status:'not_checked', authenticated:null, display_only:true, revision:1, capabilities:{}}};
  ui.respond(async url => url.includes('/models?view=summary')
    ? {items:[{...model('saved'),selectable:false,display_pending:true}], display_status:runtime.display_status, status_refresh:runtime.status_refresh}
    : url.endsWith('/authentication') ? {authentication:runtime.authentication} : {items:[runtime],default_provider_id:'workbuddy'});
  ui.renderRuntimes([runtime]); await settle(); await settle();
  assert.ok(ui.calls.some(call=>call.url.endsWith('/models?view=summary')));
  assert.equal(ui.calls.some(call=>call.url.endsWith('/authentication')),false);
  assert.equal(ui.calls.filter(call=>call.method).length,0);
  assert.match(ui.root.textContent,/完整状态核对中/);
  const picker=ui.root.querySelector('.sdk-model-section').querySelector('.sdk-model-picker').querySelector('select'); assert.equal(picker.value,'saved');
  assert.equal(button(ui.root,'检查兼容性').disabled,true);
});

test('summary completion preserves picker identity, selection and disables stale saves', async () => {
  const ui = await harness();
  const section = new Node('section');
  const runtime={...sdk,provider_id:'workbuddy',authenticated:true,installed:true,model_check_supported:true};
  const payload={...catalog,display_status:{snapshot_id:1},status_refresh:{stale:true},
    items:[{...model('model-a'),display_pending:true,selectable:false},{...model('model-b'),display_pending:true,selectable:false}]};
  // Render into the same real UI component without reconstructing its picker.
  const first=ui.render(runtime,payload), picker=first.querySelector('select');
  picker.value='model-b'; await picker.fire('change');
  first.updateModelSummary(runtime,{...payload,status_refresh:{stale:false},items:[model('model-a'),model('model-b')]});
  assert.equal(first.querySelector('select'),picker); assert.equal(picker.value,'model-b');
  assert.equal(ui.calls.length,0);
  first.updateModelSummary(runtime,payload);
  assert.equal(button(first,'检查兼容性').disabled,true);
  assert.equal(first.querySelectorAll('button').some(node=>node.textContent==='设为默认模型'),false);
});

test('a pending models response arriving after full status is ready follows with one new GET', async () => {
  const ui=await harness(); let release, modelReads=0;
  let runtime={...sdk,provider_id:'workbuddy',enabled:true,display_status:{instance_id:'boot',snapshot_id:1,binding:'same',stale:false},
    status_refresh:{status:'refreshing',running:true,stale:true},
    authentication:{status:'not_checked',authenticated:null,revision:1,capabilities:{},display_only:true}};
  ui.respond(async url=>{
    if(url.endsWith('/models?view=summary')){
      modelReads++;
      if(modelReads===1) await new Promise(resolve=>{release=resolve;});
      return {items:[model('saved')],display_status:runtime.display_status,status_refresh:runtime.status_refresh};
    }
    if(url.endsWith('/authentication'))return {authentication:runtime.authentication};
    return {items:[runtime],default_provider_id:'workbuddy'};
  });
  ui.renderRuntimes([runtime]); await settle();assert.equal(modelReads,1);
  runtime={...runtime,status_refresh:{status:'ready',running:false,stale:false,checked_at:'new'}};
  await ui.loadRuntimes(); release(); await settle(); await settle();
  assert.equal(modelReads,2);
  assert.equal(ui.calls.filter(call=>call.method).length,0);
});

for (const provider of ["codex", "workbuddy"]) for (const locale of ["zh", "en"]) {
  test(`${provider}/${locale}: valid check is reused after GET preflight; zero model checks`, async () => {
    const ui = await harness(locale);
    const runtime = {...sdk, provider_id: provider, authenticated: true, installed: true, model_check_supported: true};
    const payload = {...catalog, items: [{...model("target"), compatibility_applicability: {status: "reusable"},
      check: {compatible: true, check_digest: "saved-proof"}}]};
    ui.respond(async (url, options) => url.endsWith("/models") ? payload : {items: []});
    const section = ui.render(runtime, payload);
    assert.match(section.textContent, locale === "zh" ? /可直接保存/ : /reusable/);
    await button(section, locale === "zh" ? "设为默认模型" : "Set as default model").fire("click");
    assert.equal(ui.calls[0].method, undefined);
    assert.ok(ui.calls[0].url.endsWith("/models"));
    const writes = ui.calls.filter(call => call.method);
    assert.equal(writes.length, 1);
    assert.equal(writes[0].method, "PUT");
    assert.ok(writes[0].url.endsWith("/default-model"));
    assert.deepEqual(JSON.parse(writes[0].body), {model_id: "target"});
  });

  test(`${provider}/${locale}: changed binding preflight refuses save without automatic probe`, async () => {
    const ui = await harness(locale);
    const runtime = {...sdk, provider_id: provider, authenticated: true};
    const payload = {...catalog, items: [{...model("target"), check: {authentication_revision: 8}}]};
    ui.respond(async () => ({...payload, items: [{...payload.items[0], check: {authentication_revision: 9}}]}));
    const section = ui.render(runtime, payload);
    await button(section, locale === "zh" ? "设为默认模型" : "Set as default model").fire("click");
    assert.equal(ui.calls.length, 1);
    assert.equal(ui.calls[0].method, undefined);
    assert.match(ui.root.textContent, locale === "zh" ? /绑定已变化/ : /binding changed/);
  });
}

for (const locale of ["zh", "en"]) test(`${locale}: pending recovery is not failure or a prompt to repeat model checks`, async () => {
  const ui = await harness(locale);
  const runtime = {...sdk, provider_id: "workbuddy", authenticated: true, model_check_supported: true};
  const section = ui.render(runtime, {items: [{...model("old"), selectable: false, check_stale: true,
    check_status: "not_checked", compatibility_applicability: {status: "recovery_pending"}}]});
  assert.match(section.textContent, locale === "zh" ? /历史结果待承接/ : /Historical result awaits/);
  assert.equal(button(section, locale === "zh" ? "检查兼容性" : "Check compatibility").disabled, true);
  assert.equal(ui.calls.length, 0);
});

test("Codex unknown check response persists across rerender and only reads on another click", async () => {
  const ui = await harness();
  ui.respond(async (url, options) => {
    if (options.method) throw new TypeError("synthetic response lost");
    return catalog;
  });
  let section = ui.render(sdk, catalog);
  await button(section, "检查兼容性").fire("click");
  section = ui.render(sdk, catalog);
  await button(section, "检查兼容性").fire("click");
  assert.equal(ui.calls.filter(call => call.method).length, 1);
  assert.equal(ui.calls[1].method, undefined);
  assert.match(ui.root.textContent, /不自动重发/);
});

test("Codex new effort needs an explicit light check before save, preserving request bodies", async () => {
  const ui = await harness();
  ui.respond(async (url, options) => url.endsWith("/models/check") ? {check: {compatible: true}}
    : url.endsWith("/models") ? catalog : {items: []});
  const section = ui.render(sdk, catalog);
  const effort = section.querySelector(".sdk-model-row").querySelector("select");
  effort.value = "low"; await effort.fire("change");
  assert.equal(button(section, "保存推理强度").disabled, true);
  await button(section, "保存推理强度").fire("click");
  assert.equal(ui.calls.length, 0);
  await button(section, "检查兼容性").fire("click");
  assert.equal(button(section, "保存推理强度").disabled, false);
  await button(section, "保存推理强度").fire("click");
  const writes = ui.calls.filter(call => call.method);
  assert.equal(writes.length, 2);
  assert.deepEqual(JSON.parse(writes[0].body), {model_id: "model-a", reasoning_effort: "low"});
  assert.deepEqual(JSON.parse(writes[1].body), {reasoning_effort: "low"});
});

for (const locale of ["zh", "en"]) test(`${locale}: WorkBuddy qualification follows the selected model without inheriting legacy passes`, async () => {
  const ui = await harness(locale);
  const makeQualification = (id, passed) => ({model: id, effective_pass_count: passed ? 1 : 0,
    historical_pass_count: passed ? 1 : 0, legacy_pass_count: 13,
    operations: {sample_discovery: {status: passed ? "validated" : "unverified", permission_modes: passed ? ["bounded"] : [],
      required_permission_modes: ["bounded"], validation_origins: passed ? {bounded: {kind: "reuse"}} : {},
      reason: passed ? null : "workbuddy_qualification_validation_required"}}});
  const workbuddy = {provider_id: "workbuddy", default_model_id: "deepseek-v4.1-flash", authenticated: true, installed: true,
    business_evaluation: {required_operation_count: 17}};
  const payload = {items: [{...model("deepseek-v4.1-flash", true), qualification: makeQualification("deepseek-v4.1-flash", true)},
    {...model("hy4-preview"), qualification: makeQualification("hy4-preview", false)}]};
  const section = ui.render(workbuddy, payload);
  const details = section.querySelector(".sdk-technical-details");
  assert.match(details.textContent, /deepseek-v4\.1-flash/);
  assert.match(details.textContent, /1\/17/);
  assert.match(details.textContent, locale === "zh" ? /审计复用/ : /audited reuse/);
  assert.match(details.textContent, locale === "zh" ? /旧格式通过 13/ : /13 legacy passes/);
  const picker = section.querySelector(".sdk-model-picker").querySelector("select");
  picker.value = "hy4-preview"; await picker.fire("change");
  assert.match(details.textContent, /hy4-preview/);
  assert.match(details.textContent, /0\/17/);
  assert.doesNotMatch(details.textContent, /deepseek-v4\.1-flash/);
  assert.doesNotMatch(details.textContent, /Eligible · audited reuse|有效 · 审计复用/);
  assert.equal(ui.calls.length, 0);
  assert.match(picker.getAttribute("aria-label"), locale === "zh" ? /选择模型/ : /Choose model/);
});

test("partial WorkBuddy permission validation does not display complete eligibility", async () => {
  const ui = await harness();
  const workbuddy = {provider_id: "workbuddy", default_model_id: "model-a", authenticated: true};
  const payload = {items: [{...model("model-a", true), qualification: {model: "model-a", effective_pass_count: 0,
    historical_pass_count: 1, legacy_pass_count: 0, operations: {review_agent_feedback: {
      status: "validated", required_permission_modes: ["bounded", "trusted_local"], permission_modes: ["bounded"],
      reason: "workbuddy_qualification_validation_required"}}}}]};
  const section = ui.render(workbuddy, payload);
  assert.match(section.querySelector(".sdk-technical-details").textContent, /待验证／核对/);
  assert.doesNotMatch(section.querySelector(".sdk-technical-details").textContent, /有效 · 真实验证/);
});

test("a legacy backend's unscoped PASS cannot certify the selected WorkBuddy model", async () => {
  const ui = await harness();
  const section = ui.render({provider_id: "workbuddy", default_model_id: "model-a", authenticated: true,
    operation_capabilities: {sample_discovery: {status: "validated", permission_modes: ["bounded"]}}}, catalog);
  assert.match(section.querySelector(".sdk-technical-details").textContent, /后端尚未提供此模型的资格资料/);
  assert.doesNotMatch(section.querySelector(".sdk-technical-details").textContent, /有效 · 真实验证/);
});
const buttons = (root) => root.querySelectorAll("button");
const button = (root, text) => buttons(root).find((item) => item.textContent === text);

const authentication = {status: "not_checked", authenticated: null, method: "unknown", source: "unknown", blockers: [],
  capabilities: {login: true, logout: true, switch_account: true, check: true}, account_url: "https://chatgpt.com/"};
const runtimeItem = (extra = {}) => ({provider_id: "workbuddy", sdk_id: "codebuddy-agent-sdk", name: {zh: "WorkBuddy", en: "WorkBuddy"},
  enabled: true, selected: false, can_enable: false, selectable: false, default_model_id: "deepseek-v4.1-flash", blockers: [],
  installed: true, authenticated: true, platform_supported: true, control_status: {status: "ready", revision: 2},
  status_refresh: {status: "refreshing", stale: true, running: true, wait_exceeded: true}, ...extra});

for (const locale of ["zh", "en"]) test(`${locale}: enabled fact is visible while slow qualification cannot grant execution`, async () => {
  const ui = await harness(locale);
  ui.renderRuntimes([runtimeItem()]);
  assert.ok(button(ui.root, locale === "zh" ? "停用 Runtime" : "Disable Runtime"));
  assert.equal(button(ui.root, locale === "zh" ? "启用 Runtime" : "Enable Runtime"), undefined);
  assert.match(ui.root.textContent, locale === "zh" ? /已启用 · 资格核对中/ : /Enabled · qualification pending/);
  assert.match(ui.root.textContent, locale === "zh" ? /后台仍在继续/ : /still running/);
  assert.equal(ui.calls.length, 0);
  ui.renderRuntimes([runtimeItem({enabled: null, control_status: {status: "unknown", running: true}, can_enable: true})]);
  assert.equal(button(ui.root, locale === "zh" ? "启用 Runtime" : "Enable Runtime").disabled, true);
  assert.match(ui.root.textContent, locale === "zh" ? /状态待核对/ : /State not confirmed/);
});

test("status polls survive the old eight-poll limit and preserve account/model nodes and catalog selection", async () => {
  const ui = await harness();
  const ready = runtimeItem({status_refresh: {status: "ready", stale: false, running: false}, authentication});
  ui.respond(async (url) => url.endsWith("/models") ? catalog : {items: [ready], default_provider_id: "codex"});
  ui.renderRuntimes([ready]); await settle();
  const card = ui.root.querySelector(".sdk-card");
  const account = card.querySelector(".sdk-authentication-section");
  const section = card.querySelector(".sdk-model-section");
  const input = section.querySelector(".sdk-model-picker").querySelector("select");
  input.value = "model-b"; await input.fire("change"); input.focus();
  const slow = runtimeItem({authentication});
  ui.respond(async (url) => url.endsWith("/models") ? catalog : {items: [slow], default_provider_id: "codex"});
  await ui.loadRuntimes();
  for (let i = 0; i < 35; i++) await ui.poll();
  assert.ok(ui.calls.filter(call => call.url.endsWith("/sdk-runtimes?view=summary")).length > 8);
  assert.ok(ui.timeouts().includes(15000));
  assert.equal(ui.root.querySelector(".sdk-card"), card);
  assert.equal(card.querySelector(".sdk-authentication-section"), account);
  assert.equal(card.querySelector(".sdk-model-section").querySelector(".sdk-model-picker").querySelector("select"), input);
  assert.equal(input.value, "model-b"); assert.equal(input.focused, true);
  assert.equal(ui.calls.filter(call => call.url.endsWith("/models")).length, 1);
  ui.respond(async () => ({items: [ready], default_provider_id: "codex"})); await ui.poll();
  assert.doesNotMatch(card.querySelector(".sdk-status").textContent, /资格核对中/);
  assert.deepEqual(ui.timeouts(), []);
});

test("hidden pages pause status polls, visible pages resume and list reads are single-flight", async () => {
  const ui = await harness();
  ui.respond(async () => ({items: [runtimeItem()], default_provider_id: "codex"}));
  await ui.loadRuntimes(); await ui.visible(false);
  const count = ui.calls.length; await ui.poll(); assert.equal(ui.calls.length, count);
  await ui.visible(true); assert.equal(ui.calls.length, count + 1);
  let release; const held = new Promise(resolve => {release = resolve;});
  ui.respond(async () => {await held; return {items: [runtimeItem()], default_provider_id: "codex"};});
  const first = ui.loadRuntimes(); const second = ui.loadRuntimes();
  assert.equal(ui.calls.length, count + 2);
  release(); await Promise.all([first, second]);
  ui.hidePage(); assert.deepEqual(ui.timeouts(), []);
});

for (const status of ['unknown','ready']) test(`full ${status}/stale idle result continues polling until ready`, async () => {
  const ui = await harness();
  const pending = runtimeItem({status_refresh:{status,stale:true,running:false},
    display_status:{stale:false,running:false,instance_id:'boot',snapshot_id:1,binding:'same'}});
  const ready = {...pending,status_refresh:{status:'ready',stale:false,running:false}};
  ui.respond(async url => url.includes('/models') ? catalog : {items:[pending],default_provider_id:'workbuddy'});
  await ui.loadRuntimes(); await settle();
  assert.ok(ui.timeouts().includes(2000));
  ui.respond(async url => url.includes('/models') ? catalog : {items:[ready],default_provider_id:'workbuddy'});
  await ui.poll(); await settle();
  assert.deepEqual(ui.timeouts(),[]);
  assert.equal(ui.calls.some(call=>call.url.endsWith('/authentication')),false);
  assert.equal(ui.calls.some(call=>call.method),false);
});

test('last confirmed compatibility and identity survive refresh without enabling stale actions', async () => {
  const ui=await harness();
  const sdk={...runtimeItem(),display_status:{instance_id:'boot',snapshot_id:1,binding:'same',stale:false}};
  const account={...authentication,status:'authenticated',authenticated:true,identity:'masked-account',display_only:true,capabilities:{check:true}};
  const section=ui.renderAuthentication(sdk,account);
  assert.match(section.textContent,/masked-account/);
  assert.equal(button(section,'刷新认证').disabled,true);
  const models=ui.render(sdk,{...catalog,display_status:sdk.display_status,status_refresh:{stale:true},
    items:[{...model('model-a'),last_confirmed:true,display_pending:true,selectable:false,actual_model:'model-a'}]});
  assert.match(models.textContent,/上次兼容通过/);
  assert.match(models.textContent,/model-a/);
  assert.equal(button(models,'检查兼容性').disabled,true);
  assert.equal(ui.calls.length,0);
});

test('authentication summary reuses complete checks and updates capability controls in place',async()=>{
  const ui=await harness();
  const auth={...authentication,status:'authenticated',authenticated:true,identity:'masked-account',revision:1};
  let runtime={...runtimeItem(),environment_digest:'env',service_site_digest:'site',
    display_status:{instance_id:'boot',snapshot_id:1,binding:'same',stale:false},authentication:auth};
  ui.respond(async url=>url.includes('/models')?catalog:{items:[runtime],default_provider_id:'workbuddy'});
  await ui.loadRuntimes();await settle();
  const account=ui.root.querySelector('.sdk-authentication-section'),check=button(account,'刷新认证');
  assert.equal(check.disabled,false);
  runtime={...runtime,authentication:{...auth,display_only:true,capabilities:{}},status_refresh:{stale:true,status:'unknown',running:false}};
  await ui.loadRuntimes();await settle();
  assert.equal(ui.root.querySelector('.sdk-authentication-section'),account);
  assert.equal(button(account,'刷新认证'),check);assert.equal(check.disabled,true);
  runtime={...runtime,authentication:auth,status_refresh:{stale:false,status:'ready',running:false}};
  await ui.loadRuntimes();await settle();assert.equal(check.disabled,false);
  assert.equal(ui.calls.some(call=>call.url.endsWith('/authentication')),false);
});

test('failed status GET stops automatic retry and visibility recovery only reads GET',async()=>{
  const ui=await harness();
  ui.respond(async()=>({items:[runtimeItem()],default_provider_id:'workbuddy'}));
  await ui.loadRuntimes();assert.ok(ui.timeouts().includes(2000));
  ui.respond(async()=>{throw new Error('synthetic network failure');});
  await ui.poll();assert.deepEqual(ui.timeouts(),[]);
  ui.respond(async()=>({items:[runtimeItem({status_refresh:{status:'ready',stale:false,running:false}})],default_provider_id:'workbuddy'}));
  await ui.visible(false);await ui.visible(true);assert.deepEqual(ui.timeouts(),[]);
  assert.equal(ui.calls.some(call=>call.method),false);
});

test('saved authentication progress cannot synchronously reenter the summary renderer',async()=>{
  const ui=await harness();let renders=0;
  const auth={...authentication,status:'authenticated',authenticated:true,revision:9,
    operation:{operation_id:'historical-completed',status:'completed'}};
  const sdk={provider_id:'workbuddy',display_status:{instance_id:'boot'},authentication:auth};
  const view={sdk,card:new Node('article'),cachedAuthentication:auth,accountSection:{updateAuthenticationSummary(){
    assert.equal(++renders,1);ui.updateAuthentication(sdk,view);
  }}};
  ui.updateAuthentication(sdk,view);
  assert.equal(renders,1);assert.equal(ui.calls.length,0);
});

test('explicit form reset clears the detached model renderer and reloads the same summary key',async()=>{
  const ui=await harness();let reads=0;
  const sdk={...runtimeItem(),display_status:{instance_id:'boot',binding:'same',stale:false}};
  ui.respond(async url=>url.includes('/models')?(reads++,{...catalog,display_status:sdk.display_status}):{items:[sdk],default_provider_id:'workbuddy'});
  ui.renderRuntimes([sdk]);await settle();await settle();
  assert.ok(ui.root.querySelector('.sdk-model-section').querySelector('select'));
  assert.equal(reads,1);ui.refreshForms('workbuddy');await settle();await settle();
  assert.ok(ui.root.querySelector('.sdk-model-section').querySelector('select'));
  assert.equal(reads,2);
});

test('an authentication binding change also invalidates an in-flight model summary key',async()=>{
  const ui=await harness();let reads=0,release;
  let sdk={...runtimeItem(),authentication:{...authentication,revision:1},display_status:{instance_id:'boot',binding:'same',stale:false}};
  ui.respond(async url=>{
    if(url.includes('/models')){if(++reads===1)await new Promise(resolve=>release=resolve);return {...catalog,display_status:sdk.display_status};}
    return {items:[sdk],default_provider_id:'workbuddy'};
  });
  ui.renderRuntimes([sdk]);await settle();
  sdk={...sdk,authentication:{...sdk.authentication,revision:2}};
  await ui.loadRuntimes();release();await settle();await settle();
  assert.equal(reads,2);assert.ok(ui.root.querySelector('.sdk-model-section').querySelector('select'));
  assert.equal(ui.calls.some(call=>call.url.endsWith('/authentication')),false);
});

test("a successful enable response is applied before list refresh and stale GET cannot undo it", async () => {
  const ui = await harness();
  const disabled = runtimeItem({enabled: false, can_enable: true, runtime_configuration_revision: 2,
    status_refresh: {status: "ready", stale: false}, installed: false, authenticated: false});
  ui.renderRuntimes([disabled]);
  let release; const held = new Promise(resolve => {release = resolve;});
  ui.respond(async (url) => url.endsWith("/enable") ? {item: {...disabled, enabled: true, runtime_configuration_revision: 3}}
    : {items: [await held], default_provider_id: "codex"});
  const pending = button(ui.root, "启用 Runtime").fire("click"); await settle();
  assert.ok(button(ui.root, "停用 Runtime"));
  release(disabled); await pending;
  assert.ok(button(ui.root, "停用 Runtime"));
  assert.equal(button(ui.root, "启用 Runtime"), undefined);
  assert.equal(ui.calls.filter(call => call.method === "POST").length, 1);
});

test("a GET already in flight before a confirmed mutation cannot overwrite that mutation", async () => {
  const ui = await harness();
  ui.renderRuntimes([runtimeItem({enabled: false})]);
  let release; const held = new Promise(resolve => {release = resolve;});
  ui.respond(async () => {await held; return {items: [runtimeItem({enabled: false})], default_provider_id: "codex"};});
  const reading = ui.loadRuntimes();
  ui.applyRuntimeResult({item: runtimeItem({enabled: true, runtime_configuration_revision: 3})});
  release(); await reading;
  assert.ok(button(ui.root, "停用 Runtime"));
  assert.equal(button(ui.root, "启用 Runtime"), undefined);
});

test("a genuine status failure offers a read-only retry without authentication or model probes", async () => {
  const ui = await harness();
  const failed = runtimeItem({status_refresh: {status: "failed", stale: true, running: false, error_code: "status_projection_failed"}});
  ui.renderRuntimes([failed]);
  ui.respond(async () => ({items: [failed], default_provider_id: "codex"}));
  await button(ui.root, "重新读取状态").fire("click");
  assert.equal(ui.calls.length, 1); assert.equal(ui.calls[0].method, undefined);
  assert.match(ui.calls[0].url, /\/sdk-runtimes\?view=summary$/);
  assert.deepEqual(ui.timeouts(), []);
});
for (const locale of ["zh", "en"]) test(`${locale}: shared authentication renders without automatic probes and sign-in does not enable runtime`, async () => {
  const ui = await harness(locale); const section = ui.renderAuthentication(sdk, authentication);
  assert.equal(ui.calls.length, 0); assert.equal(button(section, locale === "zh" ? "退出登录" : "Sign out"), undefined);
  ui.respond(async () => ({operation: {operation_id: "auth_test", status: "queued"}}));
  await button(section, locale === "zh" ? "登录" : "Sign in").fire("click");
  assert.equal(ui.calls.length, 1); assert.match(ui.calls[0].url, /codex\/authentication\/operations$/);
  assert.equal(JSON.parse(ui.calls[0].body).action, "login"); assert.ok(JSON.parse(ui.calls[0].body).requestId);
  assert.equal(ui.calls[0].headers["X-SAPBA-Action"], "runtime-authentication");
  assert.doesNotMatch(ui.calls[0].url, /models|enable|default/);
});

test("logout and switch require confirmation; uncertain requests only retry original content", async () => {
  const ui = await harness(); const section = ui.renderAuthentication(sdk, {...authentication, status: "authenticated", authenticated: true});
  await button(section, "退出登录").fire("click"); assert.equal(ui.calls.length, 0);
  ui.confirm(true); ui.respond(async () => { throw new Error("response lost"); });
  await button(section, "切换账号").fire("click");
  const original = ui.calls[0].body;
  assert.equal(JSON.parse(original).confirm_provider_id, "codex");
  assert.equal(button(section, "退出登录").disabled, true);
  ui.respond(async () => ({operation: {operation_id: "auth_test", status: "queued"}}));
  await button(section, "重传原请求").fire("click"); assert.equal(ui.calls[1].body, original);
  assert.equal(ui.stored.size, 0);
});

test("waiting login and reload show memory-only official link and cancellation", async () => {
  const ui = await harness();
  const section = ui.renderAuthentication(sdk, {...authentication, operation: {operation_id: "auth_wait", status: "waiting_authentication",
    auth_url: "https://auth.openai.com/login?state=memory-only"}});
  assert.equal(button(section, "登录").disabled, true);
  assert.equal(section.querySelectorAll("a").find((a) => a.textContent === "打开官方登录页面").rel, "noopener noreferrer");
  assert.equal(ui.stored.size, 0);
  ui.respond(async () => ({operation: {operation_id: "auth_wait", status: "cancelled"}}));
  await button(section, "取消").fire("click"); assert.match(ui.calls[0].url, /auth_wait\/cancel$/);
  assert.doesNotMatch(section.textContent, /打开官方登录页面/);
});

test("external authentication disables mutation; catalog loading stays read-only", async () => {
  const ui = await harness();
  const section = ui.renderAuthentication(sdk, {...authentication, authenticated: true,
    capabilities: {check: true, login: false, logout: false, switch_account: false, reason: "runtime_authentication_external_configuration"}});
  assert.equal(button(section, "退出登录").disabled, true); assert.match(section.textContent, /不收集 API Key/);
  ui.respond(async () => ({...catalog, catalog_status: "stale"}));
  await ui.loadModels({...sdk, model_discovery_supported: true}, new Node("section"));
  assert.equal(ui.calls.length, 1); assert.equal(ui.calls[0].method, undefined);
});

test("authentication check waits for backend budget and cleanup without probing a model", async () => {
  const ui = await harness(); const section = ui.renderAuthentication(sdk, authentication);
  let resolve; const pending = new Promise((done) => { resolve = done; });
  ui.respond(async () => { await pending; return {items: [], authentication}; });
  const checking = button(section, "刷新认证").fire("click");
  assert.ok(ui.timeouts().includes(45000));
  assert.match(ui.calls[0].url, /authentication\/check$/);
  assert.equal(ui.calls[0].headers["X-SAPBA-Action"], "runtime-authentication");
  resolve(); await checking;
  assert.equal(ui.calls.some((call) => /models|enable|default/.test(call.url)), false);
});

for (const locale of ["zh", "en"]) test(`${locale}: model dropdown renders one detail and selection never writes defaults`, async () => {
  const ui = await harness(locale);
  const section = ui.render(sdk, catalog);
  const picker = section.querySelector(".sdk-model-picker").querySelector("select");
  assert.equal(picker.value, "model-a"); assert.equal(picker.children.length, 2);
  assert.match(picker.getAttribute("aria-label"), locale === "zh" ? /选择模型/ : /Choose model/);
  assert.equal(section.querySelectorAll(".sdk-model-row").length, 1);
  picker.value = "model-b"; await picker.fire("change");
  assert.equal(section.querySelectorAll(".sdk-model-row").length, 1);
  assert.match(section.querySelector(".sdk-model-name").textContent, /model-b/);
  assert.equal(ui.calls.length, 0);
  const reloaded = ui.render(sdk, catalog);
  assert.equal(reloaded.querySelector(".sdk-model-picker").querySelector("select").value, "model-b");
  assert.equal(catalog.items[0].selected, true);
});

test("Codex check uses the selected model and explicit effort; busy locks the picker", async () => {
  const ui = await harness(); const section = ui.render(sdk, catalog);
  const picker = section.querySelector(".sdk-model-picker").querySelector("select");
  picker.value = "model-b"; await picker.fire("change");
  const row = section.querySelector(".sdk-model-row"); const effort = row.querySelector("select");
  effort.value = "low"; await effort.fire("change");
  assert.equal(button(row, "设为默认模型").disabled, true);
  let resolve; const pending = new Promise((done) => { resolve = done; });
  ui.respond(async () => { await pending; return { check: { compatible: true } }; });
  const checking = button(row, "检查兼容性").fire("click");
  assert.equal(row.getAttribute("aria-busy"), "true"); assert.equal(picker.disabled, true); assert.equal(effort.disabled, true);
  assert.deepEqual(JSON.parse(ui.calls[0].body), { model_id: "model-b", reasoning_effort: "low" });
  resolve(); await checking;
  assert.equal(picker.disabled, false); assert.equal(effort.value, "low");
  assert.equal(button(row, "设为默认模型").disabled, true);
  assert.equal(ui.calls.length, 1);
});

test("Codex stale, retired and empty catalogs retain their action guards", async () => {
  const ui = await harness();
  for (const payload of [{ ...catalog, catalog_status: "stale" }, { ...catalog, items: [{ ...model("old", true), retired: true }] }]) {
    const row = ui.render(sdk, payload).querySelector(".sdk-model-row");
    assert.equal(row.querySelector("select").disabled, true);
    assert.equal(button(row, "保存推理强度"), undefined);
    assert.ok(!button(row, "检查兼容性") || button(row, "检查兼容性").disabled);
  }
  const empty = ui.render(sdk, { ...catalog, items: [] });
  assert.equal(empty.querySelector(".sdk-model-picker").querySelector("select").disabled, true);
  assert.equal(empty.querySelectorAll(".sdk-model-row").length, 0);
  assert.equal(ui.calls.length, 0);
});

test("WorkBuddy picker checks SDK catalog entries, preserves selection and has no custom or effort control", async () => {
  const ui = await harness(); const wb = { ...sdk, provider_id: "workbuddy", name: { zh: "WorkBuddy" }, installed: true, authenticated: true, model_check_supported: true, default_model_id: null };
  const payload = { items: [model("wb-a"), { ...model("wb-b"), source: "locked_cli_help", check_status: "not_checked", selectable: false }] };
  const section = ui.render(wb, payload);
  const picker = section.querySelector(".sdk-model-picker").querySelector("select");
  assert.equal(section.querySelectorAll(".sdk-model-row").length, 1);
  assert.equal(section.querySelector(".sdk-model-row").querySelectorAll("select").length, 0);
  picker.value = "wb-b"; await picker.fire("change");
  assert.equal(button(section, "设为默认模型"), undefined); assert.match(section.textContent, /CLI 声明，尚未验证/);
  assert.equal(picker.querySelectorAll("option").some(option => option.value === "__custom__"), false);
  assert.equal(section.querySelector("input"), null);
  assert.equal(ui.calls.length, 0);
  ui.respond(async () => ({ check: { compatible: true }, items: [], default_provider_id: "codex" }));
  await button(section, "检查兼容性").fire("click");
  assert.match(ui.calls[0].url, /workbuddy\/models\/check$/);
  const body = JSON.parse(ui.calls[0].body);
  assert.equal(body.model_id, "wb-b"); assert.match(body.requestId, /^[a-f0-9-]{36}$/);
  const reloaded = ui.render(wb, payload);
  assert.equal(reloaded.querySelector(".sdk-model-picker").querySelector("select").value, "wb-b");
  assert.equal(reloaded.querySelector("input"), null);
  assert.equal(ui.render(sdk, catalog).querySelector(".sdk-model-picker").querySelector("select").value, "model-a");
});

test("WorkBuddy unauthenticated and absent catalogs never enable a model probe", async () => {
  const ui = await harness();
  const section = ui.render({ ...sdk, provider_id: "workbuddy", installed: false, authenticated: false, model_check_supported: true }, { items: [] });
  assert.equal(button(section, "检查兼容性"), undefined);
  assert.equal(button(section, "刷新模型目录").disabled, true);
  assert.equal(section.querySelector(".sdk-model-picker").querySelector("select").disabled, true);
  assert.match(section.textContent, /尚未取得 SDK 模型目录/);
  assert.equal(ui.calls.length, 0);
});

for (const locale of ["zh", "en"]) test(`${locale}: compatible unevaluated model shows three independent layers`, async () => {
  const ui = await harness(locale);
  const wb = {provider_id: "workbuddy", authenticated: true, sdk_integration: {status: "certified"},
    business_evaluation: {required_operation_count: 19, status: "not_evaluated"}};
  const section = ui.render(wb, {items: [{...model("new-model"), check_contract: "sapba.workbuddy.model-check/3"}]});
  assert.equal(section.querySelectorAll("h4").length, 3);
  assert.match(section.textContent, locale === "zh" ? /业务尚未评测不阻断/ : /prior business evaluation is not required/);
  assert.match(section.textContent, /sapba.workbuddy.model-check\/3/);
  assert.doesNotMatch(section.textContent, /0\/15/);
  assert.ok(button(section, locale === "zh" ? "设为默认模型" : "Set as default model"));
});

test("unknown WorkBuddy compatibility response only reads original status on next click", async () => {
  const ui = await harness();
  const wb = {provider_id: "workbuddy", authenticated: true, installed: true, model_check_supported: true};
  const section = ui.render(wb, {items: [model("uncertain")]});
  ui.respond(async (_url, options) => {
    if (options.method === "POST") throw new TypeError("connection-lost");
    return {items: [], model_check_requests: [{request_id: JSON.parse(ui.calls[0].body).requestId, status: "running"}]};
  });
  await button(section, "检查兼容性").fire("click");
  await button(section, "检查兼容性").fire("click");
  assert.equal(ui.calls.filter(call => call.method === "POST").length, 1);
  assert.equal(ui.calls.length, 2);
  assert.match(ui.calls[1].url, /workbuddy\/models$/);
});

test("WorkBuddy control fact revision is not replaced by model configuration revision", async () => {
  const ui = await harness();
  const wb = {provider_id: "workbuddy", enabled: false, can_enable: true, installed: true,
    control_status: {revision: 299, status: "ready"}, runtime_configuration_revision: 0};
  ui.renderRuntimes([wb]);
  ui.applyRuntimeResult({item: {...wb, enabled: true, control_status: {revision: 300, status: "ready"}}});
  assert.ok(button(ui.root, "停用 Runtime"));
  assert.equal(button(ui.root, "启用 Runtime"), undefined);
});

for (const locale of ["zh", "en"]) test(`${locale}: expired WorkBuddy checks require renewal, not model retirement`, async () => {
  const ui = await harness(locale);
  const wb = {...sdk, provider_id: "workbuddy", installed: true, authenticated: true, model_check_supported: true};
  const section = ui.render(wb, {items: [{...model("history"), check_stale: true, retired: false,
    check_status: "not_checked", selectable: false, source: "user_specified", actual_model: null}]});
  assert.match(section.textContent, locale === "zh" ? /当前站点、账号或环境需要重新核对/ : /Recheck for the current site, account or environment/);
  assert.doesNotMatch(section.textContent, locale === "zh" ? /当前 SDK 已不再提供/ : /No longer offered/);
  assert.equal(button(section, locale === "zh" ? "检查兼容性" : "Check compatibility").disabled, false);
  assert.equal(button(section, locale === "zh" ? "设为默认模型" : "Set as default model"), undefined);
  assert.equal(ui.calls.length, 0);
});

for (const locale of ["zh", "en"]) test(`${locale}: site choice is explicit and sign-in freezes the saved site`, async () => {
  const ui = await harness(locale);
  const wb = {...sdk, provider_id: "workbuddy", service_site: "china", service_site_revision: 1,
    service_site_digest: "a".repeat(64), service_site_switch_allowed: true};
  const section = ui.renderAuthentication(wb, authentication);
  const picker = section.querySelector(".sdk-service-site").querySelector("select");
  const save = button(section, locale === "zh" ? "保存站点" : "Save site");
  assert.equal(picker.value, "china"); assert.equal(save.disabled, true);
  picker.value = "international"; await picker.fire("change");
  assert.equal(ui.calls.length, 0); assert.equal(save.disabled, false);
  await save.fire("click"); assert.equal(ui.calls.length, 0);
  ui.confirm(true); await save.fire("click");
  assert.equal(ui.calls[0].method, "PUT"); assert.match(ui.calls[0].url, /workbuddy\/service-site$/);
  const body = JSON.parse(ui.calls[0].body);
  assert.equal(body.service_site, "international"); assert.equal(body.expected_revision, 1); assert.ok(body.requestId);
  assert.equal(ui.calls.some(call => /authentication\/operations|models\/check|enable|default/.test(call.url)), false);
  ui.calls.length = 0;
  ui.respond(async () => ({operation: {operation_id: "site-login", status: "queued"}}));
  const reloaded = ui.renderAuthentication(wb, authentication);
  await button(reloaded, locale === "zh" ? "登录" : "Sign in").fire("click");
  assert.equal(JSON.parse(ui.calls[0].body).service_site_digest, wb.service_site_digest);
});

test("site switch uncertainty only retransmits the original site and revision", async () => {
  const ui = await harness(); ui.confirm(true);
  const wb = {...sdk, provider_id: "workbuddy", service_site: "china", service_site_revision: 1,
    service_site_digest: "a".repeat(64), service_site_switch_allowed: true};
  let section = ui.renderAuthentication(wb, authentication);
  const picker = section.querySelector(".sdk-service-site").querySelector("select");
  picker.value = "international"; await picker.fire("change");
  ui.respond(async () => { throw new Error("response lost"); });
  await button(section, "保存站点").fire("click");
  const original = ui.calls[0].body;
  section = ui.renderAuthentication(wb, authentication);
  assert.equal(button(section, "登录").disabled, true);
  ui.respond(async () => ({items: []}));
  await button(section, "重传原站点设置").fire("click");
  const writes = ui.calls.filter(call => call.method === "PUT");
  assert.equal(writes[1].body, original); assert.equal(ui.stored.size, 0);
});

test("Codex has no site control and external/active WorkBuddy cannot change sites", async () => {
  const ui = await harness();
  assert.equal(ui.renderAuthentication(sdk, authentication).querySelector(".sdk-service-site"), null);
  for (const flags of [{service_site_switch_allowed: false}, {service_site_switch_allowed: true, service_site_external_configuration: true}]) {
    const section = ui.renderAuthentication({...sdk, provider_id: "workbuddy", service_site: "china", service_site_revision: 1, ...flags}, authentication);
    assert.equal(section.querySelector(".sdk-service-site").querySelector("select").disabled, true);
    assert.equal(button(section, "保存站点").disabled, true);
  }
});
