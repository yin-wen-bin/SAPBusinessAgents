import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createContext, runInContext } from "node:vm";

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
}

const settle = () => new Promise((resolve) => setImmediate(resolve));
async function harness(locale = "zh") {
  const root = new Node("section"); root.dataset.apiBase = "http://test";
  for (const attribute of ["data-sdk-grid", "data-sdk-alert", "data-no-default"]) {
    const child = new Node("div"); child.setAttribute(attribute, ""); root.append(child);
  }
  const calls = []; let response = async () => ({ items: [], default_provider_id: "codex" });
  const context = createContext({ locale, document: { querySelector: () => root, createElement: (tag) => new Node(tag) },
    fetch: async (url, options = {}) => { calls.push({ url, ...options }); return { ok: true, json: () => response(url, options) }; },
    window: { confirm: () => false, prompt: () => null },
  });
  runInContext(`${copyDefinition}\n${script}\nglobalThis.settingsUI = { renderModels };`, context);
  await settle(); calls.length = 0;
  return { root, calls, respond: (fn) => { response = fn; }, render: (sdk, payload) => {
    const section = new Node("section"); context.settingsUI.renderModels(sdk, section, payload); return section;
  } };
}
const sdk = { provider_id: "codex", name: { zh: "Codex", en: "Codex" }, default_model_id: "model-a" };
const model = (id, selected = false) => ({ model_id: id, display_name: id, selected, check_status: "compatible", selectable: true,
  supported_reasoning_efforts: ["low", "high"], reasoning_effort: "high", saved_reasoning_effort: "high", default_reasoning_effort: "low" });
const catalog = { catalog_status: "ready", model_catalog_complete: true, items: [model("model-a", true), model("model-b")] };
const buttons = (root) => root.querySelectorAll("button");
const button = (root, text) => buttons(root).find((item) => item.textContent === text);

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

test("WorkBuddy picker is independent, supports explicit custom checks and has no effort control", async () => {
  const ui = await harness(); const wb = { ...sdk, provider_id: "workbuddy", name: { zh: "WorkBuddy" }, installed: true, authenticated: true, model_check_supported: true, default_model_id: null };
  const payload = { items: [model("wb-a"), { ...model("wb-b"), check_status: "not_checked", selectable: false }] };
  const section = ui.render(wb, payload);
  const picker = section.querySelector(".sdk-model-picker").querySelector("select");
  assert.equal(section.querySelectorAll(".sdk-model-row").length, 1);
  assert.equal(section.querySelector(".sdk-model-row").querySelectorAll("select").length, 0);
  picker.value = "wb-b"; await picker.fire("change");
  assert.equal(button(section, "设为默认模型"), undefined); assert.match(section.textContent, /CLI 声明，尚未验证/);
  picker.value = "__custom__"; await picker.fire("change");
  const input = section.querySelector("input"); assert.equal(input.parent.hidden, false);
  input.value = "custom-model"; await input.fire("input");
  assert.equal(ui.calls.length, 0);
  ui.respond(async () => ({ check: { compatible: true }, items: [], default_provider_id: "codex" }));
  await button(section, "检查兼容性").fire("click");
  assert.match(ui.calls[0].url, /workbuddy\/models\/check$/);
  assert.deepEqual(JSON.parse(ui.calls[0].body), { model_id: "custom-model" });
  const reloaded = ui.render(wb, payload);
  assert.equal(reloaded.querySelector(".sdk-model-picker").querySelector("select").value, "__custom__");
  assert.equal(reloaded.querySelector("input").value, "custom-model");
  assert.equal(ui.render(sdk, catalog).querySelector(".sdk-model-picker").querySelector("select").value, "model-a");
});

test("WorkBuddy unauthenticated and absent catalogs never enable a model probe", async () => {
  const ui = await harness();
  const section = ui.render({ ...sdk, provider_id: "workbuddy", installed: false, authenticated: false, model_check_supported: true }, { items: [] });
  assert.equal(button(section, "检查兼容性").disabled, true);
  assert.equal(button(section, "刷新模型目录").disabled, true);
  assert.equal(section.querySelector(".sdk-model-picker").querySelector("select").value, "__custom__");
  await button(section, "检查兼容性").fire("click");
  assert.equal(ui.calls.length, 0);
});
