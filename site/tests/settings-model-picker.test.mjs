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
  runInContext(`${copyDefinition}\n${script}\nglobalThis.settingsUI = { renderModels, renderAuthentication, loadModels, render, load, applyRuntimeResult };`, context);
  await settle(); calls.length = 0;
  return { root, calls, stored, timeouts: () => [...timers.values()].map((item) => item.ms), confirm: (value) => { confirmed = value; }, respond: (fn) => { response = fn; },
    renderRuntimes: (items) => context.settingsUI.render(items, "codex"),
    loadRuntimes: (options = {incremental: true}) => context.settingsUI.load(false, options),
    applyRuntimeResult: (payload) => context.settingsUI.applyRuntimeResult(payload),
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
  assert.ok(ui.calls.filter(call => call.url.endsWith("/sdk-runtimes")).length > 8);
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
  assert.match(ui.calls[0].url, /\/sdk-runtimes$/);
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
