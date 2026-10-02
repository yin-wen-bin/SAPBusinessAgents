// Isolated draft assistant acceptance: composer, annotations, quotes, Diff and fallback.
// No SAP, model, or real draft is used.
// Run after `npm run build` with an Astro preview and SAPBA_SITE_URL pointing to that preview.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.SAPBA_PLAYWRIGHT_MODULE || "playwright");
const origin = new URL(process.env.SAPBA_SITE_URL || "http://127.0.0.1:4322/SAPBusinessAgents");
if (origin.protocol !== "http:" || origin.hostname !== "127.0.0.1") throw new Error("An isolated loopback preview is required");
const siteBase = origin.pathname.replace(/\/$/, "");
const draftId = "agent_draft_synthetic_assistant_actions";
const output = new URL(`../../../.local-data/ui/agent-assistant-actions/${new Date().toISOString().replace(/[:.]/g, "-")}/`, import.meta.url);
await mkdir(output, { recursive: true });

function fixture() {
  return {
    draft_id: draftId, agent_id: "synthetic-assistant-actions", module: "Common", revision: 1,
    status: "needs_review", target_version: "0.1.0", source_type: "blank",
    technical_identity: { kind: "new_agent", agent_id: "synthetic-assistant-actions", confirmed: true, locked: false, can_rename: true, blockers: [] },
    package: { manifest: {
      schemaVersion: 2, slug: "synthetic-assistant-actions", module: "Common", version: "0.1.0", owner: "Synthetic test",
      title: { zh: "合成助手草稿", en: "Synthetic assistant draft" },
      summary: { zh: "仅用于隔离界面测试", en: "Isolated browser test only" },
      inputs: { zh: [], en: [] }, outputs: { zh: [], en: [] }, workflow: [], sapModules: [],
      guardrails: { zh: [], en: [] }, execution: { inputSchema: { type: "object", properties: {}, required: [] } },
    }, readme: "Synthetic documentation", rules: null },
    acceptance: { verdict: "NOT_TESTED" }, publishability: { can_publish: false, blockers: [] },
    revisions: [{ revision: 1 }], active_operation: null, conversation: [], pending_clarifications: [],
  };
}

