import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {createContext, runInContext} from "node:vm";
import ts from "typescript";

const formSource = await readFile(new URL("../src/components/FreeQueryPanel.astro", import.meta.url), "utf8");
const runSource = await readFile(new URL("../src/components/RunConsole.astro", import.meta.url), "utf8");
const formScript = ts.transpileModule(formSource.match(/<script>([\s\S]*?)<\/script>/)[1], {
  compilerOptions: {target: ts.ScriptTarget.ES2022},
}).outputText;
const settle = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {let resolve, reject; const promise = new Promise((a, b) => {resolve = a; reject = b;}); return {promise, resolve, reject};};
class Node {
  hidden = true; disabled = false; textContent = ""; style = {}; attributes = {}; listeners = {};
  dataset = {}; classList = {toggle() {}};
  setAttribute(name, value) {this.attributes[name] = value;}
  addEventListener(name, fn) {this.listeners[name] = fn;}
  focus() {this.focused = true;}
  children = [];
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children = children;}
}

function formHarness(locale) {
  const form = new Node(), error = new Node(), button = new Node(), label = new Node(), status = new Node(), textarea = new Node();
  const fields = {query: "  Offline question  ", receipt_reference: ""};
  form.querySelector = selector => selector.includes("button") ? button : textarea;
  const nodes = {form, ".runtime-form-error": error, "[data-submit-status]": status, "[data-submit-label]": label};
  const panel = {dataset: {locale, apiBase: "http://offline", runPath: "/run/"}, querySelector: selector => nodes[selector] || null};
  let now = 0;
  const timers = new Map(), calls = [], pending = deferred(); let timerId = 0;
  const window = {location: {search: "", href: ""},
    setInterval(fn, ms) {timers.set(++timerId, {fn, ms}); return timerId;}, clearInterval(id) {timers.delete(id);}};
  const context = createContext({window, document: {querySelectorAll: () => [panel]}, URLSearchParams,
    Date: class extends Date {static now() {return now;}},
    FormData: class {get(key) {return fields[key];}},
    fetch: (url, options) => {calls.push({url, ...options}); return pending.promise;},
  });
  runInContext(formScript, context);
  return {form, error, button, label, status, fields, calls, pending, timers, window,
    submit: () => form.listeners.submit({preventDefault() {}}),
    tick(seconds) {now += seconds * 1000; for (const timer of timers.values()) timer.fn();}};
}

for (const locale of ["zh", "en"]) {
  test(`${locale}: immediate submission feedback, elapsed time, one request and original payload`, async () => {
    const ui = formHarness(locale);
    ui.fields.receipt_reference = "private-reference";
    const first = ui.submit();
    assert.equal(ui.button.disabled, true);
    assert.equal(ui.form.attributes["aria-busy"], "true");
    assert.equal(ui.status.hidden, false);
    assert.match(ui.label.textContent, locale === "zh" ? /正在提交/ : /Submitting/);
    ui.tick(12);
    assert.match(ui.status.textContent, /12/);
    assert.match(ui.status.textContent, locale === "zh" ? /勿重复提交/ : /do not submit again/);
    assert.doesNotMatch(ui.status.textContent, /private-reference|Offline question/);
    await ui.submit();
    assert.equal(ui.calls.length, 1);
    assert.deepEqual(JSON.parse(ui.calls[0].body), {mode: "free_query", query: "Offline question", sensitiveInputs: {receipt_reference: "private-reference"}});
    ui.pending.resolve({ok: true, json: async () => ({run_id: "existing-run", session_id: "existing-session"})});
    await first;
    assert.equal(ui.window.location.href, "/run/?run=existing-run&session=existing-session");
    assert.equal(ui.timers.size, 0);
  });
  test(`${locale}: definite admission rejection is visible and can be corrected`, async () => {
    const ui = formHarness(locale), promise = ui.submit();
    ui.pending.resolve({ok: false, status: 409, json: async () => ({detail: {code: "runtime_not_selectable", secret: "private"}})});
    await promise;
    assert.equal(ui.button.disabled, false);
    assert.equal(ui.error.hidden, false);
    assert.match(ui.error.textContent, /runtime_not_selectable/);
    assert.doesNotMatch(ui.error.textContent, /private/);
  });
  for (const failure of ["network", "server", "missing_id"]) test(`${locale}: ${failure} never causes an automatic duplicate`, async () => {
    const ui = formHarness(locale), promise = ui.submit();
    if (failure === "network") ui.pending.reject(new Error("offline"));
    else ui.pending.resolve({ok: failure === "missing_id", status: 500, json: async () => ({})});
    await promise;
    await ui.submit();
    assert.equal(ui.calls.length, 1);
    assert.equal(ui.button.disabled, true);
    assert.match(ui.error.textContent, locale === "zh" ? /历史查询记录/ : /Query history/);
    assert.equal(ui.form.attributes["aria-busy"], "false");
    assert.equal(ui.timers.size, 0);
  });
}

