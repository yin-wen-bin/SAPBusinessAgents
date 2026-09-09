import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import * as draftHelpers from "../src/lib/agentDraft.ts";
import { publicValues, validateDraftInput, changedDefinition, clearDiscoveredInput, discoveryFingerprint, diffBusinessLabel, draftStepNames, draftTerminal, localText, presentationCell, publicInput, publicBranchRequirements, restoreDiscoveredInput, retainCompatibleInput, technicalIdentity, technicalIdError, feedbackTiming, feedbackDuration, feedbackFailureText, canRetryFeedback, prepareFeedbackRequest } from "../src/lib/agentDraft.ts";

// Render the actual TSX without building the catalog or starting a browser/API.
const require = createRequire(import.meta.url);
async function component(name, dependencies = {}) {
  const source = await readFile(new URL(`../src/components/${name}.tsx`, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } });
  const exports = {};
  new Function("require", "exports", compiled.outputText)((id) => id === "../lib/agentDraft" ? draftHelpers : id.endsWith(".css") ? {} : dependencies[id] || require(id), exports);
  return exports.default;
}

const title = (zh, en) => ({ zh, en });
const schema = {
  type: "object", required: ["company", "quantity", "date_from", "date_to", "description"],
  properties: {
    company: { type: "string", title: title("公司代码", "Company code"), pattern: "^[0-9]{4}$" },
    quantity: { type: "number", title: title("数量", "Quantity"), exclusiveMinimum: 0 },
    date_from: { type: "string", title: title("开始日期", "Start date"), format: "date" },
    date_to: { type: "string", title: title("结束日期", "End date"), format: "date" },
    description: { type: "string", "x-sapba-server-default": "business_date" },
    reference: { type: "string", "x-sapba-sensitive": true, default: "must-not-leak" },
    internal: { type: "string", "x-sapba-internal": true, default: "private" },
    workflow: { type: "object", "x-sapba-workflow-only": true, properties: { customer: { type: "string" } } },
    materials: { type: "array", items: { type: "string", pattern: "^[A-Z0-9]+$" }, minItems: 1, maxItems: 50, uniqueItems: true },
    demands: { type: "array", items: { type: "object", required: ["quantity"], properties: { quantity: { type: "number", exclusiveMinimum: 0 }, secret: { type: "string", "x-sapba-sensitive": true }, hidden: { type: "string", "x-sapba-internal": true } } } },
  }, dateRangePairs: [{ from: "date_from", to: "date_to", maxDays: 31 }],
};
const valid = { company: "1710", quantity: 100000, date_from: "2026-09-01", date_to: "2026-09-09" };

test("only three management stages; localizations never fall back to the wrong language", () => {
  assert.deepEqual(draftStepNames, ["compose", "review", "publish"]);
  assert.equal(localText({ en: "English only" }, "zh"), "");
  assert.equal(draftTerminal.has("ready"), true);
  assert.equal(draftTerminal.has("needs_input"), true);
  assert.equal(draftTerminal.has("interrupted"), true);
  assert.equal(draftTerminal.has("running"), false);
});

test("public defaults and recursive discovery projections exclude secrets and hidden subtrees", () => {
  const source = { ...valid, reference: "secret", internal: "private", workflow: { customer: "private" }, demands: [{ quantity: 3, secret: "never", hidden: "private" }], forged: "outside schema" };
  const projected = publicValues(schema, source, true);
  assert.deepEqual(projected, { ...valid, demands: [{ quantity: 3 }] });
  assert.equal(JSON.stringify(source).includes("never"), true, "source is not mutated");
  assert.deepEqual(publicValues(schema, {}, true), {});
  assert.equal(publicInput(schema.properties.workflow), false);
});

test("arrays keep duplicate entries so validation can reject them rather than silently dropping data", () => {
  assert.deepEqual(publicValues(schema, { materials: "TG10\nTG10; SG21" }).materials, ["TG10", "TG10", "SG21"]);
  assert.ok(validateDraftInput(schema, { ...valid, materials: "TG10\nTG10" }, {}, "zh").materials);
  assert.equal(Object.keys(validateDraftInput(schema, { ...valid, materials: Array.from({ length: 50 }, (_, i) => `M${i}`) }, {}, "en")).length, 0);
  assert.ok(validateDraftInput(schema, { ...valid, materials: Array.from({ length: 51 }, (_, i) => `M${i}`) }, {}, "zh").materials);
});