const browser = await chromium.launch({ channel: process.env.SAPBA_BROWSER_CHANNEL || "msedge", headless: true });
try {
  for (const locale of ["zh", "en"]) for (const width of [1440, 550, 390]) {
    const state = { draft: fixture(), requests: [], failFirst: true, violations: [], polls: 0, streams: 0 };
    const context = await browser.newContext({ viewport: { width, height: 844 }, serviceWorkers: "block" });
    await context.route("**/*", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (!url.pathname.includes("/api/")) {
        if (url.origin === origin.origin && request.method() === "GET") return route.continue();
        state.violations.push(`Non-static request: ${request.method()} ${url}`);
        return route.abort("blockedbyclient");
      }
      const path = url.pathname;
      const base = `/api/authoring/agents/${draftId}`;
      const headers = { "access-control-allow-origin": request.headers().origin || "*", "access-control-allow-methods": "GET, POST, PUT, OPTIONS", "access-control-allow-headers": "content-type, x-sapba-action" };
      const respond = (value) => route.fulfill({ status: 200, headers, json: structuredClone(value) });
      if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
      if (request.method() === "GET" && path === "/api/agents/catalog") return respond([]);
      if (request.method() === "GET" && path === "/api/authoring/agents") return respond([state.draft]);
      if (request.method() === "GET" && path === base) { state.polls += 1; return respond(state.draft); }
      if (request.method() === "GET" && path === `${base}/validation-report`) return respond({ acceptance: state.draft.acceptance, publishability: state.draft.publishability });
      if (request.method() === "GET" && path === `${base}/acceptance-campaigns`) return respond([]);
      if (request.method() === "GET" && path === `${base}/conversation/events`) {
        state.streams += 1;
        // Deliberately unavailable: the page must keep bounded polling alive.
        return route.fulfill({ status: 503, headers, body: "Synthetic disconnected stream" });
      }
      if (request.method() === "GET" && path === `${base}/diff`) return respond({ changes: state.draft.conversation.at(-1)?.diff || [] });
      if (request.method() === "PUT" && path === `${base}/ui-state`) return respond({});
      if (request.method() === "POST" && path === `${base}/feedback`) {
        const body = request.postDataJSON();
        state.requests.push(body);
        if (state.failFirst) { state.failFirst = false; return route.abort("failed"); }
        const turn = Math.max(0, ...state.draft.conversation.map((item) => item.turn)) + 1;
        const baseRevision = state.draft.revision;
        const diff = [];
        if (body.annotations?.length && body.intent === "revise") {
          for (const [lang, value] of [["zh", "改后的合成草稿"], ["en", "Revised synthetic draft"]]) {
            diff.push({ path: `/manifest/title/${lang}`, before: state.draft.package.manifest.title[lang], after: value, change: "modified", group: "basic" });
            state.draft.package.manifest.title[lang] = value;
          }
          state.draft.revision += 1;
          state.draft.revisions.push({ revision: state.draft.revision });
        }
        if (body.replyToClarificationId) state.draft.pending_clarifications = [];
        state.draft.conversation.push({ turn, kind: "feedback", status: "completed", user_message: body.feedback,
          base_revision: baseRevision, result_revision: state.draft.revision, diff,
          decision: { intent: body.intent, context: { step: body.step, annotations: body.annotations?.map((item) => ({ ...item, source_id: item.sourceId, source_digest: item.sourceDigest })) },
            reply_to_clarification_id: body.replyToClarificationId, assistant_message: { zh: "合成回复", en: "Synthetic reply" } } });
        return respond({ status: "completed", turn });
      }
      state.violations.push(`Unexpected API: ${request.method()} ${path}`);
      return route.fulfill({ status: 418, headers, json: { detail: { code: "synthetic_unexpected_api" } } });
    });
    if (typeof context.routeWebSocket !== "function") throw new Error("WebSocket interception is required");
    await context.routeWebSocket("**/*", (socket) => socket.close());
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const tr = (zh, en) => locale === "zh" ? zh : en;
    const button = (zh, en) => page.getByRole("button", { name: tr(zh, en), exact: true });
    try {
      await page.goto(`${origin.origin}${siteBase}/${locale}/agent-management/?draft=${draftId}&step=trial`);
      await button("打开 AI 助手", "Open AI assistant").click();
      const composer = page.locator("#draft-feedback");
      await composer.fill(tr("请解释此结果", "Explain this result"));
      const actions = page.locator(".draft-assistant-actions");
      assert.equal(await page.locator(".draft-assistant-modes").count(), 0);
      assert.equal(await actions.getByRole("button").count(), 2);
      assert.equal(await button("解释问题", "Explain issue").isEnabled(), true);
      assert.equal(await button("修改草稿", "Revise draft").isEnabled(), true);
      await actions.scrollIntoViewIfNeeded();
      await page.screenshot({ path: fileURLToPath(new URL(`${locale}-${width}-actions.png`, output)) });
      await composer.press("Tab");
      assert.equal(await button("解释问题", "Explain issue").evaluate((node) => node === document.activeElement), true);
      await page.keyboard.press("Enter");
      await page.locator("dialog.feedback-progress-dialog[open]").waitFor();
      assert.equal(state.requests[0].intent, "explain");
      await page.locator("dialog.feedback-progress-dialog[open]").getByRole("button", { name: tr("后台继续", "Continue in background") }).click();
      await button("重传原请求", "Resend original request").waitFor();
      assert.equal(await composer.isDisabled(), true);
      assert.equal(await button("修改草稿", "Revise draft").isDisabled(), true);
      await button("重传原请求", "Resend original request").click();
      await page.locator("dialog.feedback-progress-dialog[open]").waitFor();
      assert.deepEqual(state.requests[1], state.requests[0], "uncertain response must retransmit the same request and intent");
      await page.locator("dialog.feedback-progress-dialog[open]").getByRole("button", { name: tr("返回修改意见", "Return to feedback") }).click();
      await composer.fill(tr("请修改草稿", "Revise this draft"));
      await button("修改草稿", "Revise draft").click();
      await page.locator("dialog.feedback-progress-dialog[open]").waitFor();
      assert.equal(state.requests[2].intent, "revise");
      assert.notEqual(state.requests[2].requestId, state.requests[1].requestId);
      assert.equal(state.requests[2].retryOfTurn, undefined);
      await page.locator("dialog.feedback-progress-dialog[open]").getByRole("button", { name: tr("返回修改意见", "Return to feedback") }).click();
      state.draft.conversation.push({ turn: 3, kind: "feedback", status: "failed", user_message: "Retry the old revision",
        decision: { intent: "revise", error_code: "agent_feedback_timeout" } });
      await page.reload();
      await button("打开 AI 助手", "Open AI assistant").click();
      await page.locator(".draft-chat-entry").last().getByRole("button", { name: tr("重试这条意见", "Retry this feedback") }).click();
      assert.equal(await composer.inputValue(), "Retry the old revision");
      await button("解释问题", "Explain issue").click();
      await page.locator("dialog.feedback-progress-dialog[open]").waitFor();
      assert.equal(state.requests[3].intent, "explain");
      assert.equal(state.requests[3].retryOfTurn, undefined, "changing action starts a new request, not a retry");
      await page.locator("dialog.feedback-progress-dialog[open]").getByRole("button", { name: tr("返回修改意见", "Return to feedback") }).click();
      state.draft.conversation.push({ turn: 5, kind: "feedback", status: "failed", user_message: "Retry the old explanation",
        decision: { intent: "explain", error_code: "agent_feedback_timeout" } });
      await page.reload();
      await button("打开 AI 助手", "Open AI assistant").click();
      await page.locator(".draft-chat-entry").last().getByRole("button", { name: tr("重试这条意见", "Retry this feedback") }).click();
      await button("解释问题", "Explain issue").click();
      await page.locator("dialog.feedback-progress-dialog[open]").waitFor();
      assert.equal(state.requests[4].intent, "explain");
      assert.equal(state.requests[4].retryOfTurn, 5, "matching action retains the historical retry binding");
      await page.locator("dialog.feedback-progress-dialog[open]").getByRole("button", { name: tr("返回修改意见", "Return to feedback") }).click();
      await page.goto(`${origin.origin}${siteBase}/${locale}/agent-management/?draft=${draftId}&step=purpose`);
      await button("打开 AI 助手", "Open AI assistant").click();
      await button("标注控件", "Annotate controls").click();
      const chineseName = page.locator('[data-draft-ref="/manifest/title/zh"]');
      const englishName = page.locator('[data-draft-ref="/manifest/title/en"]');
      const beforeName = await chineseName.locator("input").inputValue();
      await chineseName.click();
      await englishName.click();
      const annotationList = page.locator(".draft-annotation-list");
      assert.equal(await annotationList.locator("li").count(), 2);
      if (width <= 820) await button("完成控件标注", "Finish annotating").click();
      await annotationList.getByRole("textbox").nth(0).fill(tr("修改中文名称", "Revise Chinese name"));
      await annotationList.getByRole("textbox").nth(1).fill(tr("修改英文名称", "Revise English name"));
      assert.equal(await chineseName.getAttribute("data-draft-annotation-number"), "1");
      assert.equal(await englishName.getAttribute("data-draft-annotation-number"), "2");
      assert.equal(await chineseName.locator("input").inputValue(), beforeName, "annotation click must not edit the field");
      await annotationList.getByRole("button", { name: tr("定位控件", "Locate control"), exact: true }).first().click();
      await page.waitForFunction(() => document.activeElement?.getAttribute("data-draft-ref") === "/manifest/title/zh");
      if (width <= 820 || await button("完成控件标注", "Finish annotating").isVisible()) await button("完成控件标注", "Finish annotating").click();
      await page.screenshot({ path: fileURLToPath(new URL(`${locale}-${width}-annotations.png`, output)) });
      await composer.fill(tr("按两条标注修改草稿", "Revise both annotated fields"));
      const beforeRevision = state.draft.revision;
      await button("修改草稿", "Revise draft").click();
      await page.locator("dialog.feedback-progress-dialog[open]").waitFor();
      assert.equal(state.requests.at(-1).annotations.length, 2);
      assert.equal(state.draft.revision, beforeRevision + 1);
      for (const item of state.requests.at(-1).annotations) assert.deepEqual(Object.keys(item).sort(), ["comment", "kind", "path", "revision"]);
      await page.locator("dialog.feedback-progress-dialog[open]").getByRole("button", { name: tr("关闭对话框，不取消任务", "Close dialog without cancelling"), exact: true }).click();
      assert.equal(await page.locator(".draft-unsaved").count(), 0, "assistant revisions must refresh a previously clean editor");
      await page.locator(".draft-turn-diff").last().locator("summary").click();
      assert.match(await page.locator(".draft-turn-diff").last().textContent(), /\/manifest\/title\/zh/);
      // A historical field reference must not be silently rebound to the new revision.
      await page.locator(".draft-chat-entry").last().getByRole("button", { name: "1. /manifest/title/zh", exact: true }).click();
      await page.getByText(tr("此对象属于旧修订，不能自动定位到新定义。请查看该回合的 Diff。", "This object belongs to an old revision and cannot be rebound. Review the turn Diff."), { exact: true }).waitFor();
      const requestCount = state.requests.length, pollCount = state.polls;
      state.draft.pending_clarifications = [{ id: "synthetic-choice", revision: state.draft.revision, state: "pending", answer_mode: "choice",
        question: { zh: "选择范围", en: "Choose scope" }, options: [{ id: "one", label: { zh: "范围一", en: "Scope one" } }, { id: "two", label: { zh: "范围二", en: "Scope two" } }] }];
      await button("范围二", "Scope two").waitFor({ timeout: 15000 });
      assert.ok(state.polls > pollCount && state.streams > 0, "disconnected SSE must fall back to polling");
      assert.equal(state.requests.length, requestCount, "reconnection must never resubmit feedback");
      await button("范围二", "Scope two").click();
      await button("解释问题", "Explain issue").click();
      await page.locator("dialog.feedback-progress-dialog[open]").waitFor();
      assert.equal(state.requests.at(-1).replyToClarificationId, "synthetic-choice");
      assert.equal(state.requests.at(-1).clarificationOptionId, "two");
      await page.locator("dialog.feedback-progress-dialog[open]").getByRole("button", { name: tr("返回修改意见", "Return to feedback") }).click();
      await page.goto(`${origin.origin}${siteBase}/${locale}/agent-management/?draft=${draftId}&step=logic`);
      await page.locator(".draft-advanced summary").click();
      const readme = page.locator(".draft-advanced label").filter({ hasText: "README" }).getByRole("textbox");
      await readme.focus();
      await readme.evaluate((element) => { element.setSelectionRange(0, 9); element.dispatchEvent(new Event("select", { bubbles: true })); });
      await button("打开 AI 助手", "Open AI assistant").click();
      await button("引用页面所选内容", "Quote selected content").click();
      assert.match(await page.locator(".draft-selected-excerpt").textContent(), /Synthetic/);
      await composer.fill(tr("解释选中的文档", "Explain the selected documentation"));
      await button("解释问题", "Explain issue").click();
      await page.locator("dialog.feedback-progress-dialog[open]").waitFor();
      assert.equal(state.requests.at(-1).selection.kind, "readme");
      assert.equal(state.requests.at(-1).selection.path, "/readme");
      assert.equal(await page.locator("#feedback-progress-title").textContent(), tr("解释请求已完成", "Explanation request completed"));
      assert.ok((await page.locator("dialog.feedback-progress-dialog").textContent()).includes(tr("草稿未修改", "the draft was not modified")));
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, "no horizontal overflow");
      await page.screenshot({ path: fileURLToPath(new URL(`${locale}-${width}.png`, output)) });
      assert.deepEqual(errors, []);
      assert.deepEqual(state.violations, []);
      console.log(`PASS assistant actions, annotations, Diff, choices, quotes, polling ${locale} ${width}px`);
    } catch (error) {
      await page.screenshot({ path: fileURLToPath(new URL(`${locale}-${width}-failed.png`, output)) });
      throw error;
    } finally { await context.close(); }
  }
} finally { await browser.close(); }
