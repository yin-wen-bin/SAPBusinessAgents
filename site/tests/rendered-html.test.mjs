import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";

const readPage = (...segments) => readFile(path.join("dist", ...segments, "index.html"), "utf8");

test("static catalog contains all thirty agents and the GitHub Pages base path", async () => {
  const html = await readPage("zh");
  for (const slug of ["ap-payment", "ar-collection", "gr-ir-clearing", "month-end-closing", "procure-to-pay-status"]) {
    assert.match(html, new RegExp(`/SAPBusinessAgents/zh/agents/(?:FI|MM)/${slug}/`));
  }
  assert.equal((html.match(/data-agent-id="FI\//g) ?? []).length, 4);
  assert.equal((html.match(/data-agent-id="CO\//g) ?? []).length, 5);
  assert.equal((html.match(/data-agent-id="MM\//g) ?? []).length, 5);
  assert.equal((html.match(/data-agent-id="SD\//g) ?? []).length, 11);
  assert.doesNotMatch(html, /href="\/zh\//);
});

test("CO detail pages render five independent eight-step Embedded and ADT workflows", async () => {
  for (const slug of [
    "cost-center-expense-anomaly",
    "co-month-end-allocation-settlement",
    "product-cost-variance",
    "budget-rolling-forecast",
    "internal-order-project-control",
  ]) {
    const zh = await readPage("zh", "agents", "CO", slug);
    const en = await readPage("en", "agents", "CO", slug);
    assert.equal((zh.match(/class="workflow-step"/g) ?? []).length, 8);
    assert.equal((en.match(/class="workflow-step"/g) ?? []).length, 8);
    assert.match(zh, /Embedded API schema/);
    assert.match(zh, /sap-adt-table-export/);
    assert.match(zh, /live-sap-test-report\.md/);
  }
});

test("new MM detail pages render eight steps and live validation metadata", async () => {
  for (const slug of [
    "material-shortage-procurement-response",
    "inventory-health-balancing",
    "intelligent-sourcing-rfq",
    "supplier-performance-risk",
  ]) {
    const zh = await readPage("zh", "agents", "MM", slug);
    assert.equal((zh.match(/class="workflow-step"/g) ?? []).length, 8);
    assert.match(zh, /Embedded Provider/);
    assert.match(zh, /sap-adt-table-export/);
    assert.match(zh, /真机验收/);
    assert.match(zh, /live-sap-test-report\.md/);
  }
});

test("SD detail pages render eleven independent eight-step workflows", async () => {
  const slugs = [
    "delivered-not-billed", "billing-block-diagnosis", "billing-completeness-check",
    "billing-output-monitor", "delivery-delay-prediction", "due-delivery-prioritization",
    "shortage-allocation-advisor", "billing-dispute-classification", "returns-credit-anomaly",
    "order-to-cash-anomaly-monitor", "order-to-cash-status",
  ];
  for (const slug of slugs) {
    const zh = await readPage("zh", "agents", "SD", slug);
    assert.equal((zh.match(/class="workflow-step"/g) ?? []).length, 8);
    assert.match(zh, /sap_read\.health|sap_read_provider_health/);
    assert.doesNotMatch(zh, /SAPClaw|sapclaw/);
    assert.match(zh, /SAPSkillhub read-only skill/);
    assert.match(zh, /严格只读/);
  }
});

test("English catalog reuses the SAPSkillhub UI structure", async () => {
  const html = await readPage("en");
  assert.match(html, /Discover and use SAP business agents/);
  assert.match(html, /class="site-brand"/);
  assert.match(html, /class="sidebar"/);
  assert.match(html, /class="search-shell"/);
  assert.match(html, /class="catalog-table-head"/);
  assert.doesNotMatch(html, /诊断销售订单|核对订单、交货和开票|聚合订单、交货、开票/);
});

test("catalog and Agent run entry points are consistently localized", async () => {
  const zh = await readPage("zh");
  assert.match(zh, /应付账款付款助手/);
  assert.match(zh, /生产订单执行监控助手/);

  const guidedZh = await readPage("zh", "agents", "FI", "ap-payment");
  const guidedEn = await readPage("en", "agents", "FI", "ap-payment");
  assert.match(guidedZh, /执行引擎严格按照已定义步骤运行/);
  assert.match(guidedZh, /执行这个 Agent/);
  assert.match(guidedEn, /The runtime follows the declared steps exactly/);
  assert.match(guidedEn, /Run this Agent/);
  assert.doesNotMatch(guidedZh, /该 Agent 的确定性工作流尚未接入/);

  const deterministic = await readPage("zh", "agents", "MM", "procure-to-pay-status");
  assert.match(deterministic, /执行引擎严格按照已定义步骤运行/);
  assert.doesNotMatch(deterministic, /该 Agent 的确定性工作流尚未接入/);
});

test("dual-mode prototype renders free-query and run pages", async () => {
  const home = await readPage("zh");
  const ask = await readPage("zh", "ask");
  const run = await readPage("zh", "run");
  const plugins = await readPage("zh", "plugins");
  const settings = await readPage("zh", "settings");
  assert.match(home, /直接询问 SAP/);
  assert.match(ask, /开始只读查询/);
  assert.match(run, /查询进度/);
  assert.match(run, /业务结论/);
  assert.match(run, /各阶段结果/);
  assert.match(run, /查询结果明细/);
  assert.match(run, /data-input-question/);
  assert.match(run, /请直接回答下面的具体问题/);
  assert.match(run, /技术详情（供 IT 支持和审计使用）/);
  assert.match(run, /<details class="run-technical-details">/);
  assert.match(run, /原始 SAP 证据/);
  assert.match(plugins, /插件与能力/);
  assert.match(plugins, /data-plugin-manager/);
  assert.match(plugins, /禁止 SAP 写入/);
  assert.match(settings, /SDK 版本与更新/);
  assert.match(settings, /检查全部更新/);
  assert.match(settings, /data-sdk-manager/);
});

test("workflow builder is rendered and consistently localized", async () => {
  const zh = await readPage("zh", "workflows");
  const en = await readPage("en", "workflows");
  assert.match(zh, /工作流编排/);
  assert.match(zh, /Codex 真机验证/);
  assert.match(zh, /发布固定工作流/);
  assert.doesNotMatch(zh, />Workflow builder</);
  assert.match(en, /Workflow builder/);
  assert.match(en, /Validate live with Codex/);
  assert.match(en, /Publish fixed workflow/);
  assert.doesNotMatch(en, />工作流编排</);
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

test("P2P detail page renders the complete bilingual API workflow", async () => {
  const zh = await readPage("zh", "agents", "MM", "procure-to-pay-status");
  const en = await readPage("en", "agents", "MM", "procure-to-pay-status");

  assert.equal((zh.match(/class="workflow-step"/g) ?? []).length, 8);
  assert.equal((zh.match(/class="step-operations"/g) ?? []).length, 8);
  assert.match(zh, /详细操作/);
  assert.match(zh, /本步骤 API \/ SAPSkill \/ Tools/);
  assert.match(zh, /sap_read\.health/);
  assert.match(zh, /sap_read\.schema/);
  assert.match(zh, /API_PURCHASEORDER_PROCESS_SRV/);
  assert.match(zh, /API_MATERIAL_DOCUMENT_SRV/);
  assert.match(zh, /API_SUPPLIERINVOICE_PROCESS_SRV/);
  assert.match(zh, /API_OPLACCTGDOCITEMCUBE_SRV/);
  assert.match(zh, /OriginalReferenceDocument/);
  assert.match(zh, /ClearingAccountingDocument/);
  assert.match(zh, /本次验证主路径未使用/);
  assert.match(zh, /执行这个 Agent/);
  assert.match(zh, /purchase_order/);

  assert.equal((en.match(/class="workflow-step"/g) ?? []).length, 8);
  assert.equal((en.match(/class="step-operations"/g) ?? []).length, 8);
  assert.match(en, /Detailed operations/);
  assert.match(en, /APIs, SAPSkills &amp; tools used at this step/);
  assert.match(en, /OriginalReferenceDocument/);
  assert.match(en, /ClearingAccountingDocument/);
  assert.match(en, /none was used in the live validation/);
});