test("typed, nested and date-range validation produces field errors in the current locale", () => {
  assert.deepEqual(validateDraftInput(schema, valid, {}, "zh"), {});
  const errors = validateDraftInput(schema, { company: "bad", quantity: 0, date_from: "2026-02-30", date_to: "2026-01-01", demands: [{ quantity: -1 }] }, {}, "zh");
  assert.ok(errors.company.includes("公司代码"));
  assert.ok(errors.quantity.includes("必须大于"));
  assert.ok(errors.date_from);
  assert.ok(errors.date_to);
  assert.ok(errors["demands.0.quantity"]);
  assert.ok(validateDraftInput(schema, { ...valid, date_to: "2026-12-31" }, {}, "en").date_to.includes("date"));
});

test("sample confirmation is bound to revision and all input values", () => {
  const first = discoveryFingerprint(2, { a: [1, 2], b: "1710" });
  assert.equal(first, discoveryFingerprint(2, { b: "1710", a: [1, 2] }));
  assert.notEqual(first, discoveryFingerprint(3, { b: "1710", a: [1, 2] }));
  assert.notEqual(first, discoveryFingerprint(2, { b: "1720", a: [1, 2] }));
});

test("restored discovery opens review and requires fresh confirmation without overwriting user scope", () => {
  const sample = { revision: 2, status: "ready", suggested_inputs: { company: "1710", quantity: 100000, reference: "private" }, field_sources: { quantity: { evidence_ref: "evidence" }, reference: { evidence_ref: "private" } } };
  const restored = restoreDiscoveredInput(schema, 2, { company: "1710", quantity: "" }, sample);
  assert.equal(restored.autoDiscover, true);
  assert.equal(restored.confirmedSample, "");
  assert.deepEqual(restored.input, { company: "1710", quantity: 100000 });
  assert.deepEqual(restored.autoFilled, { quantity: 100000 });
  assert.notEqual(restored.confirmedSample, restored.fingerprint, "reload cannot accept a candidate automatically");
  assert.equal(restoreDiscoveredInput(schema, 3, {}, sample), null, "old revisions cannot restore a candidate");
  assert.deepEqual(clearDiscoveredInput(restored.input, restored.autoFilled), { company: "1710" });
  assert.deepEqual(clearDiscoveredInput({ ...restored.input, quantity: 42 }, restored.autoFilled), { company: "1710", quantity: 42 }, "manual corrections are retained");
  const differentScope = restoreDiscoveredInput(schema, 2, { company: "1720" }, sample);
  assert.deepEqual(differentScope.input, { company: "1720" }, "a previous sample is never mixed into a changed scope");
  assert.equal(differentScope.scopeChanged, true);
  assert.equal(differentScope.fingerprint, "");
});

test("unsaved definitions include README and rule removal, but do not include trial inputs", () => {
  const draft = { package: { manifest: { title: { zh: "原名称" } }, readme: "readme", rules: "rule" } };
  assert.equal(changedDefinition(draft, JSON.stringify(draft.package.manifest, null, 2), "readme", "rule"), false);
  assert.equal(changedDefinition(draft, JSON.stringify(draft.package.manifest), "readme", ""), true);
  assert.equal(changedDefinition(draft, "invalid", "readme", "rule"), true);
});

test("revision changes prune removed and incompatible fields without retaining secrets", () => {
  const next = { ...schema, properties: { company: schema.properties.company, quantity: { type: "string" }, reference: schema.properties.reference } };
  assert.deepEqual(retainCompatibleInput(schema, next, { ...valid, reference: "secret" }), { company: "1710" });
  assert.equal(diffBusinessLabel({ path: "/manifest/title/zh", group: "basic" }, {}, "zh"), "名称（中文）");
  assert.equal(diffBusinessLabel({ path: "/manifest/execution/inputSchema/properties/company/pattern", group: "inputs_outputs" }, { execution: { inputSchema: schema } }, "zh"), "公司代码");
});

