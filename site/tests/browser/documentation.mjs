// Local documentation/layout acceptance only. Never submit a run or call SAP.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { loadAgentCatalog } from "../../scripts/generate-agent-catalog.mjs";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.SAPBA_PLAYWRIGHT_MODULE || "playwright");
const origin = process.env.SAPBA_SITE_URL;
if (!origin || new URL(origin).hostname !== "127.0.0.1") throw new Error("An explicit isolated localhost preview is required");
const output = new URL("../../../.local-data/ui/documentation/", import.meta.url);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: "msedge", headless: true });
const results = [];
try {
  const agents = loadAgentCatalog(undefined, { includeInactive: false });
  for (const locale of ["zh", "en"]) {
    for (const width of [1440, 772, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      await context.route("**/*", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        if (!['http:', 'https:'].includes(url.protocol)) return route.continue();
        if (url.hostname !== "127.0.0.1" || request.method() !== "GET") return route.abort();
        return route.continue();
      });
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", e => errors.push(e.message));
      for (const agent of agents) {
        const response = await page.goto(`${origin}/${locale}/agents/${agent.module}/${agent.slug}/`, { waitUntil: "networkidle" });
        assert.equal(response.status(), 200);
        const profile = page.locator("#agent-profile");
        if (agent.validation?.documentationReuse) {
          assert.ok((await profile.innerText()).includes(locale === "zh" ? "复用原版本验收" : "Original acceptance reused"));
        }
        if (agent.validation) {
          assert.ok((await profile.innerText()).includes(agent.version));
          if (locale === "zh") assert.ok(!(await profile.innerText()).includes("Live acceptance passed"));
        }
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${locale}/${agent.slug} overflow at ${width}`);
        for (const step of agent.workflow) {
          assert.ok((await page.locator("#workflow-tools").innerText()).includes(step.title[locale]), agent.slug + ':' + step.id);
        }
        if (["ap-payment", "budget-rolling-forecast", "intelligent-sourcing-rfq"].includes(agent.slug)) {
          await page.locator("#agent-profile").scrollIntoViewIfNeeded();
          await page.screenshot({ path: new URL(`${locale}-${width}-${agent.slug}.png`, output).pathname.replace(/^\/([A-Z]:)/, "$1") });
        }
        results.push({ locale, width, agent: agent.slug, version: agent.version, status: "PASS" });
      }
      assert.deepEqual(errors, []);
      await context.close();
    }
  }
} finally {
  await browser.close();
  await writeFile(new URL("result.json", output), JSON.stringify({ checks: results.length, sap_calls: 0, results }, null, 2));
}
console.log(`Documentation browser checks: ${results.length} passed; no run submitted.`);
