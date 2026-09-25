import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import * as draftHelpers from "../src/lib/agentDraft.ts";

test("acceptance timeout diagnosis is distinct from business mismatch and protects cleanup", async () => {
  const Acceptance = await component("AgentAcceptance", { "./AgentDraftInputs": () => null });
  for (const locale of ["zh", "en"]) {
    const markup = renderToStaticMarkup(createElement(Acceptance.AgentAcceptanceProgress, {
      open: true, locale, apiBase: "", draftId: "test",
      campaign: { status: "cancelling", phase: "cleanup", cases: [], report: {
        verdict: "NOT_TESTED", error: { code: "agent_acceptance_stage_timeout" },
        diagnostics: { last_failed_tool: "sap_evidence_read", last_error_code: "evidence_rows_restricted", cleanup_complete: false },
      } }, onClose() {}, onCancel() {}, onAdjust() {}, onRetry() {},
    }));
    assert.match(markup, /sap_evidence_read/);
    assert.match(markup, /evidence_rows_restricted/);
    assert.match(markup, locale === "zh" ? /草稿操作锁仍然保留/ : /draft remains locked/);
    assert.doesNotMatch(markup, /Change cases and retry|更换案例并重新验收/);
  }
});

test("publication progress separates the Git result from the page refresh", async () => {
  const Publication = await component("AgentPublicationProgress");
  const running = renderToStaticMarkup(createElement(Publication.default, {
    open: true, locale: "zh", connectionError: false,
    value: { status: "running", phase: "building_site", version: "1.2.3", publication_status: "pending", site_refresh_status: "building" },
    onClose() {}, onRetrySite() {}, onComplete() {},
  }));
  assert.match(running, /正在发布Agent/);
  assert.match(running, /构建页面/);
  assert.match(running, /后台继续/);
  const refreshFailed = renderToStaticMarkup(createElement(Publication.default, {
    open: true, locale: "zh", connectionError: false,
    value: { status: "completed", phase: "completed", version: "1.2.3", publication_status: "published", site_refresh_status: "failed", failure_code: "site_refresh_health_failed" },
    onClose() {}, onRetrySite() {}, onComplete() {},
  }));
  assert.match(refreshFailed, /Agent已发布，页面待刷新/);
  assert.match(refreshFailed, /当前前台仍显示上一可用页面/);
  assert.match(refreshFailed, /重试刷新页面/);
  const inactive = renderToStaticMarkup(createElement(Publication.default, {
    open: true, locale: "en", connectionError: false,
    value: { status: "completed", phase: "completed", publication_status: "published", site_refresh_status: "not_required", result: { active: false, version: "1.2.3" } },
    onClose() {}, onRetrySite() {}, onComplete() {}, onActivate() {},
  }));
  assert.match(inactive, /Activate this version/);
});

test("published inactive versions expose an explicit bilingual activation action and confirmation", async () => {
  const Activation = await component("AgentActivationDialog");
  const zh = renderToStaticMarkup(createElement(Activation.default, {
    open: true, confirming: true, locale: "zh", pending: { version: "1.0.1", can_activate: true },
    currentVersion: "1.0.0", value: null, connectionError: false,
    onClose() {}, onConfirm() {}, onRetrySite() {},
  }));
  assert.match(zh, /当前活动版本 1\.0\.0/);
  assert.match(zh, /待启用版本 1\.0\.1/);
  assert.match(zh, /已有工作流继续固定引用原版本/);
  assert.match(zh, /确认启用/);
  const en = renderToStaticMarkup(createElement(Activation.default, {
    open: true, confirming: false, locale: "en", pending: null,
    value: { status: "completed", phase: "completed", version: "1.0.1", activation_status: "active", site_refresh_status: "failed" },
    connectionError: false, onClose() {}, onConfirm() {}, onRetrySite() {},
  }));
  assert.match(en, /Version activated; page refresh failed/);
  assert.match(en, /Retry page refresh/);
});