test("actual AP public form requires company and supplier from the reachable direct oneOf branch", async () => {
  const manifest = JSON.parse(await readFile(new URL("../../agents/FI/ap-payment/agent.json", import.meta.url), "utf8"));
  const definition = manifest.execution.inputSchema;
  assert.deepEqual(publicBranchRequirements(definition, {}).required.sort(), ["as_of", "company_code", "supplier"]);
  const empty = validateDraftInput(definition, {}, {}, "zh");
  assert.ok(empty.company_code.includes("公司代码"));
  assert.ok(empty.supplier.includes("供应商"));
  assert.ok(empty.as_of.includes("查询基准日"));
  assert.equal(empty.ap_payment_scopes, undefined);
  assert.equal(empty._schema, undefined);
  assert.deepEqual(validateDraftInput(definition, { company_code: "1710", supplier: "TEST", as_of: "2026-09-09" }, {}, "en"), {});
});

test("ambiguous public oneOf alternatives are not picked arbitrarily", () => {
  const definition = { type: "object", properties: { order: { type: "string" }, delivery: { type: "string" } }, oneOf: [{ required: ["order"] }, { required: ["delivery"] }] };
  assert.equal(publicBranchRequirements(definition, {}).ambiguous, true);
  assert.deepEqual(publicBranchRequirements(definition, {}).required, []);
  assert.ok(validateDraftInput(definition, {}, {}, "zh")._schema);
  assert.equal(publicBranchRequirements(definition, { order: "1" }).ambiguous, false);
  assert.ok(validateDraftInput(definition, { order: "1", delivery: "2" }, {}, "en")._schema);
});

test("public presentation rows read localized cells from the validated values array", () => {
  const row = { values: [{ zh: "待处理", en: "Attention" }, { zh: "100000.00 USD", en: "100000.00 USD" }], evidence_refs: ["public-evidence"] };
  assert.equal(localText(presentationCell(row, 0, "status"), "zh"), "待处理");
  assert.equal(localText(presentationCell(row, 1, "amount"), "en"), "100000.00 USD");
});

test("workbench uses confirmed live discovery, protected inputs and formal publishability", async () => {
  const source = await readFile(new URL("../src/components/AgentDraftWorkspace.tsx", import.meta.url), "utf8");
  assert.match(source, /您的修改意见是？/);
  assert.ok(source.indexOf('draft-conversation') > source.indexOf('draft-advanced'));
  assert.match(source, /autoDiscover: false/);
  assert.match(source, /sensitiveInputs: secrets/);
  assert.match(source, /publishability\?\.can_publish === true/);
  assert.match(source, /\/diff\?fromRevision=/);
  assert.match(source, /restoreDiscoveredInput/);
  assert.match(source, /requiresSampleConfirmation && !sampleConfirmed/);
  assert.match(source, /样本来源详情/);
  assert.match(source, /source\.source_field/);
  assert.match(source, /source\.evidence_ref/);
  assert.match(source, /acceptance\.source_version \|\| acceptance\.reused_from_version/);
  assert.match(source, /change\[`\$\{side\}_exists`\] === false/);
  assert.match(source, /gpt-5\.6-sol/);
  assert.doesNotMatch(source, /gpt-6-astra|localStorage|sessionStorage|dangerouslySetInnerHTML/);
  assert.doesNotMatch(source, /trial\?\.verdict === "PASS"/);
  assert.match(source, /\["before", "after"\]/);
  assert.match(source, /setTrial\(value\.trial \|\| null\)/);
  assert.doesNotMatch(source, /value\.trial\?\.run_id \|\| trialRunId/);
});

