// Synthetic API/data only. No listening server, SDK, authentication or SAP call.
import assert from "node:assert/strict";
import {readFile, mkdir, writeFile} from "node:fs/promises";
import {createRequire} from "node:module";
import path from "node:path";
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.SAPBA_PLAYWRIGHT_MODULE || "playwright");
const buildRoot = path.resolve(process.env.SAPBA_BROWSER_DIST);
const destination = process.env.SAPBA_BROWSER_REPORT;
assert.ok(destination, "Fresh isolated report directory required");
await mkdir(destination, {recursive: false});
const browser = await chromium.launch({headless: true, channel: process.platform === "win32" ? "msedge" : undefined});
const results = [];
try {
  for (const locale of ["zh", "en"]) for (const width of [1440, 550, 390]) {
    const context = await browser.newContext({viewport: {width, height: 900}});
    const page = await context.newPage();
    await page.addInitScript(() => {
      const common = {installed: true, platform_supported: true, current_version: "synthetic", cli_version: "synthetic",
        check_status: "checked", blockers: [], capabilities: [], update_enabled: false, model_discovery_supported: true};
      const authentication = {status: "authenticated", authenticated: true, capabilities: {check: true, login: true, logout: true, switch_account: true},
        blockers: [], method: "browser", source: "official_cli"};
      const codex = {...common, provider_id: "codex", name: {zh: "Codex", en: "Codex"}, enabled: true, selected: true, selectable: true, can_enable: true, authentication};
      const wb = {...common, provider_id: "workbuddy", name: {zh: "WorkBuddy", en: "WorkBuddy"}, enabled: false, selected: false,
        selectable: false, can_enable: false, authenticated: true, authentication, default_model_id: "deepseek-v4.1-flash",
        service_site: "china", service_site_revision: 1, service_site_digest: "a".repeat(64)};
      const makeQualification = (model, pass) => ({model, effective_pass_count: pass ? 1 : 0, historical_pass_count: pass ? 1 : 0,
        legacy_pass_count: 13, operations: {sample_discovery: {status: pass ? "validated" : "unverified", permission_modes: pass ? ["bounded"] : [],
          required_permission_modes: ["bounded"], validation_origins: pass ? {bounded: {kind: "reuse"}} : {},
          reason: pass ? null : "workbuddy_qualification_legacy_binding_missing", change_reasons: ["dependency:sample_scope_graph.py"]}}});
      window.syntheticWrites = [];
      window.fetch = async (url, options = {}) => {
        if (options.method && options.method !== "GET") window.syntheticWrites.push(url);
        const payload = url.includes("/workbuddy/models") ? {items: ["deepseek-v4.1-flash", "hy4-preview"].map((id, index) => ({
          model_id: id, display_name: id, selected: index === 0, selectable: true, check_status: "compatible",
          business_evaluation: {required_operation_count: 15},
          qualification: makeQualification(id, index === 0)})), catalog_status: "incomplete"}
          : url.includes("/models") ? {items: [], catalog_status: "incomplete"}
          : {items: [codex, wb], default_provider_id: "codex"};
        return {ok: true, json: async () => payload};
      };
    });
    const html = await readFile(path.join(buildRoot, locale, "settings/index.html"), "utf8");
    await page.route("**/*", async route => {
      const url = new URL(route.request().url());
      if (url.origin !== "http://127.0.0.1:4339") return route.abort();
      if (url.pathname === "/") return route.fulfill({contentType: "text/html", body: html});
      const file = path.resolve(buildRoot, url.pathname.replace(/^\/SAPBusinessAgents\//, "").replace(/^\//, ""));
      if (!file.startsWith(buildRoot + path.sep)) return route.abort();
      try {return route.fulfill({body: await readFile(file), contentType: file.endsWith(".css") ? "text/css" : file.endsWith(".js") ? "text/javascript" : "application/octet-stream"});}
      catch {return route.abort();}
    });
    await page.goto("http://127.0.0.1:4339/");
    await page.locator('[data-runtime-tab="workbuddy"]').waitFor({state:'attached'});
    if(width<=820) await page.locator('[data-runtime-picker]').selectOption('workbuddy');
    else await page.locator('[data-runtime-tab="workbuddy"]').click();
    const wb = page.locator(".sdk-card").filter({has: page.getByRole("heading", {name: "WorkBuddy", exact: true})});
    const picker = wb.getByRole("combobox", {name: locale === "zh" ? "WorkBuddy · 选择模型" : "WorkBuddy · Choose model", exact: true});
    await picker.waitFor();
    await wb.locator('.settings-maintenance > summary').click();
    const details = wb.locator(".settings-maintenance > .sdk-technical-details").last();
    await details.locator("summary").focus(); await page.keyboard.press("Enter");
    assert.match(await details.textContent(), /deepseek-v4\.1-flash/);
    assert.match(await details.textContent(), /1\/15/);
    assert.match(await details.textContent(), locale === "zh" ? /审计复用/ : /audited reuse/);
    await picker.focus(); await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter");
    assert.equal(await picker.inputValue(), "hy4-preview");
    assert.match(await details.textContent(), /0\/15/);
    assert.doesNotMatch(await details.textContent(), /deepseek-v4\.1-flash/);
    assert.equal(await page.evaluate(() => window.syntheticWrites.length), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await details.scrollIntoViewIfNeeded();
    await page.screenshot({path: path.join(destination, `${locale}-${width}.png`), fullPage: true});
    results.push({locale, width, keyboard: true, model_isolation: true, legacy_not_eligible: true, no_overflow: true, zero_writes: true});
    await context.close();
  }
} finally {await browser.close();}
await writeFile(path.join(destination, "report.json"), JSON.stringify(results, null, 2));
console.log(JSON.stringify(results));