test("formal acceptance setup and progress expose the multi-case read-only workflow", async () => {
  const Acceptance = await component("AgentAcceptance", { "./AgentDraftInputs": () => null });
  const setup = renderToStaticMarkup(createElement(Acceptance.AgentAcceptanceSetup, {
    open: true, locale: "zh", schema: { type: "object", properties: {} },
    mode: "three_stage", runtime: { model: "gpt-5.6-sol", reasoning_effort: "max" },
    cases: [{ caseId: "case-1", source: "current", input: {}, sensitiveInputs: {} }],
    currentInput: {}, currentSecrets: {}, canUseSample: false,
    onCases() {}, onFindSample() {}, onClose() {}, onStart() {},
  }));
  assert.match(setup, /设置正式验收/);
  assert.match(setup, /1–5组必选案例/);
  assert.match(setup, /gpt-5.6-sol/);
  assert.match(setup, /单独自动查找样本/);
  const progress = renderToStaticMarkup(createElement(Acceptance.AgentAcceptanceProgress, {
    open: true, locale: "en", apiBase: "http://127.0.0.1:8765", draftId: "draft-1",
    campaign: { campaign_id: "campaign-1", status: "running", phase: "baseline",
      started_at: new Date().toISOString(), estimated_max_seconds: 3120,
      cases: [{ case_id: "case-1", status: "running", phase: "baseline" }] },
    onClose() {}, onCancel() {}, onAdjust() {}, onRetry() {},
  }));
  assert.match(progress, /Formal acceptance in progress/);
  assert.match(progress, /Independent SAP baseline/);
  assert.match(progress, /Continue in background/);
  assert.match(progress, /Cancel acceptance/);
  const invalidMode = renderToStaticMarkup(createElement(Acceptance.AgentAcceptanceProgress, {
    open: true, locale: "zh", apiBase: "http://127.0.0.1:8765", draftId: "draft-1",
    campaign: { status: "interrupted", phase: "completed",
      report: { verdict: "NOT_TESTED", error: { code: "agent_acceptance_mode_invalid" } }, cases: [] },
    onClose() {}, onCancel() {}, onAdjust() {}, onRetry() {},
  }));
  assert.match(invalidMode, /验收模式无效/);
  const workspaceSource = await readFile(new URL("../src/components/AgentDraftWorkspace.tsx", import.meta.url), "utf8");
  assert.match(workspaceSource, /report: \{ verdict: "NOT_TESTED", error: \{ code \} \}/);
});

test("sample field selection lists visible optional inputs and preserves entered scope", async () => {
  const schema = { type: "object", required: ["date"], properties: {
    date: { type: "string", format: "date", title: { zh: "收货日期", en: "Receipt date" } },
    plant: { type: "string", title: { zh: "工厂", en: "Plant" } },
    company: { type: "string", title: { zh: "公司", en: "Company" } },
    secret: { type: "string", "x-sapba-sensitive": true }, hidden: { type: "string", "x-sapba-internal": true },
  } };
  const fields = draftHelpers.sampleFieldOptions(schema, "zh", { company: "1710" });
  assert.deepEqual(fields.map(x => x.key), ["date", "plant", "company", "secret"]);
  assert.equal(fields[0].required, true);
  assert.equal(fields[1].disabled, false);
  assert.equal(fields[2].disabled, true);
  assert.equal(fields[3].disabled, true);
  const Modal = await component("AgentSampleProgress");
  const html = renderToStaticMarkup(createElement(Modal, { fields, selectedFields: [], open: true, locale: "zh", canRetry: true }));
  assert.match(html, /选择需要查找测试数据的参数/);
  assert.match(html, /disabled="">开始查找/);
  assert.ok(!html.includes("正在查找测试样本数据"));
});

test("sample preflight explains manual scope and prevents premature SAP reads", async () => {
  const Modal = await component("AgentSampleProgress");
  for (const locale of ["zh", "en"]) {
    const html = renderToStaticMarkup(createElement(Modal, {
      fields: [{ key: "company_code", label: "Company", requirement: "Required", disabled: true,
        reason: locale === "zh" ? "组织范围需手工指定" : "Enter organizational scope manually" },
        { key: "customer", label: "Customer", requirement: "Required", disabled: false, reason: "" }],
      selectedFields: ["customer"], open: true, locale, canRetry: true, scopeRequired: true,
    }));
    assert.ok(html.includes(locale === "zh" ? "请先手工填写必需的公司代码或工厂范围" : "Enter the required company code or plant scope"));
    assert.match(html, /disabled="">(?:开始查找|Start discovery)/);
  }
});