test("technical identity trusts explicit server classification and never infers upgrade from source version", () => {
  assert.equal(technicalIdentity({ agent_id: "new-clone", source_version: "1.0.0", technical_identity: { kind: "new_agent", confirmed: false, can_rename: true } }).can_rename, true);
  assert.equal(technicalIdentity({ technical_identity: { kind: "version_upgrade", confirmed: true, can_rename: true, locked: false } }).can_rename, false);
  assert.equal(technicalIdentity({ technical_identity: { kind: "unknown", confirmed: true, can_rename: true } }).confirmed, false);
  assert.equal(technicalIdentity({ agent_id: "legacy" }).kind, "unknown");
  assert.equal(technicalIdentity({ status: "published", technical_identity: { kind: "new_agent", confirmed: true, can_rename: true } }).locked, true);
});

test("new technical IDs use lowercase kebab-case length bounds and reject Windows reserved names", () => {
  for (const id of ["free-query-85ebeb30", "new-agent", "123", "a".repeat(80)]) assert.equal(technicalIdError(id, "en"), "");
  for (const id of ["ab", "A-new-agent", "new--agent", "agent-", "-agent", "new_agent", " new-agent", "新agent", "a".repeat(81), "con", "prn", "aux", "nul", "com1", "lpt9"]) assert.ok(technicalIdError(id, "zh"), id);
});

test("feedback timers resume from server execution metadata and never rewrite historical limits", () => {
  const execution = { started_at: "2026-09-09T07:20:20.000Z", deadline_at: "2026-09-09T08:20:20.000Z", timeout_seconds: 3600, elapsed_seconds: 12 };
  const active = feedbackTiming({ status: "running", decision: { execution } }, Date.parse("2026-09-09T07:22:20.000Z"));
  assert.equal(active.elapsed_seconds, 120);
  assert.equal(active.timeout_seconds, 3600);
  assert.equal(active.deadline_reached, false);
  assert.equal(feedbackTiming({ status: "running", decision: { execution } }, Date.parse("2026-09-09T08:21:20.000Z")).deadline_reached, true);
  const finished = feedbackTiming({ status: "failed", completed_at: "2026-09-09T07:23:20.113Z", decision: { execution: { ...execution, timeout_seconds: 180, elapsed_seconds: 180.113 } } }, Date.parse("2026-10-01T00:00:00Z"));
  assert.equal(finished.elapsed_seconds, 180.113);
  assert.equal(finished.timeout_seconds, 180);
  assert.equal(finished.deadline_reached, false);
  assert.equal(feedbackDuration(finished.timeout_seconds, "en"), "3m 0s");
  assert.equal(feedbackDuration(3600, "zh"), "1小时0分0秒");
  assert.equal(feedbackTiming({ status: "failed", created_at: execution.started_at, completed_at: execution.deadline_at }).timeout_seconds, null);
  assert.equal(feedbackTiming({ status: "failed" }).elapsed_seconds, null);
  assert.equal(feedbackTiming({ status: "queued", decision: { execution: { timeout_seconds: 3600 } } }).elapsed_seconds, null, "queued time is not fabricated as execution time");
  assert.equal(feedbackTiming({ status: "running", decision: { execution: { started_at: "bad", timeout_seconds: -1, elapsed_seconds: Infinity } } }).timeout_seconds, null);
});

test("feedback failure messages are bilingual and never echo arbitrary Runtime errors", () => {
  assert.match(feedbackFailureText("agent_feedback_timeout", "zh"), /时限/);
  assert.match(feedbackFailureText("agent_feedback_cleanup_failed", "en"), /Restart the API/);
  assert.match(feedbackFailureText("runtime_agent_feedback_connection_timeout", "en"), /does not mean/);
  assert.doesNotMatch(feedbackFailureText("SECRET server traceback https://private.invalid", "en"), /SECRET|traceback|private/);
  assert.match(feedbackFailureText("agent_feedback_cancelled", "en"), /cancelled/);
});

test("retry requires a terminal failed feedback record with a recorded request", () => {
  const turn = { turn: 2, kind: "feedback", status: "failed", user_message: "Please refine the output" };
  assert.equal(canRetryFeedback(turn), true);
  assert.equal(canRetryFeedback({ ...turn, status: "cancelled" }), true);
  assert.equal(canRetryFeedback({ ...turn, status: "running" }), false);
  assert.equal(canRetryFeedback({ ...turn, status: "completed" }), false);
  assert.equal(canRetryFeedback({ ...turn, user_message: "" }), false);
  assert.equal(canRetryFeedback({ ...turn, kind: "initial" }), false);
});

