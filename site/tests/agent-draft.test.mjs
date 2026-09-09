import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { publicValues, validateDraftInput, changedDefinition, clearDiscoveredInput, discoveryFingerprint, diffBusinessLabel, draftStepNames, draftTerminal, localText, presentationCell, publicInput, publicBranchRequirements, restoreDiscoveredInput, retainCompatibleInput } from "../src/lib/agentDraft.ts";

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
