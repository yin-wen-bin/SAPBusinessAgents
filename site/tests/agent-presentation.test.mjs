import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { deriveAgentPresentation } from "../scripts/agent-presentation.mjs";

const purchaseOrderAgent = () => JSON.parse(readFileSync("../agents/Common/mm-po-gr-status/agent.json", "utf8"));

test("shared presentation extracts nested OData entities once and diagnoses legacy placeholders", () => {
  const agent = purchaseOrderAgent();
  agent.owner = "Unassigned";
  agent.sapModules = ["Common"];
  agent.tables = ["SAP OData entity"];
  for (const step of agent.workflow) delete step.operations;
  const projection = deriveAgentPresentation(agent);
  assert.equal(projection.status, "needs_input");
  assert.deepEqual(projection.odata_objects.map((item) => item.entity), [
    "A_PurchaseOrderScheduleLine",
    "A_PurchaseOrderItem",
    "A_PurchaseOrder",
    "A_MaterialDocumentItem",
    "A_MaterialDocumentHeader",
  ]);
  assert.deepEqual(new Set(projection.issues.map((item) => item.code)), new Set([
    "presentation_business_domain_missing",
    "presentation_sap_modules_missing",
    "presentation_object_placeholder",
    "presentation_workflow_operations_missing",
  ]));
  assert.equal(projection.scope_mode, "odata_only");
});

test("complete metadata becomes ready without changing deterministic execution", () => {
  const agent = purchaseOrderAgent();
  const execution = JSON.stringify(agent.execution);
  agent.owner = "MM-PUR / MM-IM";
  agent.sapModules = ["MM-PUR", "MM-IM"];
  agent.tables = [];
  for (const step of agent.workflow) {
    step.operations = { zh: ["核对输入、处理证据并输出结果。"], en: ["Check inputs, process evidence and produce output."] };
  }
  const projection = deriveAgentPresentation(agent);
  assert.equal(projection.status, "ready");
  assert.deepEqual(projection.issues, []);
  assert.equal(JSON.stringify(agent.execution), execution);
});

test("conditional table Skill exposes a traceable object and its call condition", () => {
  const agent = purchaseOrderAgent();
  agent.owner = "Basis administration";
  agent.sapModules = ["BC"];
  agent.tables = ["TSTC"];
  agent.workflow = [{
    id: "read-table", title: { zh: "读取事务资料", en: "Read transaction data" },
    description: { zh: "满足条件时读取已批准对象。", en: "Read an approved object when required." },
    operations: { zh: ["条件成立时读取TSTC并保留空结果。"], en: ["Read TSTC when the condition is true and preserve empty results."] },
    tools: [{ name: "sap-adt-table-export", kind: "Skill", purpose: { zh: "只读导出", en: "Read-only export" } }],
    executionStepIds: ["read_table"],
  }];
  agent.execution.steps = [{
    id: "read_table", executor: "skill", operation: "execute", skillId: "sap-adt-table-export", readOnly: true,
    when: { source: "{{steps.choose_source.output.use_table}}", equals: true }, inputMapping: { object: "TSTC" },
  }];
  const projection = deriveAgentPresentation(agent);
  assert.equal(projection.status, "ready");
  assert.equal(projection.tables[0].name, "TSTC");
  assert.equal(projection.tables[0].conditional, true);
  assert.equal(projection.tools[0].conditional, true);
});

test("unknown Skill internals are disclosed without guessing or blocking", () => {
  const agent = purchaseOrderAgent();
  agent.owner = "Procurement";
  agent.sapModules = ["MM-PUR"];
  agent.tables = [];
  agent.workflow = [{
    id: "use-skill", title: { zh: "读取证据", en: "Read evidence" },
    description: { zh: "调用只读Skill并保留缺口。", en: "Call a read-only Skill and preserve gaps." },
    operations: { zh: ["读取可用证据，空结果保持为空。"], en: ["Read available evidence and preserve an empty result."] },
    tools: [{ name: "private-read", kind: "Skill", purpose: { zh: "读取证据", en: "Read evidence" } }],
    executionStepIds: ["private_read"],
  }];
  agent.execution.steps = [{ id: "private_read", executor: "skill", operation: "execute", skillId: "private-read", readOnly: true }];
  const projection = deriveAgentPresentation(agent);
  assert.equal(projection.status, "ready");
  assert.equal(projection.tables.length, 0);
  assert.equal(projection.unknown_skill_objects[0].skill_id, "private-read");
  assert.equal(projection.scope_mode, "partially_declared");
});

test("missing and duplicate execution mappings remain blocking structural errors", () => {
  const agent = purchaseOrderAgent();
  agent.workflow[1].executionStepIds = [...agent.workflow[0].executionStepIds];
  const projection = deriveAgentPresentation(agent);
  assert.equal(projection.status, "invalid");
  assert.ok(projection.issues.some((item) => item.code === "presentation_execution_step_mapped_multiple_times"));
  assert.ok(projection.issues.some((item) => item.code === "presentation_execution_step_unmapped"));
});
