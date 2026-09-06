// Optional browser regression against the local preview; never executes SAP queries.
// Set SAPBA_PLAYWRIGHT_MODULE to an installed playwright package if it is not local.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.SAPBA_PLAYWRIGHT_MODULE || "playwright");
const origin = process.env.SAPBA_SITE_URL || "http://127.0.0.1:4321";
const output = fileURLToPath(new URL("../../../.local-data/ui/agent-management/", import.meta.url));
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: process.env.SAPBA_BROWSER_CHANNEL || "msedge", headless: true });
const errors = [];
const attach = (page) => page.on("pageerror", (error) => errors.push(error.message));
const waitCount = (page, n) => page.waitForFunction((count) => document.querySelectorAll(".agent-list-item").length === count, n);
try {
  for (const locale of ["zh", "en"]) {
    for (const [width, height] of [[1440, 900], [772, 698], [390, 844]]) {
      const context = await browser.newContext({ viewport: { width, height } });
      const page = await context.newPage(); attach(page);
      await page.goto(`${origin}/${locale}/agent-management/`);
      await page.locator(".agent-list-item").first().waitFor();
      assert.equal(await page.locator(".agent-tabs").count(), 0);
      const filters = page.locator(".agent-filter-bar select");
      assert.equal(await filters.count(), 3);
      await filters.nth(0).selectOption("SD");
      await filters.nth(1).selectOption("active");
      await filters.nth(2).selectOption("BLOCKED");
      await page.locator(".agent-list-item").first().waitFor();
      const count = await page.locator(".agent-list-item").count();
      assert.ok(count > 0);
      for (const item of await page.locator(".agent-list-item").all()) {
        assert.equal((await item.locator(".agent-list-row > div").nth(0).textContent()).trim(), "SD");
      }
      const create = page.locator(".agent-create-link");
      const box = await create.boundingBox();
      const header = await page.locator(".agent-management-heading > div").boundingBox();
      if (width > 820) assert.ok(box.x > header.x + header.width - 1);
      else assert.ok(box.y >= header.y + header.height - 1);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
      await page.screenshot({ path: `${output}/${locale}-${width}.png` });
      await page.locator(".agent-list-actions button").first().click();
      await page.getByRole("button", { name: locale === "zh" ? "返回目录" : "Back to catalog", exact: true }).click();
      assert.equal(await filters.nth(0).inputValue(), "SD");
      assert.equal(await filters.nth(2).inputValue(), "BLOCKED");
      await waitCount(page, count);
      await page.reload();
      await page.locator(".agent-list-item").first().waitFor();
      assert.equal(await filters.nth(0).inputValue(), "");
      assert.equal(await create.getAttribute("href"), `/${locale}/ask/`);
      await create.click();
      await page.waitForURL(`**/${locale}/ask/`);
      console.log(`PASS live UI ${locale} ${width}x${height}: filters, layout, return, refresh and create link`);
      await context.close();
    }
  }

  const context = await browser.newContext({ viewport: { width: 772, height: 698 } });
  const page = await context.newPage(); attach(page);
  const catalog = [
    { id: "same", module: "SD", title: { zh: "同名", en: "Same" }, version: "1.0.0", lifecycle: { state: "active" }, validation: { verdict: "BLOCKED" } },
    { id: "inactive", module: "FI", title: { zh: "已停用示例", en: "Inactive" }, lifecycle: { state: "inactive" }, validation: { verdict: "PASS" } },
  ];
  let drafts = [
    { draft_id: "draft_same", agent_id: "same", module: "SD", title: { zh: "同名", en: "Same" }, status: "validated", target_version: "1.1.0", revision: 1, validation: { verdict: "NOT_TESTED" }, management: { can_delete: true } },
    { draft_id: "draft_pending", agent_id: "pending", module: "FI", title: { zh: "验证示例", en: "Pending" }, status: "validating", revision: 1, validation: { verdict: "pending", status: "running" } },
  ];
  let catalogFail = false, draftFail = false, draftGets = 0;
  await context.route("**/api/agents/catalog?*", (route) => route.fulfill({ status: catalogFail ? 503 : 200, json: catalogFail ? { detail: "Catalog unavailable" } : catalog }));
  await context.route("**/api/authoring/agents?*", (route) => { draftGets++; return route.fulfill({ status: draftFail ? 503 : 200, json: draftFail ? { detail: "Drafts unavailable" } : drafts }); });
  await context.route("**/api/authoring/agents/draft_same", (route) => route.fulfill({ json: { ...drafts[0], package: { manifest: { slug: "same", version: "1.1.0" }, readme: "Review", rules: null }, revisions: [], conversation: [] } }));
  await page.goto(`${origin}/zh/agent-management/`); await waitCount(page, 4);
  const filters = page.locator(".agent-filter-bar select");
  assert.deepEqual(await page.locator('[data-row-key$="same"]').evaluateAll((elements) => elements.map((element) => element.dataset.rowKey)), ["agent:same", "draft:draft_same"]);
  await filters.nth(1).selectOption("unpublished"); await filters.nth(2).selectOption("NOT_TESTED"); await waitCount(page, 1);
  assert.match(await page.locator(".agent-list-item").textContent(), /已验证/);
  await page.locator(".agent-list-actions button").first().click();
  await page.getByRole("button", { name: "返回目录", exact: true }).click();
  assert.equal(await filters.nth(2).inputValue(), "NOT_TESTED");
  await page.getByRole("button", { name: "重置筛选", exact: true }).click(); await waitCount(page, 4);
  draftFail = true; await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page.getByText("数据可能不是最新", { exact: false }).waitFor();
  await waitCount(page, 4);
  assert.match(await page.locator(".agent-result-count").textContent(), /数据不完整/);
  await page.reload(); await waitCount(page, 2); // failed initial drafts are not treated as empty
  draftFail = false; await page.getByRole("button", { name: "重试", exact: true }).click(); await waitCount(page, 4);
  catalogFail = true; draftFail = true; await page.reload();
  await page.getByText("目录和草稿均加载失败，请重试。", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "重试", exact: true }).count(), 2);
  catalogFail = false; draftFail = false;
  await page.clock.install(); await page.reload(); await waitCount(page, 4);
  await filters.nth(2).selectOption("PENDING"); await waitCount(page, 1);
  const beforePoll = draftGets;
  await page.clock.runFor(5100);
  await page.waitForFunction(() => !document.querySelector(".agent-resource-status").textContent.includes("正在加载"));
  assert.ok(draftGets > beforePoll);
  await page.evaluate(() => { Object.defineProperty(document, "hidden", { configurable: true, value: true }); document.dispatchEvent(new Event("visibilitychange")); });
  const beforeHidden = draftGets;
  await page.clock.runFor(16000);
  assert.equal(draftGets, beforeHidden);
  drafts = drafts.map((item) => item.draft_id === "draft_pending" ? { ...item, status: "needs_review", validation: { status: "completed", verdict: "INCONCLUSIVE" } } : item);
  await page.evaluate(() => { Object.defineProperty(document, "hidden", { configurable: true, value: false }); document.dispatchEvent(new Event("visibilitychange")); });
  await waitCount(page, 0);
  await page.getByText("验收或状态已更新，部分记录已移出当前筛选。", { exact: true }).waitFor();
  assert.equal(await filters.nth(0).locator("option").count(), 3); // options do not shrink with filtered results
  console.log("PASS simulated UI: drafts, partial/both failure, retry, stale data, visibility pause and terminal synchronization");
  await context.close();
  for (const sourceKind of ["session", "run"]) {
    const context = await browser.newContext({ viewport: { width: 772, height: 698 } });
    const page = await context.newPage(); attach(page);
    let generated = false, imported = false, generations = 0, imports = 0;
    const managed = { draft_id: "agent_draft_ui", agent_id: "generated-example", module: "Common", title: { zh: "接入示例", en: "Imported example" }, status: "needs_review", validation: { verdict: "NOT_TESTED" }, revision: 1, target_version: "0.1.0", source_type: "free_query", management: { can_delete: true } };
    await context.route("**/api/**", (route) => {
      const request = route.request(), path = new URL(request.url()).pathname;
      if (path.endsWith("/agent-draft") || path.endsWith("/create-agent-draft")) {
        assert.equal(request.method(), "POST"); generated = true; generations++;
        return route.fulfill({ json: { draft_id: "draft_ui", status: "validated", managed_draft_id: null, management_import_status: "failed" } });
      }
      if (path.endsWith("/import-to-management")) {
        assert.equal(request.method(), "POST"); assert.ok(generated); imports++; imported = true;
        return route.fulfill({ json: { draft_id: "draft_ui", managed_draft_id: managed.draft_id, management_import_status: "complete" } });
      }
      assert.equal(request.method(), "GET", `Unexpected mutation ${path}`);
      if (path === "/api/free-query-sessions/session_ui") return route.fulfill({ json: { session_id: "session_ui", original_query: "UI test", status: generated ? "draft_created" : "satisfied", draft_id: generated ? "draft_ui" : null, managed_draft_id: imported ? managed.draft_id : null, current_iteration: 1, iterations: [{ iteration: 1, status: "completed", run_id: "run_ui", result_digest: "test_digest" }] } });
      if (path === "/api/runs/run_ui") return route.fulfill({ json: { run_id: "run_ui", mode: "free_query", status: "completed", plan: { steps: [] }, result: null } });
      if (path.endsWith("/events")) return route.fulfill({ contentType: "text/event-stream", body: 'event: run_completed\ndata: {}\n\n' });
      if (path === "/api/agents/catalog") return route.fulfill({ json: [] });
      if (path === "/api/authoring/agents") return route.fulfill({ json: imported ? [managed] : [] });
      if (path === `/api/authoring/agents/${managed.draft_id}`) return route.fulfill({ json: { ...managed, package: { manifest: { slug: managed.agent_id, version: "0.1.0" }, readme: "Review source", rules: null }, revisions: [], conversation: [] } });
      return route.fulfill({ status: 404, json: {} });
    });
    await page.goto(`${origin}/zh/run/?${sourceKind === "session" ? "session=session_ui" : "run=run_ui"}`);
    await page.locator(sourceKind === "session" ? "[data-session-draft]" : "[data-draft]").click();
    await page.getByText("草稿已生成，接入管理中心失败", { exact: false }).waitFor();
    await page.locator("[data-import-draft]").click();
    await page.locator("[data-managed-draft-link]").click();
    await page.getByRole("button", { name: "返回目录", exact: true }).waitFor();
    await page.getByRole("button", { name: "返回目录", exact: true }).click();
    await waitCount(page, 1);
    assert.match(await page.locator(".agent-list-item").textContent(), /需要复核/);
    assert.match(await page.locator(".agent-list-item").textContent(), /未测试/);
    assert.equal(generations, 1); assert.equal(imports, 1);
    console.log(`PASS simulated ${sourceKind}: generation, failed import, retry without regeneration and management entry`);
    await context.close();
  }
  assert.deepEqual(errors, []);
} finally { await browser.close(); }