test("an uncertain send retains its exact request while a confirmed retry gets a new identity", () => {
  let count = 0;
  const id = () => `request-${++count}`;
  const next = { feedback: "Original request", locale: "zh", baseTurn: 2, baseRevision: 1, retryOfTurn: 2 };
  const first = prepareFeedbackRequest(null, next, id);
  assert.equal(first.requestId, "request-1");
  const retransmit = prepareFeedbackRequest(first, { ...next, baseTurn: 3, baseRevision: 2 }, id);
  assert.equal(retransmit, first, "polling after an uncertain response must not turn retransmission into another request");
  assert.equal(retransmit.baseRevision, 1);
  const retry = prepareFeedbackRequest(null, { ...next, baseTurn: 3, baseRevision: 2 }, id);
  assert.equal(retry.requestId, "request-2");
  assert.equal(retry.baseRevision, 2);
  assert.equal(retry.retryOfTurn, 2);
  assert.notEqual(prepareFeedbackRequest(first, { ...next, feedback: "Edited request" }, id).requestId, first.requestId);
});

test("configuration resolution failures are distinct from unset model settings in both languages", () => {
  const zh = feedbackFailureText("agent_runtime_snapshot_failed", "zh");
  const en = feedbackFailureText("agent_runtime_snapshot_failed", "en");
  assert.match(zh, /不是推理强度未设置/);
  assert.match(en, /not an unset reasoning effort/);
  assert.notEqual(zh, feedbackFailureText("agent_runtime_binding_missing", "zh"));
  assert.notEqual(en, feedbackFailureText("agent_runtime_binding_missing", "en"));
  assert.doesNotMatch(zh + en, /AttributeError|PluginManager|PRIVATE/);
});

test("input overview derives names from Schema and keeps requirement badges separate", async () => {
  const definition = { type: "object", required: ["date"], properties: {
    date: { type: "string", format: "date", title: title("交货日期", "Delivery date") },
    plant: { type: "string", title: title("工厂", "Plant") },
    hidden: { type: "string", "x-sapba-internal": true },
    workflow: { type: "object", "x-sapba-workflow-only": true },
  } };
  const original = JSON.stringify(definition);
  assert.deepEqual(draftHelpers.draftInputLabels(definition, "zh"), [
    { key: "date", label: "交货日期", requirement: "必填" }, { key: "plant", label: "工厂", requirement: "可选" },
  ]);
  const Inputs = await component("AgentDraftInputs");
  const props = { schema: definition, values: {}, onChange: () => {}, secrets: {}, onSecrets: () => {} };
  const zh = renderToStaticMarkup(createElement(Inputs, { ...props, locale: "zh" }));
  const en = renderToStaticMarkup(createElement(Inputs, { ...props, locale: "en" }));
  assert.match(zh, /交货日期<span class="draft-field-requirement">必填<\/span>/);
  assert.match(zh, /工厂<span class="draft-field-requirement">可选<\/span>/);
  assert.match(en, /Delivery date<span class="draft-field-requirement">Required<\/span>/);
  assert.match(en, /Plant<span class="draft-field-requirement">Optional<\/span>/);
  assert.match(zh, /id="draft-input-date"[^>]*required=""/);
  assert.doesNotMatch(zh, /id="draft-input-plant"[^>]*required=|draft-input-hidden|draft-input-workflow/);
  assert.equal(JSON.stringify(definition), original);

  const Workspace = await component("AgentDraftWorkspace", { "./AgentDraftInputs": () => null, "./AgentDraftResult": () => null, "./AgentDraftConversation": () => null });
  const draft = { draft_id: "test", agent_id: "test-agent", revision: 1, status: "draft", package: { manifest: { title: title("示例", "Example"), version: "0.1.0", inputs: { zh: ["错误旧展示"], en: ["Stale display"] }, execution: { inputSchema: definition } } } };
  for (const locale of ["zh", "en"]) {
    const html = renderToStaticMarkup(createElement(Workspace, { initialDraft: draft, locale, apiBase: "", runPath: "/runs", onBack: () => {}, onPublished: () => {} }));
    const overview = html.slice(html.indexOf(locale === "zh" ? "输入与输出" : "Inputs and outputs"), html.indexOf(locale === "zh" ? "高级编辑" : "Advanced editing"));
    assert.match(overview, locale === "zh" ? /交货日期<span[^>]*>必填/ : /Delivery date<span[^>]*>Required/);
    assert.doesNotMatch(overview.split("</section>")[0], /错误旧展示|Stale display/);
  }
});

