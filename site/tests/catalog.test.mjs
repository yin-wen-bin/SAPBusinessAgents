import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { loadAgentCatalog, validateAgent } from "../scripts/generate-agent-catalog.mjs";

test("Astro React development runtime stays on supported Vite 7", () => {
  const packageJson = JSON.parse(readFileSync("package.json", "utf8"));
  assert.match(packageJson.overrides?.vite ?? "", /^\^?7\./);
});

test("catalog discovers thirty valid agents with step-level tools", () => {
  const records = loadAgentCatalog(path.resolve("..", "agents"));
  assert.equal(records.length, 30);
  assert.deepEqual(
    records.map((agent) => `${agent.module}/${agent.slug}`),
    [
      "FI/ap-payment",
      "FI/ar-collection",
      "FI/gr-ir-clearing",
      "FI/month-end-closing",
      "CO/budget-rolling-forecast",
      "CO/co-month-end-allocation-settlement",
      "CO/cost-center-expense-anomaly",
      "CO/internal-order-project-control",
      "CO/product-cost-variance",
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
      "MM/intelligent-sourcing-rfq",
      "MM/inventory-health-balancing",
      "MM/material-shortage-procurement-response",
      "MM/procure-to-pay-status",
      "MM/supplier-performance-risk",
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
    assert.match(agent.title.zh, /[\u3400-\u9fff]/);
    assert.doesNotMatch(agent.title.en, /[\u3400-\u9fff]/);
    assert.doesNotMatch(agent.summary.en, /[\u3400-\u9fff]/);
    const stack = [agent.execution];
    while (stack.length) {
      const value = stack.pop();
      if (Array.isArray(value)) {
        stack.push(...value);
      } else if (value && typeof value === "object") {
        if (Object.hasOwn(value, "service_name") && Object.hasOwn(value, "entity_set")) {
          assert.ok(["2.0", "4.0"].includes(value.odata_version));
        }
        stack.push(...Object.values(value));
      }
    }
  }

  const sdAgents = records.filter((agent) => agent.module === "SD");
  assert.equal(sdAgents.length, 11);
  assert.ok(sdAgents.every((agent) => agent.workflow.length === agent.execution.steps.length));
  assert.ok(sdAgents.every((agent) => agent.guardrails.zh.some((item) => item.includes("只读"))));
  for (const agent of records) {
    const executionIds = new Set(agent.execution.steps.map((step) => step.id));
    const mappedIds = agent.workflow.flatMap((step) => step.executionStepIds);
    assert.deepEqual(new Set(mappedIds), executionIds);
    assert.equal(mappedIds.length, new Set(mappedIds).size);
  }

  const p2p = records.find((agent) => agent.slug === "procure-to-pay-status");
  assert.equal(p2p.schemaVersion, 2);
  assert.equal(p2p.execution.mode, "deterministic");
  assert.ok(p2p.execution.steps.every((step) => step.executor === "rule" || step.readOnly === true));
  assert.equal(p2p.workflow.length, p2p.execution.steps.length);
  assert.ok(p2p.workflow.every((step) => step.operations.zh.length > 0));
  assert.ok(p2p.workflow.every((step) => step.operations.zh.length === step.operations.en.length));
  const p2pOperations = p2p.workflow.flatMap((step) => step.operations.en);
  for (const api of [
    "API_PURCHASEORDER_PROCESS_SRV",
    "API_MATERIAL_DOCUMENT_SRV",
    "API_SUPPLIERINVOICE_PROCESS_SRV",
    "API_OPLACCTGDOCITEMCUBE_SRV",
  ]) {
    assert.ok(p2pOperations.some((operation) => operation.includes(api)));
  }

  const mmAgents = records.filter((agent) => agent.module === "MM");
  assert.equal(mmAgents.length, 5);
  const newMmAgents = mmAgents.filter((agent) => agent.slug !== "procure-to-pay-status");
  assert.ok(newMmAgents.every((agent) => agent.workflow.length === agent.execution.steps.length));
  assert.ok(newMmAgents.every((agent) => agent.validation?.providers.includes("embedded-sap-odata")));
  assert.ok(newMmAgents.every((agent) => agent.execution.steps.some((step) => step.when)));
  assert.ok(newMmAgents.every((agent) => agent.execution.steps.filter((step) => step.executor === "skill").every((step) => step.skillId === "sap-adt-table-export" && step.failurePolicy === "record_gap")));

  const coAgents = records.filter((agent) => agent.module === "CO");
  assert.equal(coAgents.length, 5);
  assert.ok(coAgents.every((agent) => agent.schemaVersion === 2));
  assert.ok(coAgents.every((agent) => agent.workflow.length === agent.execution.steps.length));
  assert.ok(coAgents.every((agent) => agent.validation?.providers.includes("embedded-sap-odata")));
  assert.ok(coAgents.every((agent) => agent.execution.steps.every((step) => ["sap_read", "skill", "rule"].includes(step.executor))));

  const o2c = records.find((agent) => agent.slug === "order-to-cash-status");
  assert.equal(o2c.schemaVersion, 2);
  assert.equal(o2c.execution.mode, "deterministic");

  const ppAgents = records.filter((agent) => agent.module === "PP");
  assert.equal(ppAgents.length, 5);
  const ppVerdicts = Object.fromEntries(ppAgents.map((agent) => [agent.slug, agent.validation?.verdict]));
  assert.deepEqual(ppVerdicts, {
    "demand-forecast-planning": "BLOCKED",
    "mrp-exception-analysis": "PASS",
    "production-order-monitoring": "PASS",
    "production-scheduling-capacity": "BLOCKED",
    "production-variance-analysis": "BLOCKED",
  });
  assert.ok(ppAgents.every((agent) => agent.workflow.length === agent.execution.steps.length));
  assert.ok(ppAgents.every((agent) => agent.workflow.every((step) => step.operations.zh.length === step.operations.en.length)));
  assert.ok(ppAgents.every((agent) => agent.schemaVersion === 2));
  assert.ok(ppAgents.every((agent) => agent.execution.mode === "deterministic"));
  assert.ok(ppAgents.every((agent) => agent.execution.steps.every((step) => step.executor === "rule" || step.readOnly === true)));
});

test("manifest validation rejects a workflow step without tools", () => {
  const example = structuredClone(loadAgentCatalog(path.resolve("..", "agents"))[0]);
  example.workflow[0].tools = [];
  assert.throws(
    () => validateAgent(example, example.module, example.slug, "example/agent.json"),
    /tools must be a non-empty array/,
  );
});

test("manifest validation rejects a schema v2 non-GET execution step", () => {
  const example = structuredClone(loadAgentCatalog(path.resolve("..", "agents")).find((agent) => agent.slug === "procure-to-pay-status"));
  example.execution.steps[0].request.plan.http_method = "POST";
  assert.throws(
    () => validateAgent(example, example.module, example.slug, "example/agent.json"),
    /non-GET SAP operation/,
  );
});

test("manifest validation permits truthful empty transaction and table scope", () => {
  const example = structuredClone(loadAgentCatalog(path.resolve("..", "agents"))[0]);
  example.transactions = [];
  example.tables = [];
  assert.doesNotThrow(() => validateAgent(example, example.module, example.slug, "example/agent.json"));
});

test("manifest validation still requires transaction and table scope arrays", () => {
  const example = structuredClone(loadAgentCatalog(path.resolve("..", "agents"))[0]);
  example.transactions = "none";
  assert.throws(
    () => validateAgent(example, example.module, example.slug, "example/agent.json"),
    /transactions must be an array/,
  );
});

test("manifest validation rejects a service reference without OData version", () => {
  const example = structuredClone(loadAgentCatalog(path.resolve("..", "agents")).find((agent) => agent.slug === "procure-to-pay-status"));
  delete example.execution.steps[0].request.plan.odata_version;
  assert.throws(
    () => validateAgent(example, example.module, example.slug, "example/agent.json"),
    /must declare odata_version/,
  );
});

test("manifest validation rejects duplicate execution step mappings", () => {
  const example = structuredClone(loadAgentCatalog(path.resolve("..", "agents")).find((agent) => agent.slug === "month-end-closing"));
  example.workflow[1].executionStepIds = [...example.workflow[0].executionStepIds];
  assert.throws(
    () => validateAgent(example, example.module, example.slug, "example/agent.json"),
    /maps an execution step more than once|must map every execution step exactly once/,
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

test("manifest validation rejects a public enum without bilingual display labels", () => {
  const example = structuredClone(loadAgentCatalog(path.resolve("..", "agents")).find((agent) => agent.slug === "inventory-health-balancing"));
  delete example.execution.outputSchema.properties.selected_checks["x-sapba-display"];
  assert.throws(
    () => validateAgent(example, example.module, example.slug, "example/agent.json"),
    /must localize every public enum/,
  );
});

test("manifest validation rejects an incomplete public enum translation", () => {
  const example = structuredClone(loadAgentCatalog(path.resolve("..", "agents")).find((agent) => agent.slug === "inventory-health-balancing"));
  delete example.execution.outputSchema.properties.selected_checks["x-sapba-display"].labels.expiry.zh;
  assert.throws(
    () => validateAgent(example, example.module, example.slug, "example/agent.json"),
    /labels\.expiry must be localized|labels\.expiry\.zh must be a non-empty string/,
  );
});
