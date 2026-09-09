// Isolated UI acceptance. Every API request is fulfilled or rejected locally.
// No existing draft, business data, SAP connection, or model is read or changed.
// Run from site after `npm run build`. In terminal 1:
// npm run preview -- --host 127.0.0.1 --port 4322
// In terminal 2 (PowerShell; use the matching preview base path):
// $env:SAPBA_SITE_URL = "http://127.0.0.1:4322/SAPBusinessAgents"
// $env:SAPBA_PLAYWRIGHT_MODULE = "<absolute path to an already installed playwright package>"
// node tests/browser/agent-identity-feedback.mjs
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.SAPBA_PLAYWRIGHT_MODULE || "playwright");
const origin = new URL(process.env.SAPBA_SITE_URL || "http://127.0.0.1:4321");
if (origin.protocol !== "http:" || origin.hostname !== "127.0.0.1" || origin.username || origin.password) throw new Error("Only an isolated http://127.0.0.1 preview is permitted");
const siteBase = origin.pathname.replace(/\/$/, "");
const pageUrl = (locale, name) => `${origin.origin}${siteBase}/${locale}/${name}/`;
const output = new URL(`../../../.local-data/ui/agent-identity-feedback/${new Date().toISOString().replace(/[:.]/g, "-")}/`, import.meta.url);
await mkdir(output, { recursive: true });
const draftId = "agent_draft_synthetic_browser";
const originalId = "synthetic-agent-original";
const renamedId = "synthetic-agent-renamed";
const requestText = "Synthetic revision request / 合成修改意见";
const anchor = Date.parse("2030-01-02T03:04:00Z");
const iso = (time) => new Date(time).toISOString();
const copy = (value) => structuredClone(value);
const results = [];
const violations = [];
let apiRequests = 0;

function fixture() {
  return {
    draft_id: draftId, agent_id: originalId, module: "Common", revision: 1,
    title: { zh: "合成验收草稿", en: "Synthetic acceptance draft" },
    target_version: "0.1.0", status: "needs_review", source_type: "clone", source_version: "1.0.0",
    technical_identity: { kind: "new_agent", agent_id: originalId, confirmed: false, locked: false, can_rename: true, blockers: [] },
    package: {
      manifest: { schemaVersion: 2, slug: originalId, module: "Common", version: "0.1.0", owner: "Synthetic test team",
        title: { zh: "合成验收草稿", en: "Synthetic acceptance draft" }, summary: { zh: "仅用于隔离界面测试", en: "Isolated UI testing only" },
        inputs: { zh: [], en: [] }, outputs: { zh: [], en: [] }, workflow: [], sapModules: [],
        guardrails: { zh: ["合成数据，不连接 SAP 或模型"], en: ["Synthetic data; no SAP or model connections"] },
        execution: { inputSchema: { type: "object", properties: {}, required: [] } } },
      readme: "Synthetic documentation only", rules: null,
    },
    acceptance: { verdict: "PASS", report_digest: "synthetic-report" },
    publishability: { can_publish: true, blockers: [] },
    revisions: [{ revision: 1 }], active_operation: null,
    conversation: [
      { turn: 1, kind: "clone", status: "completed", result_revision: 1, decision: { source: "clone" } },
      { turn: 2, kind: "feedback", status: "failed", user_message: requestText, request_id: "synthetic-old-request",
        completed_at: iso(anchor - 10000), decision: { error_code: "agent_feedback_timeout", agent_id: originalId,
          runtime_snapshot: { model: "synthetic-model", reasoning_effort: "xhigh" },
          execution: { started_at: iso(anchor - 190113), timeout_seconds: 180, elapsed_seconds: 180.113 } } },
      { turn: 3, kind: "feedback", status: "completed", user_message: "Synthetic unrecorded historical timing", decision: { assistant_message: { zh: "合成历史回复", en: "Synthetic historical reply" } } },
    ],
  };
}