function consoleHarness(locale = "zh") {
  const labels = runSource.slice(runSource.indexOf("const labels ="), runSource.indexOf("\n---", runSource.indexOf("const labels =")));
  const progress = runSource.slice(runSource.indexOf("  const progressPhases ="), runSource.indexOf("  const renderLimits ="));
  const reading = runSource.slice(runSource.indexOf("  const readRun ="), runSource.indexOf("  root?.querySelector(\"[data-reconnect]\")"));
  const completeness = runSource.slice(runSource.indexOf("  const renderCompleteness ="), runSource.indexOf("  const eventDetail ="));
  const nodes = new Map();
  const root = {querySelector(selector) {if (!nodes.has(selector)) nodes.set(selector, new Node()); return nodes.get(selector);}, querySelectorAll() {return [];}};
  const calls = [], events = [], timers = new Map(), documentEvents = {}, windowEvents = {};
  let next = 0, now = Date.parse("2026-10-16T10:00:00Z"), responder = async () => ({run_id: "saved-run", mode: "free_query", status: "planning", started_at: "2026-10-16T09:59:00Z", progress: {phase: "preparing"}});
  class EventSource {
    static CLOSED = 2;
    readyState = 1; listeners = {};
    constructor(url) {this.url = url; events.push(this);}
    addEventListener(name, fn) {this.listeners[name] = fn;}
    close() {this.readyState = 2;}
  }
  const document = {hidden: false, createElement() {return new Node();}, addEventListener(name, fn) {documentEvents[name] = fn;}};
  const window = {
    setInterval(fn, ms) {timers.set(++next, {fn, ms, interval: true}); return next;}, clearInterval(id) {timers.delete(id);},
    setTimeout(fn, ms) {timers.set(++next, {fn, ms}); return next;}, clearTimeout(id) {timers.delete(id);},
    addEventListener(name, fn) {windowEvents[name] = fn;},
  };
  const context = createContext({locale, root, window, document, EventSource, AbortController,
    Date: class extends Date {static now() {return now;}},
    fetch: async (url, options) => {calls.push({url, options}); return {ok: true, json: () => responder()};},
  });
  runInContext(`${labels}
    let runId = 'saved-run', sessionId = null, apiBase = 'http://offline';
    let source, feedbackSource, runtimeActivity, runLoad, runLoadId, progressTimer, refreshTimer, currentRun;
    const terminal = new Set(['completed', 'inconclusive', 'failed', 'cancelled']);
    const friendlyId = value => value; const appendEvent = () => {}; const showAlert = () => {};
    const loadSession = async () => {};
    const renderRun = run => {currentRun = run; renderProgress(run); if (terminal.has(run.status)) stopProgressUpdates();};
    ${progress}
    ${completeness}
    ${reading}
    globalThis.ui = {connect, loadRun, observeRuntimeEvent, startProgressUpdates, executionInterruption, renderCompleteness,
      render: renderRun, lastActivity: () => runtimeActivity};`, context);
  return {ui: context.ui, root, nodes, calls, events, timers,
    respond(fn) {responder = fn;},
    tick(seconds) {now += seconds * 1000; for (const timer of [...timers.values()]) if (timer.interval) timer.fn();},
    async poll() {const entry = [...timers].find(([, timer]) => timer.ms === 5000); assert.ok(entry); timers.delete(entry[0]); await entry[1].fn();},
    async visible(value) {document.hidden = !value; documentEvents.visibilitychange(); await settle();},
    hidePage() {windowEvents.pagehide();},
  };
}

for (const locale of ["zh", "en"]) test(`${locale}: subscribe to native milestones, keep wait honest and tick without model/SAP calls`, async () => {
  const ui = consoleHarness(locale);
  await ui.ui.loadRun();
  ui.ui.connect();
  const stream = ui.events[0];
  for (const name of ["agent_runtime_turn_started", "agent_runtime_connected", "agent_runtime_query_submitted", "agent_runtime_activity", "agent_runtime_result_received", "agent_runtime_sdk_closing", "agent_runtime_sdk_closed", "workbuddy_native_wait", "workbuddy_native_failure", "workbuddy_formatter_progress"]) assert.equal(typeof stream.listeners[name], "function", name);
  const initial = ui.root.querySelector("[data-progress-message]").textContent;
  ui.tick(7);
  assert.notEqual(ui.root.querySelector("[data-progress-message]").textContent, initial);
  stream.listeners.agent_runtime_activity({data: JSON.stringify({sequence: 2, data: {assistant_message_count: 0, content: "private-reasoning"}})});
  const activity = ui.root.querySelector("[data-runtime-activity]");
  assert.match(activity.textContent, locale === "zh" ? /等待模型/ : /Waiting for the model/);
  assert.doesNotMatch(activity.textContent, /private-reasoning/);
  // Replayed/older events cannot replace a later observed state.
  ui.ui.observeRuntimeEvent("agent_runtime_connected", {sequence: 1});
  assert.match(activity.textContent, locale === "zh" ? /等待模型/ : /Waiting for the model/);
  await ui.poll();
  assert.ok(ui.calls.every(call => call.url.endsWith("/api/runs/saved-run") && !call.options.method));
  assert.equal(ui.calls.length, 2);
  ui.ui.render({run_id: "saved-run", mode: "free_query", status: "completed", progress: {state: "completed"}});
  assert.equal(activity.hidden, true);
  assert.equal(ui.timers.size, 0);
});

