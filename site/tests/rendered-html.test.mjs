import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";

const readPage = (...segments) => readFile(path.join("dist", ...segments, "index.html"), "utf8");

test("static catalog contains all five agents and the GitHub Pages base path", async () => {
  const html = await readPage("zh");
  for (const slug of ["ap-payment", "ar-collection", "gr-ir-clearing", "month-end-closing", "procure-to-pay-status"]) {
    assert.match(html, new RegExp(`/SAPBusinessAgents/zh/agents/(?:FI|MM)/${slug}/`));
  }
  assert.equal((html.match(/data-module="FI"/g) ?? []).length, 4);
  assert.equal((html.match(/data-module="MM"/g) ?? []).length, 1);
  assert.doesNotMatch(html, /href="\/zh\//);
});

test("English catalog is generated", async () => {
  const html = await readPage("en");
  assert.match(html, /From business question to controlled action/);
  assert.match(html, /Step-level tools/);
});

test("detail pages render workflow and step-level tools", async () => {
  const ap = await readPage("zh", "agents", "FI", "ap-payment");
  assert.match(ap, /工作流与 Tools/);
  assert.match(ap, /ApIntentParser/);
  assert.match(ap, /SapApDataAdapter/);
  assert.match(ap, /PaymentRiskEngine/);

  const closing = await readPage("zh", "agents", "FI", "month-end-closing");
  const grir = await readPage("zh", "agents", "FI", "gr-ir-clearing");
  assert.match(closing, /module-fi/);
  assert.match(grir, /module-fi/);
});
