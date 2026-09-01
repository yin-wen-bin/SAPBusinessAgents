import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";

const readPage = (...segments) => readFile(path.join("dist", ...segments, "index.html"), "utf8");
const readManifest = async (module, slug) =>
  JSON.parse(await readFile(path.join("..", "agents", module, slug, "agent.json"), "utf8"));

test("static catalog contains all agents and the GitHub Pages base path", async () => {
  const html = await readPage("zh");
  for (const slug of ["ap-payment", "ar-collection", "gr-ir-clearing", "month-end-closing", "procure-to-pay-status"]) {
    assert.match(html, new RegExp(`/SAPBusinessAgents/zh/agents/(?:FI|MM)/${slug}/`));
  }
  assert.equal((html.match(/data-agent-id="FI\//g) ?? []).length, 4);
  assert.equal((html.match(/data-agent-id="Common\//g) ?? []).length, 1);
  assert.equal((html.match(/data-agent-id="CO\//g) ?? []).length, 5);
  assert.equal((html.match(/data-agent-id="MM\//g) ?? []).length, 5);
  assert.equal((html.match(/data-agent-id="SD\//g) ?? []).length, 12);
  assert.match(html, /class="odata-version-tag">V2</);
  assert.doesNotMatch(html, /href="\/zh\//);
});

test("CO detail pages render the exact manifest execution workflows", async () => {
  for (const slug of [
    "cost-center-expense-anomaly",
    "co-month-end-allocation-settlement",
    "product-cost-variance",
    "budget-rolling-forecast",
    "internal-order-project-control",
  ]) {
    const zh = await readPage("zh", "agents", "CO", slug);
    const en = await readPage("en", "agents", "CO", slug);
    const manifest = await readManifest("CO", slug);
    assert.equal((zh.match(/class="workflow-step"/g) ?? []).length, manifest.execution.steps.length);
    assert.equal((en.match(/class="workflow-step"/g) ?? []).length, manifest.execution.steps.length);
    assert.match(zh, /Embedded SAP OData Provider/);
    for (const skillStep of manifest.execution.steps.filter((step) => step.executor === "skill")) {
      assert.match(zh, new RegExp(skillStep.skillId));
    }
    assert.match(zh, /three-stage-live-acceptance\.md/);
  }
});

test("new MM detail pages render exact steps and fail-closed validation metadata", async () => {
  for (const slug of [
    "material-shortage-procurement-response",
    "inventory-health-balancing",
    "intelligent-sourcing-rfq",
    "supplier-performance-risk",
  ]) {
    const zh = await readPage("zh", "agents", "MM", slug);
    const manifest = await readManifest("MM", slug);
    assert.equal((zh.match(/class="workflow-step"/g) ?? []).length, manifest.execution.steps.length);
    assert.match(zh, /Embedded SAP OData Provider/);
    assert.match(zh, /sap-adt-table-export/);
    assert.match(zh, new RegExp(manifest.validation.verdict));
    assert.match(zh, /three-stage-live-acceptance\.md/);
    assert.match(zh, /class="odata-version-badge">V2</);
  }
});

test("role matching assistant renders description and document input modes", async () => {
  const zh = await readPage("zh", "agents", "Common", "role-agent-matching");
  const en = await readPage("en", "agents", "Common", "role-agent-matching");
  assert.match(zh, /运行岗位匹配助理/);
  assert.match(zh, /岗位描述文字和\/或本地文件或目录路径/);
  assert.match(zh, /用户描述与正式文档来源分开标记/);
  assert.match(en, /Run role-matching assistant/);
  assert.match(en, /Role description text and\/or local file or directory paths/);
  assert.doesNotMatch(zh, /执行这个 Agent/);
});

test("material shortage inputs are localized, documented, and prefilled", async () => {
  const zh = await readPage("zh", "agents", "MM", "material-shortage-procurement-response");
  const en = await readPage("en", "agents", "MM", "material-shortage-procurement-response");

  assert.match(zh, /MRP 区域/);
  assert.match(zh, /短缺参数文件/);
  assert.match(zh, /短缺定义序号/);
  assert.match(zh, /通常使用默认值 SAP000000001，无需修改/);
  assert.match(zh, /通常使用默认值 001，无需修改/);
  assert.match(zh, /name="shortage_profile"[^>]*value="SAP000000001"/);
  assert.match(zh, /name="shortage_counter"[^>]*value="001"/);
  assert.doesNotMatch(zh, />mrp area</i);
  assert.doesNotMatch(zh, />shortage profile</i);
  assert.doesNotMatch(zh, />shortage counter</i);

  assert.match(en, /MRP Area/);
  assert.match(en, /Shortage Profile/);
  assert.match(en, /Shortage Definition Counter/);
  assert.match(en, /The default SAP000000001 normally requires no change/);
  assert.match(en, /The default 001 normally requires no change/);
});

test("supplier performance accepts punctuated SAP identifiers and localizes run errors", async () => {
  const zh = await readPage("zh", "agents", "MM", "supplier-performance-risk");
  const en = await readPage("en", "agents", "MM", "supplier-performance-risk");
  const manifest = await readManifest("MM", "supplier-performance-risk");
  const panelSource = await readFile(path.join("src", "components", "AgentRunPanel.astro"), "utf8");
  const supplier = manifest.execution.inputSchema.properties.supplier;

  assert.equal(manifest.version, "0.2.1");
  assert.equal(supplier.maxLength, 10);
  assert.equal(supplier["x-sapba-sap-identifier"], true);
  assert.equal(supplier.pattern, undefined);
  assert.match(zh, /允许企业供应商编码中的连字符/);
  assert.match(en, /enterprise supplier IDs may contain non-control characters such as hyphens/);
  assert.match(zh, /准时足量交付率\(OTIF\)/);
  assert.match(en, /On Time In Full \(OTIF\)/);
  assert.match(zh, /<form class="agent-run-form" novalidate>/);
  assert.match(zh, /data-field-error="supplier"/);
  assert.match(zh, /请修正标出的输入后重试/);
  assert.match(zh, /无法连接本地运行服务/);
  assert.doesNotMatch(zh, /无法创建任务，请确认本地运行服务已经启动/);
  assert.match(panelSource, /class RunCreateHttpError extends Error/);
  assert.match(panelSource, /agent_input_invalid/);
  assert.match(panelSource, /caught instanceof RunCreateHttpError/);
});

test("SD detail pages render eleven execution-mapped workflows", async () => {
  const slugs = [
    "delivered-not-billed", "billing-block-diagnosis", "billing-completeness-check",
    "billing-output-monitor", "delivery-delay-prediction", "due-delivery-prioritization",
    "shortage-allocation-advisor", "billing-dispute-classification", "returns-credit-anomaly",
    "order-to-cash-anomaly-monitor", "order-to-cash-status",
  ];
  for (const slug of slugs) {
    const zh = await readPage("zh", "agents", "SD", slug);
    const manifest = await readManifest("SD", slug);
    assert.equal((zh.match(/class="workflow-step"/g) ?? []).length, manifest.execution.steps.length);
    assert.match(zh, /Embedded SAP OData Provider/);
    if (manifest.execution.steps.some((step) => step.executor === "skill")) {
      assert.match(zh, /sap-adt-table-export/);
    }
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
  assert.match(guidedEn, /The deterministic engine follows the declared steps exactly/);
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
  assert.match(run, /data-input-disclosure/);
  assert.match(run, /调整查询方向（可选）/);
  assert.match(run, /data-presentation-blocks/);
  assert.match(run, /presentation-block-/);
  assert.match(run, /预算型/);
  assert.match(run, /const pageSize = 20/);
  assert.match(run, /presentation\/blocks\/\$\{blockIndex\}\/rows\?offset=/);
  assert.match(run, /targetStart >= rows\.length/);
  assert.match(run, /rows\.push\(\.\.\.incoming\)/);
  assert.match(run, /cell\.textContent = localized\(value\)/);
  assert.match(run, /presentation-table-wide/);
  assert.match(run, /columnCount \* 118/);
  assert.match(run, /cell\.dataset\.key = column\.key/);
  assert.doesNotMatch(run, /\.innerHTML\s*=/);
  assert.match(run, /技术详情（供 IT 支持和审计使用）/);
  assert.match(run, /<details class="run-technical-details">/);
  assert.match(run, /原始 SAP 证据/);
  assert.match(run, /未执行付款准备复核：没有可复核的上游证据/);
  assert.match(run, /node_skipped_empty_input/);
  assert.match(run, /采购订单汇总/);
  assert.match(run, /AP付款准备度分组/);
  assert.match(run, /workflow-presentation\/tables/);
  assert.match(run, /workflow-report\.md/);
  assert.match(run, /workflow-orders\.csv/);
  assert.match(run, /workflow-ap-scopes\.csv/);
  assert.match(run, /const pageSize = 20/);
  assert.match(run, /payment_run_evidence_incomplete/);
  assert.match(run, /多轮查询与修正/);
  assert.match(run, /结果需要修正/);
  assert.match(run, /继续修正此结果/);
  assert.match(run, /结果符合预期/);
  assert.match(run, /用户期望与SAP证据核对/);
  assert.match(run, /free-query-sessions/);
  assert.match(run, /feedback-input/);
  assert.match(run, /agent-draft/);
  assert.match(run, /本轮没有新增SAP查询/);
  assert.match(run, /反馈处理进度/);
  assert.match(run, /Agent Runtime正在理解反馈/);
  assert.match(run, /feedback-requests/);
  assert.match(run, /harness_finalization_started/);
  assert.match(run, /已自适应延长/);
  assert.doesNotMatch(run, /\.innerHTML\s*=/);
  assert.match(plugins, /插件与能力/);
  assert.match(plugins, /data-plugin-manager/);
  assert.match(plugins, /禁止 SAP 写入/);
  assert.match(settings, /Agent Runtime 与 SDK/);
  assert.match(settings, /检查全部 Runtime/);
  assert.match(settings, /data-sdk-manager/);
  assert.match(settings, /设为默认 Runtime/);
});

test("workflow builder is rendered and consistently localized", async () => {
  const zh = await readPage("zh", "workflows");
  const en = await readPage("en", "workflows");
  const centerSource = await readFile(path.join("src", "components", "WorkflowCenter.tsx"), "utf8");
  const builderSource = await readFile(path.join("src", "components", "WorkflowBuilder.tsx"), "utf8");
  assert.match(zh, /已发布工作流/);
  assert.match(zh, /创建工作流/);
  assert.match(zh, /正在读取已发布工作流/);
  assert.doesNotMatch(zh, />Workflow builder</);
  assert.match(en, /Published workflows/);
  assert.match(en, /Create workflow/);
  assert.match(en, /Loading published workflows/);
  assert.doesNotMatch(en, />工作流编排</);
  assert.match(centerSource, /\/api\/workflows\/catalog/);
  assert.match(centerSource, /使用中的工作流/);
  assert.match(centerSource, /已停用工作流/);
  assert.match(centerSource, /创建新版本/);
  assert.match(centerSource, /永久删除/);
  assert.match(centerSource, /\/versions\/draft/);
  assert.match(centerSource, /method: action === "delete" \? "DELETE" : "POST"/);
  assert.match(centerSource, /mode: "workflow", workflowId: workflow\.id/);
  assert.match(centerSource, /每行一个，也可使用逗号或分号分隔/);
  assert.match(centerSource, /发布验证记录不可用/);
  assert.match(centerSource, /带完整性缺口发布/);
  assert.match(centerSource, /useState<CenterView>\("published"\)/);
  assert.match(builderSource, /用一句话生成工作流/);
  assert.match(builderSource, /你希望完成什么业务任务/);
  assert.match(builderSource, /从空白画布开始/);
  assert.match(builderSource, /Generate a workflow from one request/);
  assert.match(builderSource, /Publish workflow/);
  assert.match(builderSource, /正在通过只读 SAP 查询自动发现真机样本并启动验证/);
  assert.match(builderSource, /workflow_validation_input_unavailable/);
  assert.match(builderSource, /missing_fields/);
  assert.match(builderSource, /aria-required=\{required\}/);
  assert.match(builderSource, /workflow-spinner--button/);
  assert.match(builderSource, /workflow-validation-feedback is-/);
  assert.match(builderSource, /Agent Runtime 真机验证/);
  assert.match(builderSource, /草稿已生成/);
  assert.match(builderSource, /下一步：检查工作流/);
  assert.match(builderSource, /平台已清理非业务终态输出/);
  assert.match(builderSource, /重新生成草稿/);
  assert.match(builderSource, /dismissedRequestedOutputs/);
  assert.match(builderSource, /设计预审未通过/);
  assert.match(builderSource, /未启动样本发现、SAP查询或验证运行/);
  assert.match(builderSource, /workflow_runtime_review_blocked/);
  assert.match(builderSource, /preflightIssues/);
  assert.match(builderSource, /真机验证测试报告/);
  assert.match(builderSource, /本次真机验证存在以下证据完整性缺口/);
  assert.match(builderSource, /Live validation test report/);
  assert.match(builderSource, /validation-report/);
  assert.match(builderSource, /validationReportDigest/);
  assert.match(builderSource, /acceptedGapCodes/);
  assert.doesNotMatch(builderSource, /真机验证已启动/);
  assert.doesNotMatch(builderSource, /我确认并接受本次验证中的完整性缺口/);
  assert.match(builderSource, /条件终态/);
  assert.match(builderSource, /node\.onSkip\?\.reasonCode/);
  assert.match(builderSource, /工作流对话记录/);
  assert.match(builderSource, /设计符合预期/);
  assert.match(builderSource, /验证结果需要修正/);
  assert.match(builderSource, /accept-design/);
  assert.match(builderSource, /accept-validation/);
  assert.match(builderSource, /\/feedback/);
  assert.match(builderSource, /\/undo/);
});

test("detail pages render workflow and step-level tools", async () => {
  const ap = await readPage("zh", "agents", "FI", "ap-payment");
  assert.match(ap, /工作流与 Tools/);
  assert.doesNotMatch(ap, /class="table-of-contents"/);
  assert.doesNotMatch(ap, /本页目录/);
  assert.doesNotMatch(ap, /class="tag-list detail-tags"/);
  assert.match(ap, /id="sap-scope"/);
  assert.doesNotMatch(ap, /<h3>事务码<\/h3>/);
  assert.doesNotMatch(ap, /<h3>核心对象 \/ 表<\/h3>/);
  assert.match(ap, /class="source-button detail-source-button"/);
  assert.match(ap, /GET API_OPLACCTGDOCITEMCUBE_SRV@2\.0/);
  assert.match(ap, /evaluate_business_agent/);

  const closing = await readPage("zh", "agents", "FI", "month-end-closing");
  const grir = await readPage("zh", "agents", "FI", "gr-ir-clearing");
  assert.match(closing, /class="detail-module-badge">FI</);
  assert.match(closing, /SAP S\/4HANA/);
  assert.doesNotMatch(closing, /SAP ECC/);
  assert.doesNotMatch(closing, /class="step-sap-scope"/);
  assert.match(closing, /evaluate_business_agent/);
  assert.match(grir, /class="detail-module-badge">FI</);
});

test("P2P detail page renders the complete bilingual API workflow", async () => {
  const zh = await readPage("zh", "agents", "MM", "procure-to-pay-status");
  const en = await readPage("en", "agents", "MM", "procure-to-pay-status");

  const manifest = await readManifest("MM", "procure-to-pay-status");
  assert.equal((zh.match(/class="workflow-step"/g) ?? []).length, manifest.execution.steps.length);
  assert.equal((zh.match(/class="step-operations"/g) ?? []).length, manifest.execution.steps.length);
  assert.match(zh, /详细操作/);
  assert.match(zh, /本步骤 API \/ SAPSkill \/ Tools/);
  assert.match(zh, /Embedded SAP OData Provider/);
  assert.match(zh, /API_PURCHASEORDER_PROCESS_SRV/);
  assert.match(zh, /API_MATERIAL_DOCUMENT_SRV/);
  assert.match(zh, /API_SUPPLIERINVOICE_PROCESS_SRV/);
  assert.match(zh, /API_OPLACCTGDOCITEMCUBE_SRV/);
  assert.match(zh, /p2p-ap-workflow-live-acceptance\.md/);
  assert.match(zh, /执行这个 Agent/);
  assert.match(zh, /purchase_order/);

  assert.equal((en.match(/class="workflow-step"/g) ?? []).length, manifest.execution.steps.length);
  assert.equal((en.match(/class="step-operations"/g) ?? []).length, manifest.execution.steps.length);
  assert.match(en, /Detailed operations/);
  assert.match(en, /APIs, SAPSkills &amp; tools used at this step/);
  assert.match(en, /p2p-ap-workflow-live-acceptance\.md/);
});