const sdkFixture = (state) => ({ sdk_id: "synthetic-sdk", provider_id: "synthetic", name: { zh: "合成 Runtime", en: "Synthetic Runtime" }, description: { zh: "不调用真实模型", en: "No real model calls" },
  enabled: true, installed: true, selected: true, selectable: true, platform_supported: true, authenticated: true,
  current_version: "test", cli_version: "test", package_name: "synthetic-sdk", ecosystem: "python", capabilities: [],
  default_model_id: state.defaultModel, model_discovery_supported: true, blockers: [] });
const modelFixture = (state) => ({ provider_id: "synthetic", catalog_status: "ready", model_catalog_complete: true, captured_at: iso(anchor), items: [
  { model_id: "synthetic-model", display_name: "Synthetic model", input_modalities: ["text"], service_tiers: [],
    supported_reasoning_efforts: ["low", "medium", "high", "xhigh"], default_reasoning_effort: "medium", saved_reasoning_effort: state.effort,
    reasoning_effort: state.effort, reasoning_effort_source: "system_settings", selected: state.defaultModel === "synthetic-model",
    check_status: "compatible", selectable: true, retired: false },
] });

async function installMocks(context, state) {
  await context.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname;
    if (!path.includes("/api/")) {
      if (url.origin === origin.origin && method === "GET") return route.continue();
      violations.push(`Blocked non-static request: ${method} ${url.origin}${path}`);
      return route.abort("blockedbyclient");
    }
    apiRequests++;
    const headers = { "access-control-allow-origin": request.headers().origin || "*", "access-control-allow-methods": "GET, POST, PUT, OPTIONS", "access-control-allow-headers": "content-type, x-sapba-action" };
    const respond = (json, status = 200) => route.fulfill({ status, headers, json: copy(json) });
    if (method === "OPTIONS") return route.fulfill({ status: 204, headers });
    const base = `/api/authoring/agents/${draftId}`;
    if (method === "GET" && path === "/api/agents/catalog") return respond([]);
    if (method === "GET" && path === "/api/authoring/agents") return respond([state.draft]);
    if (method === "GET" && path === base) return respond(state.draft);
    if (method === "GET" && path === `${base}/validation-report`) return respond({ acceptance: state.draft.acceptance, publishability: state.draft.publishability, static_checks: state.draft.static_checks });
    if (method === "POST" && path === `${base}/validate`) {
      state.staticChecks.push(request.postDataJSON());
      state.draft.static_checks = { checks: [{ code: "synthetic-static-check" }], errors: [] };
      return respond(state.draft);
    }
    if (method === "POST" && path === `${base}/technical-id/check`) {
      const body = request.postDataJSON(); state.identityChecks.push(body);
      if (body.agentId === "synthetic-taken") return respond({ detail: { code: "agent_technical_id_taken" } }, 409);
      return respond({ agent_id: body.agentId, available: true, reserved: false, revision: state.draft.revision });
    }
    if (method === "PUT" && path === `${base}/technical-id`) {
      const body = request.postDataJSON(); state.identityWrites.push(body);
      assert.equal(body.expectedRevision, state.draft.revision);
      const before = state.draft.agent_id;
      state.draft.agent_id = body.agentId;
      state.draft.package.manifest.slug = body.agentId;
      state.draft.technical_identity = { ...state.draft.technical_identity, agent_id: body.agentId, confirmed: true };
      if (before !== body.agentId) {
        state.draft.revision++; state.draft.revisions.push({ revision: state.draft.revision });
        state.draft.acceptance = { verdict: "NOT_TESTED" }; state.draft.publishability = { can_publish: false, blockers: [] };
        state.draft.conversation.push({ turn: 4, kind: "technical_id", status: "completed", result_revision: 2, diff: [{ path: "/manifest/slug", before, after: body.agentId }] });
      }
      return respond(state.draft);
    }
    if (method === "POST" && path === `${base}/feedback`) {
      const body = request.postDataJSON(); state.feedbackRequests.push(body);
      if (state.dropFirstFeedback) { state.dropFirstFeedback = false; return route.abort("failed"); }
      state.draft.active_operation = { operation_id: "synthetic-operation", kind: "feedback", status: "running", turn: 5, request_id: body.requestId };
      state.draft.conversation.push({ turn: 5, kind: "feedback", status: "running", user_message: body.feedback,
        decision: { retry_of_turn: body.retryOfTurn, agent_id: state.draft.agent_id, runtime_snapshot: { model: "synthetic-model", reasoning_effort: "high" },
          execution: { started_at: iso(state.now - 125000), deadline_at: iso(state.now + 3475000), timeout_seconds: 3600, elapsed_seconds: 125 } } });
      return respond({ status: "running", turn: 5, operation_id: "synthetic-operation" });
    }
    if (method === "GET" && path === "/api/system/sdk-runtimes") return respond({ items: [sdkFixture(state)], default_provider_id: "synthetic" });
    if (method === "GET" && path === "/api/system/sdk-runtimes/synthetic/models") return respond(modelFixture(state));
    if (method === "POST" && path === "/api/system/sdk-runtimes/synthetic/models/check") {
      const body = request.postDataJSON(); state.modelChecks.push(body);
      if (state.modelCheckGate) await state.modelCheckGate;
      return respond({ check: { compatible: true, status: "compatible", reasoning_effort: body.reasoning_effort } });
    }
    if (method === "PUT" && path === "/api/system/sdk-runtimes/synthetic/models/synthetic-model/reasoning-effort") {
      const body = request.postDataJSON(); state.effortWrites.push(body); state.effort = body.reasoning_effort;
      return respond({ item: modelFixture(state) });
    }
    if (method === "PUT" && path === "/api/system/sdk-runtimes/synthetic/default-model") {
      const body = request.postDataJSON(); state.modelWrites.push(body); state.defaultModel = body.model_id;
      return respond({ item: sdkFixture(state) });
    }
    violations.push(`Unexpected API (never forwarded): ${method} ${path}`);
    return respond({ detail: { code: "synthetic_unexpected_api" } }, 418);
  });
  // Block service-worker bypass and browser-created WebSockets as well as HTTP APIs.
  if (typeof context.routeWebSocket !== "function") throw new Error("Installed Playwright must support routeWebSocket to keep acceptance fail-closed");
  await context.routeWebSocket("**/*", (socket) => socket.close());
}