test("sample modal exposes bilingual progress, background and cancellation separately", async () => {
  const Modal = await component("AgentSampleProgress");
  for (const locale of ["zh", "en"]) {
    const props = { open: true, locale, canRetry: true, onClose() {}, onCancel() {}, onRetry() {}, onReview() {} };
    const html = renderToStaticMarkup(createElement(Modal, { ...props, value: {
      status: "running", phase: "reading_candidates", timeout_seconds: 600, elapsed_seconds: 32,
      model: "gpt-5.6-sol", reasoning_effort: "max", run_id: "sample-test",
    }}));
    assert.match(html, /<dialog/);
    assert.match(html, /aria-labelledby="sample-progress-title"/);
    assert.ok(html.includes(locale === "zh" ? "正在查找测试样本数据" : "Finding test sample data"));
    assert.ok(html.includes(locale === "zh" ? "后台继续" : "Continue in background"));
    assert.ok(html.includes(locale === "zh" ? "取消查找" : "Cancel discovery"));
    assert.match(html, /10:00/);
    assert.match(html, /gpt-5.6-sol/);
    const failed = renderToStaticMarkup(createElement(Modal, { ...props, value: {
      status: "timed_out", failed_stage: "checking_sample", timeout_seconds: 300, elapsed_seconds: 305, candidate_count: 100,
    }}));
    assert.match(failed, /05:00/);
    assert.ok(failed.includes(locale === "zh" ? "尚未启动试运行" : "No trial was started"));
    assert.ok(failed.includes(locale === "zh" ? "重新查找" : "Retry discovery"));
    const ready = renderToStaticMarkup(createElement(Modal, { ...props, value: { status: "ready", field_sources: { date: {} } } }));
    assert.ok(ready.includes(locale === "zh" ? "查看并确认参数" : "Review and confirm inputs"));
    assert.ok(!ready.includes(locale === "zh" ? "取消查找" : "Cancel discovery"));
  }
});

test("trial modal exposes immediate bilingual progress and a full-result handoff", async () => {
  const Modal = await component("AgentTrialProgress");
  for (const locale of ["zh", "en"]) {
    const props = { open: true, locale, onClose() {}, onCancel() {}, onReview() {} };
    const active = renderToStaticMarkup(createElement(Modal, { ...props,
      trial: { status: "starting", timeout_seconds: 600, started_at: "2026-09-13T00:00:00Z" }, run: null,
    }));
    assert.match(active, /<dialog/);
    assert.ok(active.includes(locale === "zh" ? "正在执行只读试运行" : "Running read-only trial"));
    assert.ok(active.includes(locale === "zh" ? "后台继续" : "Continue in background"));
    assert.match(active, /10:00/);
    const completed = renderToStaticMarkup(createElement(Modal, { ...props,
      trial: { status: "completed", verdict: "PASS", timeout_seconds: 600, business_output_available: true, business_record_count: 2 },
      run: { run_id: "trial-1", status: "completed", elapsed_seconds: 28 },
    }));
    assert.ok(completed.includes(locale === "zh" ? "查看完整结果" : "View full result"));
    assert.ok(completed.includes(locale === "zh" ? "业务记录" : "Business records"));
    const timedOut = renderToStaticMarkup(createElement(Modal, { ...props,
      trial: { status: "failed", verdict: "FAIL", timeout_seconds: 600 },
      run: { run_id: "trial-timeout", status: "failed", error: { code: "run_timeout" } },
    }));
    assert.ok(timedOut.includes(locale === "zh" ? "试运行超时" : "Trial timed out"));
  }
});

