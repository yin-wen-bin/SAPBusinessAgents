import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { loadAgentCatalog, validateAgent } from "../scripts/generate-agent-catalog.mjs";

test("catalog discovers twenty-one valid agents with step-level tools", () => {
  const records = loadAgentCatalog(path.resolve("..", "agents"));
  assert.equal(records.length, 21);
  assert.deepEqual(
    records.map((agent) => `${agent.module}/${agent.slug}`),
    [
      "FI/ap-payment",
      "FI/ar-collection",
      "FI/gr-ir-clearing",
      "FI/month-end-closing",
      "SD/billing-block-diagnosis",
      "SD/billing-completeness-check",
      "SD/billing-dispute-classification",
      "SD/billing-output-monitor",
      "SD/delivered-not-billed",
      "SD/delivery-delay-prediction",
      "SD/due-delivery-prioritization",
      "SD/order-to-cash-anomaly-monitor",
      "SD/order-to-cash-status",
      "SD/returns-credit-anomaly",
      "SD/shortage-allocation-advisor",
      "MM/procure-to-pay-status",
      "PP/demand-forecast-planning",
      "PP/mrp-exception-analysis",
      "PP/production-order-monitoring",
      "PP/production-scheduling-capacity",
      "PP/production-variance-analysis",
    ],
  );
  for (const agent of records) {
    assert.ok(agent.workflow.length > 0);
    assert.ok(agent.workflow.every((step) => step.tools.length > 0));
  }

  const sdAgents = records.filter((agent) => agent.module === "SD");
  assert.equal(sdAgents.length, 11);
  assert.ok(sdAgents.every((agent) => agent.workflow.length === 8));
  assert.ok(sdAgents.every((agent) => agent.workflow.some((step) => step.sapScope)));
  assert.ok(sdAgents.every((agent) => agent.guardrails.zh.some((item) => item.includes("只读"))));
  const closing = records.find((agent) => agent.slug === "month-end-closing");
  assert.ok(closing.workflow.every((step) => step.sapScope));
  for (const [scopeField, agentField] of [["modules", "sapModules"], ["transactions", "transactions"], ["tables", "tables"]]) {
    const coveredValues = new Set(closing.workflow.flatMap((step) => step.sapScope[scopeField]));
    assert.deepEqual(coveredValues, new Set(closing[agentField]));
  }

  const p2p = records.find((agent) => agent.slug === "procure-to-pay-status");
  assert.equal(p2p.workflow.length, 8);
  assert.ok(p2p.workflow.every((step) => step.operations.zh.length > 0));
  assert.ok(p2p.workflow.every((step) => step.operations.zh.length === step.operations.en.length));
  const p2pTools = p2p.workflow.flatMap((step) => step.tools.map((tool) => tool.name));
  for (const api of [
    "API_PURCHASEORDER_PROCESS_SRV",
    "API_MATERIAL_DOCUMENT_SRV",
    "API_SUPPLIERINVOICE_PROCESS_SRV",
    "API_OPLACCTGDOCITEMCUBE_SRV",
  ]) {
    assert.ok(p2pTools.includes(api));
  }

  const ppAgents = records.filter((agent) => agent.module === "PP");
  assert.equal(ppAgents.length, 5);
  assert.ok(ppAgents.every((agent) => agent.status === "Live-tested design"));
  assert.ok(ppAgents.every((agent) => agent.workflow.length === 6));
  assert.ok(ppAgents.every((agent) => agent.workflow.every((step) => step.operations.zh.length === step.operations.en.length)));
  assert.ok(ppAgents.every((agent) => agent.workflow.some((step) => step.tools.some((tool) => tool.kind === "Thin SAPClaw"))));
  assert.ok(ppAgents.every((agent) => agent.workflow.some((step) => step.tools.some((tool) => tool.kind === "SAPSkillhub"))));
});

test("manifest validation rejects a workflow step without tools", () => {
  const example = structuredClone(loadAgentCatalog(path.resolve("..", "agents"))[0]);
  example.workflow[0].tools = [];
  assert.throws(
    () => validateAgent(example, example.module, example.slug, "example/agent.json"),
    /tools must be a non-empty array/,
  );
});

test("manifest validation rejects step SAP scope outside the Agent scope", () => {
  const example = structuredClone(loadAgentCatalog(path.resolve("..", "agents")).find((agent) => agent.slug === "month-end-closing"));
  example.workflow[0].sapScope.transactions.push("SE38");
  assert.throws(
    () => validateAgent(example, example.module, example.slug, "example/agent.json"),
    /outside agent\.transactions/,
  );
});

test("manifest validation rejects an empty localized operations list", () => {
  const example = structuredClone(loadAgentCatalog(path.resolve("..", "agents")).find((agent) => agent.slug === "procure-to-pay-status"));
  example.workflow[0].operations.zh = [];
  assert.throws(
    () => validateAgent(example, example.module, example.slug, "example/agent.json"),
    /operations\.zh must be a non-empty array/,
  );
});

test("manifest validation rejects mismatched localized operations", () => {
  const example = structuredClone(loadAgentCatalog(path.resolve("..", "agents")).find((agent) => agent.slug === "procure-to-pay-status"));
  example.workflow[0].operations.en.pop();
  assert.throws(
    () => validateAgent(example, example.module, example.slug, "example/agent.json"),
    /operations must contain the same number of zh and en items/,
  );
});
