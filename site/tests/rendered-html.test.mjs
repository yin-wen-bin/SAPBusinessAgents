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
  assert.equal((html.match(/data-agent-id="FI\//g) ?? []).length, 4);
  assert.equal((html.match(/data-agent-id="MM\//g) ?? []).length, 1);
  assert.doesNotMatch(html, /href="\/zh\//);
});

test("English catalog reuses the SAPSkillhub UI structure", async () => {
  const html = await readPage("en");
  assert.match(html, /Discover and use SAP business agents/);
  assert.match(html, /class="site-brand"/);
  assert.match(html, /class="sidebar"/);
  assert.match(html, /class="search-shell"/);
  assert.match(html, /class="catalog-table-head"/);
});

test("detail pages render workflow and step-level tools", async () => {
  const ap = await readPage("zh", "agents", "FI", "ap-payment");
  assert.match(ap, /工作流与 Tools/);
  assert.doesNotMatch(ap, /class="table-of-contents"/);
  assert.doesNotMatch(ap, /本页目录/);
  assert.doesNotMatch(ap, /class="tag-list detail-tags"/);
  assert.match(ap, /id="sap-scope"/);
  assert.match(ap, /class="source-button detail-source-button"/);
  assert.match(ap, /ApIntentParser/);
  assert.match(ap, /SapApDataAdapter/);
  assert.match(ap, /PaymentRiskEngine/);

  const closing = await readPage("zh", "agents", "FI", "month-end-closing");
  const grir = await readPage("zh", "agents", "FI", "gr-ir-clearing");
  assert.match(closing, /class="detail-module-badge">FI</);
  assert.match(closing, /SAP S\/4HANA On-Premise/);
  assert.doesNotMatch(closing, /id="sap-scope"/);
  assert.match(closing, /class="step-sap-scope"/);
  assert.match(closing, /业务模块/);
  assert.match(closing, /事务码/);
  assert.match(closing, /核心对象 \/ 表/);
  assert.match(grir, /class="detail-module-badge">FI</);
});