test("feedback modal exposes immediate status, persisted phases and terminal actions", async () => {
  const Modal = await component("AgentFeedbackProgress");
  for (const locale of ["zh", "en"]) {
    const props = { open: true, locale, onClose() {}, onCancel() {}, onRetry() {}, onReview() {} };
    const active = renderToStaticMarkup(createElement(Modal, { ...props,
      turn: { turn: 3, kind: "feedback", status: "running", decision: { execution: { timeout_seconds: 3600, elapsed_seconds: 65 }, runtime_snapshot: { model: "gpt-5.6-sol", reasoning_effort: "max" } } },
      operation: { status: "running", detail: { turn: 3, phase: "generating_revision", completed_units: 1, total_units: 4 } },
    }));
    assert.match(active, /<dialog/);
    assert.match(active, /aria-labelledby="feedback-progress-title"/);
    assert.ok(active.includes(locale === "zh" ? "正在处理修改意见" : "Processing revision request"));
    assert.ok(active.includes(locale === "zh" ? "调查并生成修改" : "Investigating and preparing changes"));
    assert.ok(active.includes(locale === "zh" ? "后台继续" : "Continue in background"));
    assert.ok(active.includes(locale === "zh" ? "取消本轮" : "Cancel turn"));
    assert.match(active, /gpt-5\.6-sol/);
    assert.match(active, /max/);
    assert.ok(!active.includes("PRIVATE-RUNTIME-LOG"));

    const completed = renderToStaticMarkup(createElement(Modal, { ...props,
      turn: { turn: 3, kind: "feedback", status: "completed", base_revision: 8, result_revision: 9, decision: { changed: true, execution: { timeout_seconds: 3600, elapsed_seconds: 80 } } },
      operation: null,
    }));
    assert.ok(completed.includes(locale === "zh" ? "查看修改前后对比" : "Review before and after"));

    const failed = renderToStaticMarkup(createElement(Modal, { ...props,
      turn: { turn: 4, kind: "feedback", status: "failed", user_message: "retry", decision: { error_code: "runtime_agent_feedback_connection_failed", execution: { timeout_seconds: 3600, elapsed_seconds: 12 } } },
      operation: null,
    }));
    assert.ok(failed.includes(locale === "zh" ? "返回并重试" : "Return and retry"));
  }
});

test("full access feedback separates execution permission from live acceptance and application", async () => {
  const Conversation = await component("AgentDraftConversation");
  for (const locale of ["zh", "en"]) {
    const html = renderToStaticMarkup(createElement(Conversation, { locale, turns: [{
      turn: 1, kind: "feedback", status: "failed", user_message: "Check this draft",
      decision: { authoring_policy: { version: 2, mode: "full_access" },
        error_code: "runtime_command_preflight_timeout",
        harness: { live_testing: "not_performed", change_set_id: "rcs_example" } },
    }] }));
    assert.match(html, /full access/);
    assert.match(html, /rcs_example/);
    assert.ok(html.includes(locale === "zh" ? "不是安全沙盒" : "not a security sandbox"));
    assert.ok(html.includes(locale === "zh" ? "尚未应用" : "not applied"));
    assert.ok(html.includes(locale === "zh" ? "命令启动预检超时" : "command startup preflight timed out"));
    assert.ok(html.includes(locale === "zh" ? "不等于 SAP 真机验收" : "not SAP live acceptance"));
  }
});

test("trial failure is bilingual and never renders arbitrary exception text", () => {
  const failed = { status: "failed", error: { message: "Template path is unavailable: input.purchase_order" } };
  assert.match(draftHelpers.trialFailureText(failed, "zh"), /purchase_order/);
  assert.match(draftHelpers.trialFailureText(failed, "en"), /missing input/);
  assert.equal(draftHelpers.trialFailureText({ status: "completed" }, "zh"), "");
  const secret = "RAW_BANK_REFERENCE_123";
  assert.ok(!draftHelpers.trialFailureText({ status: "failed", error: { message: secret } }, "zh").includes(secret));
});

