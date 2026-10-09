// Isolated browser acceptance: all API calls are synthetic and intercepted.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.SAPBA_PLAYWRIGHT_MODULE || "playwright");
const origin = new URL(process.env.SAPBA_SITE_URL || "http://127.0.0.1:4337/SAPBusinessAgents");
if (origin.protocol !== "http:" || origin.hostname !== "127.0.0.1") throw new Error("Isolated localhost preview required");
const base = origin.href.replace(/\/$/, "");
const output = new URL("../../../.local-data/ui/query-history/", import.meta.url);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, ...(process.env.SAPBA_BROWSER_CHANNEL ? { channel: process.env.SAPBA_BROWSER_CHANNEL } : {}) });
const violations = [], results = [];
try {
  for (const locale of ["zh", "en"]) {
    const state = { days: 30, writes: [], changed: false, unavailable: false, rejectDraft: false };
    const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
    await context.route("**/*", async (route) => {
      const request = route.request(), url = new URL(request.url()), path = url.pathname;
      const reply = (value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value), headers: { "Access-Control-Allow-Origin": origin.origin } });
      if (request.method() === "OPTIONS" && path.startsWith("/api/")) return route.fulfill({ status: 204, headers: { "Access-Control-Allow-Origin": origin.origin, "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS", "Access-Control-Allow-Headers": "Content-Type" } });
      if (!path.startsWith("/api/")) {
        if (url.origin === origin.origin && request.method() === "GET") {
          if (path.endsWith("/agent-management/") || path.endsWith("/run/")) return route.fulfill({ contentType: "text/html", body: "<h1>Synthetic destination</h1>" });
          return route.continue();
        }
        violations.push(request.url()); return route.abort();
      }
      if (request.method() !== "GET") state.writes.push({ path, body: request.postDataJSON() });
      if (path === "/api/free-query-sessions" && request.method() === "GET") {
        if (state.unavailable) return reply({ detail: "Synthetic offline state" }, 503);
        const offset = Number(url.searchParams.get("offset"));
        const all = Array.from({ length: 23 }, (_, i) => ({ history_id: `session-${i}`, session_id: `session-${i}`, run_id: `run-${i}`,
          query: i === 0 ? "<img src=x onerror=window.__historyXss=true> Synthetic question" : `Synthetic question ${i}`,
          iteration: i === 0 ? 3 : 1, status: i === 0 ? "failed" : "completed", activity_at: "2030-01-01T00:00:00Z", expires_at: "2030-01-31T00:00:00Z",
          can_create_draft: i !== 0, draft_blocker: i === 0 ? "query_not_successful" : null, draft_id: null, managed_draft_id: null }));
        return reply({ items: all.slice(offset, offset + 20), total: 23, retention_days: state.days });
      }
      if (path === "/api/system/free-query-history") {
        if (request.method() === "PUT") state.days = request.postDataJSON().retention_days;
        return reply({ retention_days: state.days });
      }
      if (path === "/api/system/sdk-runtimes") return reply({ items: [], default_provider_id: null });
      if (path === "/api/runs" && request.method() === "POST") {
        const body = request.postDataJSON();
        assert.equal(body.query, "Synthetic original question\n\nSynthetic follow-up");
        assert.equal(body.mode, "free_query"); assert.equal(body.session_id, undefined); assert.equal(body.sessionId, undefined);
        return reply({ run_id: "run-retry-new", session_id: "session-retry-new" }, 202);
      }
      if (path.endsWith("/retry-query")) return reply({ query: "Synthetic original question\n\nSynthetic follow-up" });
      if (/^\/api\/free-query-sessions\/session-\d+$/.test(path)) return reply({ current_iteration: 1, can_create_draft: !state.changed, draft_blocker: state.changed ? "query_not_successful" : null,
        iterations: [{ iteration: 1, result_digest: "synthetic-digest", status: state.changed ? "failed" : "completed" }] });
      if (path.endsWith("/accept")) { assert.equal(request.postDataJSON().expectedResultDigest, "synthetic-digest"); return reply({ status: "satisfied" }); }
      if (path.endsWith("/agent-draft")) {
        if (state.rejectDraft) return reply({ detail: { code: "free_query_source_ineligible", blocker: "query_not_successful", message: "Synthetic state change" } }, 409);
        assert.equal(request.postDataJSON().module, "MM"); return reply({ draft_id: "draft-synthetic", managed_draft_id: "managed-synthetic" }, 201);
      }
      violations.push(`${request.method()} ${path}`); return reply({ detail: "Unexpected synthetic request" }, 500);
    });
    const page = await context.newPage();
    const errors = []; page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${base}/${locale}/ask/`);
    await page.locator("[data-query-history-link]").click();
    await page.locator("[data-history-rows] tr").first().waitFor();
    assert.equal(await page.locator("[data-history-rows] tr").count(), 20);
    assert.equal(await page.evaluate(() => window.__historyXss), undefined);
    assert.equal(await page.locator("[data-history-rows] img").count(), 0);
    await page.screenshot({ path: new URL(`${locale}-desktop.png`, output).pathname.replace(/^\/([A-Za-z]:)/, "$1"), fullPage: true });
    await page.locator("[data-history-next]").click();
    await page.waitForFunction(() => document.querySelectorAll("[data-history-rows] tr").length === 3);
    await page.locator("[data-history-prev]").click();
    await page.waitForFunction(() => document.querySelectorAll("[data-history-rows] tr").length === 20);
    await page.locator('[data-history-id="session-0"] button').click();
    await page.locator("[data-history-blocked][open]").waitFor();
    await page.locator("[data-history-abandon]").click();
    assert.equal(state.writes.length, 0);
    await page.locator('[data-history-id="session-0"] button').click();
    await page.locator("[data-history-retry]").click();
    await page.waitForFunction(() => document.querySelector("textarea[name=query]")?.value.includes("Synthetic follow-up"));
    assert.equal(state.writes.length, 0); assert.ok(page.url().endsWith("?retryHistory=session-0"));
    await page.locator("[data-free-query] button[type=submit]").click();
    await page.waitForURL(`**/${locale}/run/?run=run-retry-new&session=session-retry-new`);
    assert.equal(state.writes.length, 1); state.writes = [];
    await page.goto(`${base}/${locale}/query-history/`);
    await page.locator('[data-history-id="session-1"] button').click();
    state.changed = true;
    await page.locator("[data-history-confirm]").click();
    await page.locator("[data-history-blocked][open]").waitFor();
    assert.equal(state.writes.length, 0);
    await page.locator("[data-history-abandon]").click(); state.changed = false;
    await page.locator('[data-history-id="session-1"] button').click(); state.rejectDraft = true;
    await page.locator("[data-history-confirm]").click();
    await page.locator("[data-history-blocked][open]").waitFor();
    await page.locator("[data-history-abandon]").click(); state.rejectDraft = false; state.writes = [];
    await page.locator('[data-history-id="session-1"] button').click();
    await page.locator("[data-history-module]").selectOption("MM");
    await page.locator("[data-history-confirm]").click();
    await page.waitForURL(`**/${locale}/agent-management/?draft=managed-synthetic`);
    assert.equal(state.writes.length, 2);
    await page.goto(`${base}/${locale}/settings/`);
    await page.locator("[data-history-settings] input:enabled").waitFor();
    await page.locator("[data-history-settings] input").fill("60");
    await page.locator("[data-history-settings] button").click();
    await page.waitForFunction(() => document.querySelector("[data-history-settings-message]").textContent.length > 0);
    assert.equal(state.days, 60);
    await page.reload(); await page.locator("[data-history-settings] input:enabled").waitFor();
    assert.equal(await page.locator("[data-history-settings] input").inputValue(), "60");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${base}/${locale}/query-history/`); await page.locator("[data-history-rows] tr").first().waitFor();
    assert.equal(await page.locator("[data-history-rows] tr").count(), 20);
    await page.screenshot({ path: new URL(`${locale}-mobile.png`, output).pathname.replace(/^\/([A-Za-z]:)/, "$1"), fullPage: true });
    state.unavailable = true; await page.locator("[data-history-refresh]").click();
    await page.waitForFunction(() => document.querySelector("[data-history-table]").hidden);
    assert.ok(await page.locator("[data-history-message]").textContent());
    assert.deepEqual(errors, []); results.push(`${locale}: navigation, pagination, retry, stale result, draft conversion, retention, mobile, offline PASS`);
    await context.close();
  }
  assert.deepEqual(violations, []);
  console.log(results.join("\n"));
} finally { await browser.close(); }