test("requirement indicators respect server defaults, conditional branches and object-array children", async () => {
  const definition = { type: "object", required: ["date", "rows"], properties: {
    date: { type: "string", "x-sapba-server-default": "business_date", title: title("日期", "Date") },
    rows: { type: "array", title: title("需求行", "Rows"), items: { type: "object", required: ["quantity"], properties: { quantity: { type: "number", title: title("数量", "Quantity") }, note: { type: "string", title: title("备注", "Note") } } } },
  } };
  const Inputs = await component("AgentDraftInputs");
  for (const locale of ["zh", "en"]) {
    const html = renderToStaticMarkup(createElement(Inputs, { schema: definition, values: { rows: [{}] }, onChange: () => {}, secrets: {}, onSecrets: () => {}, locale }));
    assert.match(html, locale === "zh" ? /可留空 · 服务端默认/ : /May be empty · server default/);
    assert.match(html, /id="draft-input-rows.0.quantity"[^>]*required=""/);
    assert.doesNotMatch(html, /id="draft-input-date"[^>]*required=|id="draft-input-rows.0.note"[^>]*required=/);
    assert.match(html, locale === "zh" ? /<legend>需求行<span[^>]*>必填/ : /<legend>Rows<span[^>]*>Required/);
  }
  const conditional = { type: "object", properties: { order: { type: "string" }, delivery: { type: "string" } }, oneOf: [{ required: ["order"] }, { required: ["delivery"] }] };
  assert.equal(draftHelpers.inputRequirement(conditional, "order", {}, "zh"), "按条件必填");
  assert.equal(draftHelpers.inputRequirement(conditional, "order", { order: "123" }, "en"), "Required");
});

test("conversation explains definition failures without leaking raw validation values", async () => {
  const Conversation = await component("AgentDraftConversation");
  const turn = { turn: 9, kind: "feedback", status: "failed", user_message: "Revise inputs", decision: {
    error_code: "agent_definition_invalid", validation_issues: [
      { code: "input_title_missing", path: "/manifest/execution/inputSchema/properties/plant/title", message: "PRIVATE-SDK-RESPONSE", instance: "PRIVATE-DEFAULT" },
      { code: "unknown-private-error", path: "/manifest/<PRIVATE-PATH>" },
    ],
  } };
  for (const locale of ["zh", "en"]) {
    const html = renderToStaticMarkup(createElement(Conversation, { turns: [turn], locale, onRetry: () => {} }));
    assert.match(html, locale === "zh" ? /定义校验未通过/ : /Definition validation failed/);
    assert.match(html, locale === "zh" ? /缺少中文或英文名称/ : /missing a Chinese or English name/);
    assert.match(html, /\/manifest\/execution\/inputSchema\/properties\/plant\/title/);
    assert.match(html, locale === "zh" ? /重试这条意见/ : /Retry this feedback/);
    assert.doesNotMatch(html, /PRIVATE|unknown-private-error|AttributeError/);
  }
  assert.deepEqual(draftHelpers.feedbackValidationIssues({ decision: {} }, "zh"), [], "do not invent details for old failures");
  assert.deepEqual(draftHelpers.feedbackValidationIssues({ decision: { validation_issues: {} } }, "en"), []);
});