test("actual trial result shows the actionable failure next to the result", async () => {
  const Result = await component("AgentDraftResult");
  for (const locale of ["zh", "en"]) {
    const html = renderToStaticMarkup(createElement(Result, {
      locale, runPath: "/run/", apiBase: "http://127.0.0.1:8765",
      run: { run_id: "test-run", status: "failed", error: { message: "Template path is unavailable: input.purchase_order" } },
    }));
    assert.match(html, /role="alert"/);
    assert.match(html, /purchase_order/);
    assert.ok(html.includes(locale === "zh" ? "试运行失败原因" : "Why the trial failed"));
  }
});

test("historical completed trial without a business report is not presented as business success", async () => {
  const Result = await component("AgentDraftResult");
  for (const locale of ["zh", "en"]) {
    const html = renderToStaticMarkup(createElement(Result, {
      locale, runPath: "/run/", apiBase: "http://127.0.0.1:8765",
      trial: { status: "completed", verdict: "PASS" },
      run: { run_id: "legacy-run", status: "completed", result: { rule_results: [{ rule_id: "evidence_completeness" }], presentation: { blocks: [] } } },
    }));
    assert.ok(html.includes(locale === "zh" ? "查询已执行，但该草稿没有生成业务结果" : "The query ran, but this draft did not generate a business result"));
    assert.match(html, /role="alert"/);
  }
});

test("tool policy is conditional on preflight and is not SAP acceptance", async () => {
  const Conversation = await component("AgentDraftConversation");
  for (const locale of ["zh", "en"]) {
    const html = renderToStaticMarkup(createElement(Conversation, {locale, turns: [{
      turn: 1, status: "failed", feedback: "Test", decision: {
        authoring_policy: {mode: "isolated_tools"},
        error_code: "agent_harness_sandbox_preflight_timeout",
        harness: {live_testing: "not_performed"},
      },
    }]}));
    assert.ok(html.includes(locale === "zh" ? "预检通过后" : "require successful preflight"));
    assert.ok(html.includes(locale === "zh" ? "启动预检超时" : "startup preflight timed out"));
    assert.ok(html.includes(locale === "zh" ? "不等于 SAP 真机验收" : "not SAP live acceptance"));
  }
});
import { publicValues, validateDraftInput, changedDefinition, clearDiscoveredInput, discoveryFingerprint, diffBusinessLabel, draftStepNames, draftTerminal, localText, presentationCell, publicInput, publicBranchRequirements, restoreDiscoveredInput, retainCompatibleInput, technicalIdentity, technicalIdError, feedbackTiming, feedbackDuration, feedbackFailureText, canRetryFeedback, prepareFeedbackRequest } from "../src/lib/agentDraft.ts";

// Render the actual TSX without building the catalog or starting a browser/API.
const require = createRequire(import.meta.url);
async function component(name, dependencies = {}) {
  if (name === "AgentDraftConversation") dependencies["./SafeMarkdown"] = await component("SafeMarkdown");
  if (["AgentDraftWorkspace", "AgentAcceptance"].includes(name)) {
    dependencies["./AcceptanceReadiness"] = await component("AcceptanceReadiness");
  }
  if (name === "AgentDraftWorkspace") {
    dependencies["./AgentSampleProgress"] = await component("AgentSampleProgress");
    dependencies["./AgentTrialProgress"] = await component("AgentTrialProgress");
    dependencies["./AgentFeedbackProgress"] = await component("AgentFeedbackProgress");
    dependencies["./AgentPublicationProgress"] = await component("AgentPublicationProgress");
    dependencies["./AgentDefinitionDetails"] ||= () => null;
    dependencies["./AgentAcceptance"] ||= {
      AcceptanceSummary: () => null,
      AgentAcceptanceSetup: () => null,
      AgentAcceptanceProgress: () => null,
    };
  }
  const source = await readFile(new URL(`../src/components/${name}.tsx`, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } });
  const exports = {};
  new Function("require", "exports", compiled.outputText)((id) => id === "../lib/agentDraft" ? draftHelpers : id.endsWith(".css") ? {} : dependencies[id] || require(id), exports);
  return exports.default ? Object.assign(exports.default, exports) : exports;
}

const title = (zh, en) => ({ zh, en });