async function eventually(read, expected, label) {
  for (let count = 0; count < 80; count++) {
    if (await read() === expected) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.equal(await read(), expected, label);
}

const browser = await chromium.launch({ channel: process.env.SAPBA_BROWSER_CHANNEL || "msedge", headless: true });
try {
  for (const locale of ["zh", "en"]) for (const [width, height] of [[1440, 900], [772, 698], [390, 844]]) {
    const state = { draft: fixture(), identityChecks: [], identityWrites: [], staticChecks: [], feedbackRequests: [], dropFirstFeedback: true,
      now: anchor + 1000, effort: "high", defaultModel: "synthetic-current", modelChecks: [], effortWrites: [], modelWrites: [] };
    const context = await browser.newContext({ viewport: { width, height }, serviceWorkers: "block" });
    await installMocks(context, state);
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const errors = []; page.on("pageerror", (error) => errors.push(error.message));
    const tr = (zh, en) => locale === "zh" ? zh : en;
    const button = (zh, en) => page.getByRole("button", { name: tr(zh, en), exact: true });
    const shot = async (name, locator) => { if (locator) await locator.scrollIntoViewIfNeeded(); await page.screenshot({ path: fileURLToPath(new URL(`${locale}-${width}-${name}.png`, output)) }); };
    const noOverflow = async () => assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${locale} ${width}: horizontal overflow`);
    const loadDraft = async () => { await page.goto(`${pageUrl(locale, "agent-management")}?draft=${draftId}`); await page.locator(".draft-technical-identity").waitFor(); };
    try {
      await page.clock.install({ time: new Date(anchor) });
      await page.clock.pauseAt(new Date(state.now));
      await loadDraft();
      const identity = page.locator("#draft-technical-id");
      const trial = button("执行只读试运行", "Run read-only trial");
      const composer = page.locator("#draft-feedback");
      const oldTurn = page.locator(".draft-chat-entry").nth(0);
      assert.equal(await trial.isDisabled(), true);
      assert.equal(await button("自动检查定义", "Check definition").isEnabled(), true);
      assert.equal(await page.locator(".draft-discovery input[type=checkbox]").isDisabled(), true);
      assert.equal(await composer.isEnabled(), true);
      await button("自动检查定义", "Check definition").click();
      await page.getByText(tr("自动检查通过", "Automatic checks passed"), { exact: true }).waitFor();
      await eventually(() => button("自动检查定义", "Check definition").isEnabled(), true, "Static checks finish without requiring identity confirmation");
      assert.deepEqual(state.staticChecks, [{ expectedRevision: 1 }]);
      assert.equal(state.draft.technical_identity.confirmed, false);
      assert.equal(await trial.isDisabled(), true);
      assert.match(await oldTurn.innerText(), locale === "zh" ? /本轮时限: 3分0秒/ : /Turn time limit: 3m 0s/);
      assert.match(await oldTurn.innerText(), /xhigh/);
      assert.match(await page.locator(".draft-chat-entry").nth(1).innerText(), locale === "zh" ? /本轮时限: 未记录/ : /Turn time limit: Not recorded/);
      await noOverflow(); await shot("unconfirmed", page.locator(".draft-technical-identity"));
      await page.locator(".agent-steps button").nth(2).click();
      assert.equal(await button("发布为未启用版本", "Publish inactive").isDisabled(), true);
      await button("返回定义并确认技术 ID", "Return to confirm technical ID").click();
      await page.locator(".draft-basic-grid input").first().fill("Synthetic unsaved edit");
      assert.equal(await identity.isDisabled(), true);
      assert.equal(await oldTurn.getByRole("button").isDisabled(), true);
      await button("放弃未保存修改", "Discard unsaved edits").click();
      await eventually(() => identity.isEnabled(), true, "Discard restores identity controls");
      await identity.fill("CON"); assert.equal(await button("检查可用性", "Check availability").isDisabled(), true);
      await identity.fill("synthetic-taken"); await button("检查可用性", "Check availability").click();
      await page.getByText(tr("该技术 ID 已被占用，请换一个名称并重新检查。", "This technical ID is already taken. Choose another name and check again."), { exact: true }).waitFor();
      await identity.fill(originalId); await button("检查可用性", "Check availability").click();
      await button("确认使用此 ID", "Confirm this ID").click();
      await eventually(() => trial.isEnabled(), true, "Confirmation opens trial gate");
      assert.equal(state.draft.revision, 1, "Same-ID confirmation preserves revision and validation");
      await identity.fill(renamedId); await button("检查可用性", "Check availability").click();
      await button("确认改名并重新验证", "Confirm rename and revalidate").click();
      await eventually(() => identity.inputValue(), renamedId, "Rename is reflected in the editor");
      await page.getByText(tr("技术 ID 已更新并确认。已保存新修订，需重新验证；历史对话保持不变，后续修改基于当前 ID。", "Technical ID changed and confirmed. A new revision requires fresh validation. Conversation history is preserved; future changes use the current ID."), { exact: true }).waitFor();
      assert.equal(state.draft.revision, 2); assert.equal(state.draft.acceptance.verdict, "NOT_TESTED");
      assert.deepEqual(state.identityWrites, [{ expectedRevision: 1, agentId: originalId }, { expectedRevision: 1, agentId: renamedId }]);
      assert.equal(await page.locator(".draft-audit-entry").count(), 2);
      assert.match(await oldTurn.innerText(), new RegExp(originalId));
      await noOverflow(); await shot("renamed", page.locator(".draft-technical-identity"));
      await oldTurn.getByRole("button", { name: tr("重试这条意见", "Retry this feedback"), exact: true }).click();
      assert.equal(await composer.inputValue(), requestText);
      assert.equal(await composer.evaluate((node) => node === document.activeElement), true);
      assert.match(await page.locator(".draft-retry-context").innerText(), new RegExp(renamedId));
      assert.equal(state.feedbackRequests.length, 0, "Retry only prefills, never submits automatically");
      await shot("retry-prefilled", composer);
      state.now = await page.evaluate(() => Date.now());
      await button("确认发送重试", "Confirm retry").click();
      await button("重传原请求", "Resend original request").waitFor();
      assert.equal(await composer.inputValue(), requestText);
      await button("重传原请求", "Resend original request").click();
      await eventually(() => composer.isDisabled(), true, "Running feedback locks the composer");
      assert.equal(state.feedbackRequests.length, 2);
      assert.deepEqual(state.feedbackRequests[0], state.feedbackRequests[1], "A lost response retransmits the same complete payload");
      assert.equal(state.feedbackRequests[1].retryOfTurn, 2);
      assert.equal(state.feedbackRequests[1].baseRevision, 2);
      assert.equal(state.feedbackRequests[1].baseTurn, 4);
      assert.notEqual(state.feedbackRequests[1].requestId, "synthetic-old-request");
      assert.match(state.feedbackRequests[1].requestId, /^[0-9a-f-]{36}$/);
      assert.equal(await identity.isDisabled(), true);
      assert.equal(await oldTurn.getByRole("button").isDisabled(), true);
      const running = page.locator(".draft-chat-entry").nth(2);
      await running.waitFor(); // Busy state can precede the refreshed conversation payload.
      assert.match(await running.innerText(), locale === "zh" ? /耗时: 2分5秒/ : /Elapsed: 2m 5s/);
      assert.match(await running.innerText(), locale === "zh" ? /本轮时限: 1小时0分0秒/ : /Turn time limit: 1h 0m 0s/);
      await page.clock.runFor(2000);
      assert.match(await running.innerText(), locale === "zh" ? /耗时: 2分7秒/ : /Elapsed: 2m 7s/);
      await page.reload(); await running.waitFor();
      assert.match(await running.innerText(), locale === "zh" ? /耗时: 2分7秒/ : /Elapsed: 2m 7s/);
      await noOverflow(); await shot("running", running);
      await page.clock.fastForward(3600000);
      await page.getByText(tr("已到记录的截止时间，正在等待服务确认状态；请勿重复发送。", "The recorded deadline has passed. Waiting for the server to confirm the status; do not send a duplicate request."), { exact: true }).waitFor();
      assert.equal(await composer.isDisabled(), true, "Client deadline never unlocks the draft");
      state.draft.active_operation.status = "cancelling";
      const failed = state.draft.conversation.at(-1); failed.status = "failed"; failed.completed_at = iso(state.now + 3475000);
      failed.decision.error_code = "agent_feedback_cleanup_failed"; failed.decision.execution.elapsed_seconds = 3600;
      await page.reload(); await running.waitFor();
      assert.match(await running.innerText(), locale === "zh" ? /重启 API 服务/ : /Restart the API service/);
      assert.equal(await composer.isDisabled(), true); assert.equal(await running.getByRole("button").isDisabled(), true);
      assert.equal(await button("取消任务", "Cancel task").isDisabled(), true);
      assert.match(await oldTurn.innerText(), locale === "zh" ? /本轮时限: 3分0秒/ : /Turn time limit: 3m 0s/);
      await noOverflow(); await shot("cleanup-locked", running);
      state.draft = fixture(); state.draft.technical_identity = { kind: "version_upgrade", agent_id: originalId, confirmed: true, locked: true, can_rename: false, blockers: [] };
      await page.reload(); await page.locator(".draft-technical-identity").waitFor();
      assert.equal(await identity.count(), 0, "Version upgrades never expose a rename input");
      assert.equal(await trial.isEnabled(), true);

      await page.goto(pageUrl(locale, "settings"));
      const modelRow = page.locator(".sdk-model-row").first(); await modelRow.waitFor();
      const effort = modelRow.locator("select");
      assert.equal(await effort.inputValue(), "high");
      assert.deepEqual(await effort.locator("option").evaluateAll((items) => items.map((item) => item.value)), ["low", "medium", "high", "xhigh"]);
      await effort.selectOption("low");
      const selectModel = modelRow.getByRole("button", { name: tr("设为默认模型", "Set as default model"), exact: true });
      assert.equal(await selectModel.isDisabled(), true);
      let releaseCheck; state.modelCheckGate = new Promise((resolve) => { releaseCheck = resolve; });
      await modelRow.getByRole("button", { name: tr("检查兼容性", "Check compatibility"), exact: true }).click();
      await eventually(() => effort.isDisabled(), true, "Checks lock the entire model row");
      assert.equal(await modelRow.getAttribute("aria-busy"), "true");
      releaseCheck(); state.modelCheckGate = null;
      await eventually(() => effort.isEnabled(), true, "Check completion restores controls");
      assert.deepEqual(state.modelChecks, [{ model_id: "synthetic-model", reasoning_effort: "low" }]);
      assert.equal(await effort.inputValue(), "low", "Checking an unsaved effort preserves that choice");
      await modelRow.getByRole("button", { name: tr("保存推理强度", "Save reasoning effort"), exact: true }).click();
      await page.getByText(tr("推理强度已保存；新会话和该模型草稿的下一轮修改生效，正在运行的轮次不变。", "Reasoning effort saved for new sessions and the next Agent editing turn using this model; running turns are unchanged."), { exact: true }).waitFor();
      assert.deepEqual(state.effortWrites, [{ reasoning_effort: "low" }]);
      await page.reload(); await modelRow.waitFor(); assert.equal(await effort.inputValue(), "low");
      await selectModel.click();
      await eventually(() => state.defaultModel, "synthetic-model", "Default selection keeps saved model effort");
      await modelRow.locator(".sdk-model-badge.is-selected").waitFor();
      await eventually(() => selectModel.count(), 0, "The refreshed row reflects the selected model");
      assert.deepEqual(state.modelWrites, [{ model_id: "synthetic-model" }]);
      assert.equal(state.effort, "low");
      await noOverflow(); await shot("reasoning", modelRow);
      assert.deepEqual(errors, []);
      assert.deepEqual(violations, []);
      results.push({ locale, width, height, status: "PASS", checks: ["unconfirmed gates with static checks allowed", "collision", "same-ID confirmation", "rename invalidation", "dirty/active locks", "retry focus/confirmation", "network idempotency", "running/refresh/historical timing", "cleanup lock", "upgrade immutable", "model effort check/save/reload", "responsive layout"] });
      console.log(`PASS isolated identity/feedback/settings ${locale} ${width}x${height}`);
    } catch (error) {
      await shot("failure").catch(() => {});
      results.push({ locale, width, height, status: "FAIL", error: error.message, page_errors: errors });
      throw error;
    } finally { await context.close(); }
  }
} finally {
  await browser.close();
  await writeFile(new URL("result.json", output), JSON.stringify({ scenarios: results.length, api_requests_mocked: apiRequests, api_requests_forwarded: 0, sap_calls: 0, model_calls: 0, violations, results }, null, 2));
}
console.log(`PASS ${results.length} isolated browser scenarios; ${apiRequests} API requests mocked; zero API/SAP/model requests forwarded. Artifacts: ${fileURLToPath(output)}`);
