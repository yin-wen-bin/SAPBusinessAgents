import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { loadAgentCatalog, validateAgent } from "../scripts/generate-agent-catalog.mjs";

test("catalog discovers five valid agents with step-level tools", () => {
  const records = loadAgentCatalog(path.resolve("..", "agents"));
  assert.equal(records.length, 5);
  assert.deepEqual(
    records.map((agent) => `${agent.module}/${agent.slug}`),
    [
      "FI/ap-payment",
      "FI/ar-collection",
      "FI/gr-ir-clearing",
      "FI/month-end-closing",
      "MM/procure-to-pay-status",
    ],
  );
  for (const agent of records) {
    assert.ok(agent.workflow.length > 0);
    assert.ok(agent.workflow.every((step) => step.tools.length > 0));
  }
});

test("manifest validation rejects a workflow step without tools", () => {
  const example = structuredClone(loadAgentCatalog(path.resolve("..", "agents"))[0]);
  example.workflow[0].tools = [];
  assert.throws(
    () => validateAgent(example, example.module, example.slug, "example/agent.json"),
    /tools must be a non-empty array/,
  );
});