test("assistant Markdown escapes HTML, blocks unsafe links and never loads images", async () => {
  const Markdown = await component("SafeMarkdown");
  const html = renderToStaticMarkup(createElement(Markdown, {
    text: "**结论** <script>alert(1)</script> [bad](javascript:alert(1)) ![remote](https://example.com/x.png) [good](https://example.com)",
  }));
  assert.match(html, /<strong>结论<\/strong>/);
  assert.doesNotMatch(html, /<script|href="javascript:|<img/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /href="https:\/\/example.com"/);
});

test("trial diagnostic annotations bind to the matching run and source digest", async () => {
  const Result = await component("AgentDraftResult");
  const props = { run: { run_id: "run-current", status: "failed" },
    trial: { errors: [{ code: "trial_schema_invalid", message: "Check records" }] },
    locale: "en", runPath: "/en/run/", apiBase: "", revision: 7 };
  const bound = renderToStaticMarkup(createElement(Result, {
    ...props, feedbackSource: { source_id: "run-current", digest: "sha256:test" },
  }));
  assert.match(bound, /data-draft-annotation-kind="trial_issue"/);
  assert.match(bound, /data-draft-ref="\/errors\/0"/);
  assert.match(bound, /data-draft-source-digest="sha256:test"/);
  const stale = renderToStaticMarkup(createElement(Result, {
    ...props, feedbackSource: { source_id: "run-other", digest: "sha256:old" },
  }));
  assert.doesNotMatch(stale, /data-draft-annotation-kind="trial_issue"/);
});

test("assistant renders queued state, clarification, bound diff and collapsed diagnostics", async () => {
  const Conversation = await component("AgentDraftConversation");
  const html = renderToStaticMarkup(createElement(Conversation, { locale: "zh", turns: [
    { turn: 1, kind: "feedback", status: "completed", base_revision: 2, result_revision: 3,
      user_message: "请调整", decision: { action: "clarify", summary: { zh: "**请确认**", en: "Confirm" },
        pending_clarification: { id: "q1", revision: 3, question: { zh: "范围？", en: "Scope?" } } },
      diff: [{ path: "/manifest/title/zh", before: "旧", after: "新" }] },
    { turn: 2, kind: "feedback", status: "waiting", base_revision: 3, user_message: "继续", decision: {} },
  ], onWithdraw: () => {} }));
  assert.match(html, /待确认问题/);
  assert.match(html, /查看本轮修改/);
  assert.match(html, /修订 2 → 3/);
  assert.match(html, /撤回排队消息/);
  assert.match(html, /消息排队中/);
  assert.match(html, /<summary>技术详情<\/summary>/);
});

test("draft presentation exposes typed references without changing published details", async () => {
  const Details = await component("AgentDefinitionDetails");
  const agent = { sapModules: ["MM-PUR"], workflow: [{
    id: "read-order", title: { zh: "读取订单", en: "Read order" },
    description: { zh: "核对订单", en: "Check order" },
    executionStepIds: [],
  }] };
  const draft = renderToStaticMarkup(createElement(Details, {
    agent, locale: "zh", idPrefix: "draft", annotatable: true,
  }));
  const published = renderToStaticMarkup(createElement(Details, {
    agent, locale: "zh",
  }));
  assert.match(draft, /data-draft-ref="\/manifest\/sapModules"/);
  assert.match(draft, /data-draft-ref="\/manifest\/workflow\/0"/);
  assert.doesNotMatch(published, /data-draft-ref=/);
});

test("acceptance readiness explains contract gaps without claiming certification", async () => {
  const Readiness = await component("AcceptanceReadiness");
  for (const locale of ["zh", "en"]) {
    const html = renderToStaticMarkup(createElement(Readiness, { locale, value: { status: "needs_input", issues: [{ code: "contract_technical_metric", path: "/execution/acceptance/metrics" }] } }));
    assert.ok(html.includes(locale === "zh" ? "仍可保存草稿和试运行" : "Saving and trials remain available"));
    assert.ok(html.includes("/execution/acceptance/metrics"));
    const ready = renderToStaticMarkup(createElement(Readiness, { locale, value: { status: "ready", issues: [] } }));
    assert.ok(ready.includes(locale === "zh" ? "不代表业务验收通过" : "not certified"));
  }
});
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

test("six management stages and legacy links are stable; localizations never fall back to the wrong language", () => {
  assert.deepEqual(draftStepNames, ["purpose", "io", "logic", "trial", "acceptance", "publish"]);
  assert.equal(draftHelpers.legacyDraftStep("compose"), "purpose");
  assert.equal(draftHelpers.legacyDraftStep("review"), "publish");
  assert.equal(draftHelpers.legacyDraftStep("validate"), "acceptance");
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
  assert.match(source, /className="draft-assistant-actions"[\s\S]*?submitFeedback\("explain"\)[\s\S]*?解释问题/);
  assert.match(source, /className="draft-assistant-actions"[\s\S]*?submitFeedback\("revise"\)[\s\S]*?修改草稿/);
  assert.doesNotMatch(source, /draft-assistant-modes|feedbackIntent|请求解释|提交修改要求/);
  assert.match(source, /retryIntent === intent/);
  assert.match(source, /uncertainFeedback && pendingFeedbackRequest\.current\?\.intent !== intent/);
  assert.match(source, /disabled=\{busy \|\| draft\.status === "published" \|\| remoteConflict \|\| uncertainFeedback \|\| \(active && !feedbackTaskActive\)\}/);
  assert.match(source, /\/ui-state/);
  assert.match(source, /autoDiscover: false/);
  assert.match(source, /sensitiveInputs: secrets/);
  assert.match(source, /publishability\?\.can_publish === true/);
  assert.match(source, /baseline=source/);
  assert.match(source, /restoreDiscoveredInput/);
  assert.match(source, /requiresSampleConfirmation && !sampleConfirmed/);
  assert.match(source, /acceptance\.source_version \|\| acceptance\.reused_from_version/);
  assert.match(source, /change\[`\$\{side\}_exists`\] === false/);
  assert.doesNotMatch(source, /gpt-6-astra|localStorage|sessionStorage|dangerouslySetInnerHTML/);
  assert.doesNotMatch(source, /trial\?\.verdict === "PASS"/);
  assert.match(source, /\["before", "after"\]/);
  assert.match(source, /setTrial\(value\.trial \|\| null\)/);
  assert.doesNotMatch(source, /value\.trial\?\.run_id \|\| trialRunId/);
  assert.match(source, /setFeedbackDialogOpen\(true\)/);
  assert.match(source, /<AgentFeedbackProgress/);
  assert.ok(source.indexOf("setFeedbackDialogOpen(true)") < source.indexOf("call(`${base}/feedback`, request)"), "the modal opens before the feedback request returns");
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
  const next = { feedback: "Original request", locale: "zh", baseTurn: 2, baseRevision: 1, retryOfTurn: 2, intent: "revise", step: "logic" };
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
  assert.notEqual(prepareFeedbackRequest(first, { ...next, intent: "explain" }, id).requestId, first.requestId);
  assert.notEqual(prepareFeedbackRequest(first, { ...next, fieldPath: "/manifest/execution" }, id).requestId, first.requestId);
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
    const html = renderToStaticMarkup(createElement(Workspace, { initialDraft: draft, initialStep: "io", locale, apiBase: "", runPath: "/runs", onBack: () => {}, onPublished: () => {} }));
    assert.match(html, locale === "zh" ? /中文名称<input[^>]*value="交货日期"/ : /English name<input[^>]*value="Delivery date"/);
    assert.match(html, /<ul class="draft-input-definition-list"><li class="draft-input-definition"[^>]*><details><summary>/);
    assert.match(html, /<code>date<\/code><\/span><span class="draft-input-definition-meta">/);
    assert.match(html, locale === "zh" ? /设置查询条件与输出/ : /Set query conditions and output/);
    assert.match(html, locale === "zh" ? /<h2>结果结构预览<\/h2>[\s\S]*?<button[^>]*>让 AI 调整<\/button>/ : /<h2>Result structure preview<\/h2>[\s\S]*?<button[^>]*>Ask AI to adjust<\/button>/);
    assert.doesNotMatch(html, /错误旧展示|Stale display/);
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
  assert.match(html, /data-draft-ref="\/manifest\/title\/en"/);
  assert.match(html, /data-draft-ref="\/manifest\/owner"/);
  const trial = renderToStaticMarkup(createElement(Workspace, { ...props, initialStep: "trial" }));
  assert.match(trial, /disabled=""[^>]*>Run read-only trial/);
  const logic = renderToStaticMarkup(createElement(Workspace, { ...props, initialStep: "logic" }));
  assert.doesNotMatch(logic, /disabled=""[^>]*>Run automatic checks again/);
  const publish = renderToStaticMarkup(createElement(Workspace, { ...props, initialStep: "publish" }));
  assert.match(publish, /<section class="agent-panel draft-publish-details"><details><summary>Detailed change information<\/summary>/);
  assert.doesNotMatch(publish, /class="agent-panel draft-publish-details"><details open/);
  assert.match(publish, /disabled=""[^>]*>Publish inactive/);
  assert.match(publish, /Publication requirements are not met/);
  const publishZh = renderToStaticMarkup(createElement(Workspace, { ...props, initialStep: "publish", locale: "zh" }));
  assert.match(publishZh, /<summary>详细变更信息<\/summary>/);
  const publishedInactive = renderToStaticMarkup(createElement(Workspace, {
    ...props, initialStep: "publish", onActivatePublished() {},
    initialDraft: { ...draft, status: "published", publication_operation: {
      status: "completed", phase: "completed", publication_status: "published",
      site_refresh_status: "not_required", result: { agent_id: "sample-test", version: "0.1.0", active: false },
    } },
  }));
  assert.match(publishedInactive, /Activate this version/);
  const upgrade = renderToStaticMarkup(createElement(Workspace, { ...props, initialDraft: { ...draft, technical_identity: { kind: "version_upgrade", confirmed: true, locked: true, can_rename: false } } }));
  assert.match(upgrade, /version upgrade/);
  assert.doesNotMatch(upgrade, /id="draft-technical-id"/);
  const cleanup = renderToStaticMarkup(createElement(Workspace, { ...props, initialDraft: { ...draft, active_operation: { status: "cancelling", kind: "feedback" }, conversation: [{ turn: 2, kind: "feedback", status: "failed", user_message: "Retry me", decision: { error_code: "agent_feedback_cleanup_failed" } }] } }));
  assert.match(cleanup, /id="draft-technical-id"[^>]*disabled=""/);
  assert.match(cleanup, /disabled=""[^>]*>Cancel task/);
});

test("workbench separates the latest trial attempt from the effective trial", async () => {
  const Workspace = await component("AgentDraftWorkspace", {
    "./AgentDraftInputs": () => null,
    "./AgentDraftResult": () => null,
    "./AgentDraftConversation": () => null,
  });
  const draft = {
    draft_id: "draft-effective", agent_id: "effective-agent", revision: 3,
    status: "draft", target_version: "0.1.0", conversation: [],
    package: { manifest: { module: "MM", title: { zh: "示例", en: "Sample" }, version: "0.1.0", execution: { inputSchema: { type: "object", properties: {} } } } },
    technical_identity: { kind: "new_agent", confirmed: true, locked: false, can_rename: true },
    trial: { run_id: "trial-cancelled", revision: 3, status: "cancelled", verdict: "FAIL" },
    effective_trial: { run_id: "trial-pass", revision: 3, status: "completed", verdict: "PASS", business_output_available: true, output_schema_valid: true, read_only_audit: true },
    acceptance_readiness: { status: "ready", issues: [] },
    static_checks: { checks: ["manifest"], errors: [], presentation_contract: { status: "ready" } },
    acceptance: { verdict: "NOT_TESTED" }, publishability: { can_publish: false, blockers: [] },
  };
  const html = renderToStaticMarkup(createElement(Workspace, {
    initialDraft: draft, locale: "en", apiBase: "", runPath: "/runs",
    initialStep: "trial", onBack() {}, onPublished() {},
  }));
  assert.match(html, /Effective trial for acceptance/);
  assert.match(html, /trial-pass/);
  assert.match(html, /latest attempt did not replace this result/i);
});