test("actual conversation renders recorded historical timing, safe failures and disabled retry", async () => {
  const Conversation = await component("AgentDraftConversation");
  const turns = [
    { turn: 1, kind: "initial", status: "completed" },
    { turn: 2, kind: "feedback", status: "failed", user_message: "Please revise", decision: { error_code: "agent_feedback_timeout", execution: { timeout_seconds: 180, elapsed_seconds: 180.113 }, runtime_snapshot: { model: "gpt-5.6-sol", reasoning_effort: "xhigh" } } },
    { turn: 3, kind: "feedback", status: "failed", user_message: "Older request", decision: {} },
  ];
  const html = renderToStaticMarkup(createElement(Conversation, { turns, locale: "en", onRetry: () => {}, retryDisabled: true }));
  assert.match(html, /Conversation 1/);
  assert.match(html, /Turn time limit.*3m 0s/);
  assert.match(html, /Reasoning effort.*xhigh/);
  assert.match(html, /Turn time limit.*Not recorded/);
  assert.match(html, /disabled=""[^>]*>Retry this feedback/);
  assert.doesNotMatch(html, /3600|1h 0m 0s|Conversation 3/);
  const chinese = renderToStaticMarkup(createElement(Conversation, { turns, locale: "zh", onRetry: () => {} }));
  assert.match(chinese, /本轮时限.*3分0秒/);
  assert.match(chinese, /超高 \(xhigh\)/);
  assert.match(chinese, /未应用修改/);
  assert.match(chinese, /未记录/);
});

test("actual workbench gates validation and publication but keeps unconfirmed definition editing available", async () => {
  const Conversation = await component("AgentDraftConversation");
  const Workspace = await component("AgentDraftWorkspace", { "./AgentDraftInputs": () => null, "./AgentDraftResult": () => null, "./AgentDraftConversation": Conversation });
  const draft = { draft_id: "draft-test", agent_id: "sample-test", revision: 1, status: "draft", target_version: "0.1.0", package: { manifest: { module: "Common", title: { en: "Sample" }, version: "0.1.0", execution: { inputSchema: { type: "object", properties: {} } } } }, conversation: [], publishability: { can_publish: true }, acceptance: { verdict: "PASS" }, technical_identity: { kind: "new_agent", confirmed: false, locked: false, can_rename: true } };
  const props = { initialDraft: draft, locale: "en", apiBase: "", runPath: "/runs", onBack: () => {}, onPublished: () => {} };
  const html = renderToStaticMarkup(createElement(Workspace, props));
  assert.match(html, /Check availability/);
  assert.match(html, /disabled=""[^>]*>Run read-only trial/);
  assert.doesNotMatch(html, /disabled=""[^>]*>Check definition/);
  assert.doesNotMatch(html.match(/<textarea[^>]*id="draft-feedback"[^>]*>/)?.[0] || "", /disabled/);
  const publish = renderToStaticMarkup(createElement(Workspace, { ...props, initialStep: "publish" }));
  assert.match(publish, /disabled=""[^>]*>Publish inactive/);
  assert.match(publish, /Return to confirm technical ID/);
  const upgrade = renderToStaticMarkup(createElement(Workspace, { ...props, initialDraft: { ...draft, technical_identity: { kind: "version_upgrade", confirmed: true, locked: true, can_rename: false } } }));
  assert.match(upgrade, /version upgrade/);
  assert.doesNotMatch(upgrade, /id="draft-technical-id"/);
  const cleanup = renderToStaticMarkup(createElement(Workspace, { ...props, initialDraft: { ...draft, active_operation: { status: "cancelling", kind: "feedback" }, conversation: [{ turn: 2, kind: "feedback", status: "failed", user_message: "Retry me", decision: { error_code: "agent_feedback_cleanup_failed" } }] } }));
  assert.match(cleanup, /id="draft-technical-id"[^>]*disabled=""/);
  assert.match(cleanup, /id="draft-feedback"[^>]*disabled=""/);
  assert.match(cleanup, /disabled=""[^>]*>Retry this feedback/);
  assert.match(cleanup, /Restart the API service/);
  assert.match(cleanup, /disabled=""[^>]*>Cancel task/);
});