function timedOutRun() {
  return {run_id: "saved-run", mode: "free_query", status: "inconclusive", runtime: {provider_id: "workbuddy"},
    progress: {state: "inconclusive", phase: "preparing_result", elapsed_seconds: 1800, hard_limit_seconds: 1800},
    result: {tool_calls: [], evidence: [], harness: {tool_call_count: 0},
      completeness: {missing_evidence: ["harness_deadline_exceeded"]},
      summary: {zh: "已保留取得的证据。", en: "Collected evidence was preserved."}}};
}

for (const locale of ["zh", "en"]) {
  test(`${locale}: saved WorkBuddy timeout with no reads is not successful evidence collection`, () => {
    const ui = consoleHarness(locale), run = timedOutRun();
    const original = JSON.stringify(run);
    const description = ui.ui.executionInterruption(run);
    assert.equal(description.noReads, true);
    assert.match(description.headline, locale === "zh" ? /模型响应超时，未开始 SAP 取证/ : /Model response timed out before SAP evidence collection/);
    assert.match(description.overview, locale === "zh" ? /不能据此判定 SAP 中没有数据/ : /does not establish that SAP has no data/);
    ui.ui.render(run);
    assert.match(ui.root.querySelector("[data-progress-message]").textContent, /1800/);
    assert.doesNotMatch(ui.root.querySelector("[data-progress-message]").textContent, /查询完成|Query completed/);
    assert.match(ui.root.querySelector("[data-progress-indicator]").className, /is-stopped/);
    assert.notEqual(ui.root.querySelector("[data-progress-bar]").style.width, "100%");
    ui.ui.renderCompleteness(run);
    assert.equal(ui.root.querySelector("[data-completeness]").dataset.complete, "unknown");
    assert.equal(ui.root.querySelector("[data-completeness]").children[0].textContent, description.headline);
    assert.equal(JSON.stringify(run), original, "do not rewrite historical result or evidence");
    assert.equal(ui.calls.length, 0, "rendering cannot retry a query");
  });

  test(`${locale}: partial evidence and unknown read counts receive conservative timeout wording`, () => {
    const ui = consoleHarness(locale);
    for (const change of [
      run => {run.result.evidence = [{evidence_ref: "existing"}]; run.result.harness.tool_call_count = 1;},
      run => {delete run.result.harness;},
      run => {delete run.result.tool_calls;},
      run => {run.result.tool_calls = [{tool: "sap_schema_get"}]; run.result.harness.tool_call_count = 1;},
    ]) {
      const run = timedOutRun(); change(run);
      const description = ui.ui.executionInterruption(run);
      assert.equal(description.noReads, false);
      assert.match(description.headline, locale === "zh" ? /查询超时，尚未形成完整结论/ : /Query timed out without a complete conclusion/);
      assert.doesNotMatch(description.headline, /未开始 SAP|before SAP/);
    }
  });

  test(`${locale}: Codex, fixed Agents, ordinary evidence gaps and active runs retain their previous path`, () => {
    const ui = consoleHarness(locale);
    for (const change of [
      run => {run.runtime.provider_id = "codex";},
      run => {run.mode = "agent";},
      run => {run.status = "planning";},
      run => {run.status = "completed";},
      run => {run.result.completeness.missing_evidence = ["harness_structured_terminal_missing"];},
      run => {delete run.runtime;},
    ]) {
      const run = timedOutRun(); change(run);
      assert.equal(ui.ui.executionInterruption(run), null);
    }
    const run = timedOutRun(); run.runtime.provider_id = "codex";
    ui.ui.render(run);
    assert.match(ui.root.querySelector("[data-progress-indicator]").className, /is-complete/);
    assert.equal(ui.root.querySelector("[data-progress-bar]").style.width, "100%");
  });
}

test("run status reads are single-flight, hidden-page polling pauses, resuming never submits a query", async () => {
  const ui = consoleHarness();
  const wait = deferred(); ui.respond(() => wait.promise);
  const a = ui.ui.loadRun(), b = ui.ui.loadRun();
  assert.equal(a, b); assert.equal(ui.calls.length, 1);
  wait.resolve({run_id: "saved-run", mode: "free_query", status: "planning", progress: {phase: "preparing"}});
  await a; ui.ui.connect();
  await ui.visible(false);
  assert.equal(ui.timers.size, 0);
  await ui.visible(true);
  assert.ok([...ui.timers.values()].some(timer => timer.ms === 5000));
  assert.ok(ui.calls.every(call => !call.options.method));
  ui.hidePage();
  assert.equal(ui.timers.size, 0);
  assert.equal(ui.events[0].readyState, 2);
});
