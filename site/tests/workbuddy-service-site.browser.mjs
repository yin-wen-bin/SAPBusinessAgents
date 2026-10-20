// Built UI with synthetic account/site APIs only. No SDK, login or model call.
import assert from "node:assert/strict";
import {readFile, mkdir, writeFile} from "node:fs/promises";
import {createRequire} from "node:module";
import path from "node:path";
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.SAPBA_PLAYWRIGHT_MODULE || "playwright");
const buildRoot = path.resolve(process.env.SAPBA_BROWSER_DIST);
const destination = process.env.SAPBA_BROWSER_REPORT;
assert.ok(destination, "Provide a fresh isolated report directory");
await mkdir(destination, {recursive: false});
const browser = await chromium.launch({headless: true, channel: process.platform === "win32" ? "msedge" : undefined});
const records = [];
try {
  for (const locale of ["zh", "en"]) for (const width of [1440, 550, 390]) {
    const context = await browser.newContext({viewport: {width, height: 900}});
    const page = await context.newPage();
    await page.addInitScript(() => {
      const capabilities = {check: true, login: true, logout: true, switch_account: true};
      const common = {installed: true, platform_supported: true, current_version: "synthetic", cli_version: "synthetic",
        check_status: "checked", blockers: [], capabilities: [], update_enabled: false, model_discovery_supported: true};
      const codex = {...common, provider_id: "codex", sdk_id: "codex-python-sdk", name: {zh: "Codex", en: "Codex"},
        enabled: true, selected: true, selectable: true, can_enable: true,
        authentication: {status: "authenticated", authenticated: true, capabilities, blockers: [], method: "browser", source: "official_cli"}};
      const saved = JSON.parse(sessionStorage.getItem("synthetic-site") || "null");
      const wb = {...common, provider_id: "workbuddy", sdk_id: "workbuddy-python-sdk", name: {zh: "WorkBuddy", en: "WorkBuddy"},
        enabled: false, selected: false, selectable: false, can_enable: false, service_site: "china", service_site_revision: 1,
        service_site_digest: "a".repeat(64), service_site_switch_allowed: true, ...saved,
        authentication: {status: "not_checked", authenticated: null, capabilities, blockers: [], method: "unknown", source: "unknown"}};
      window.syntheticWrites = [];
      window.fetch = async (url, options = {}) => {
        let payload;
        if (options.method === "PUT") {
          const body = JSON.parse(options.body);
          window.syntheticWrites.push({url, body});
          Object.assign(wb, {service_site: body.service_site, service_site_revision: body.expected_revision + 1,
            service_site_digest: "b".repeat(64)});
          sessionStorage.setItem("synthetic-site", JSON.stringify({service_site: wb.service_site,
            service_site_revision: wb.service_site_revision, service_site_digest: wb.service_site_digest}));
          payload = {service_site_configuration: wb};
        } else if (options.method === "POST") {
          window.syntheticWrites.push({url, body: options.body ? JSON.parse(options.body) : null});
          payload = {operation: {operation_id: "auth_synthetic", status: "completed", action: "login"}};
        } else if (url.includes("/models")) payload = {items: [], catalog_status: "ready", model_catalog_complete: true};
        else payload = {items: [codex, wb], default_provider_id: "codex"};
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
      try { return route.fulfill({body: await readFile(file), contentType: file.endsWith(".css") ? "text/css" : file.endsWith(".js") ? "text/javascript" : "application/octet-stream"}); }
      catch { return route.abort(); }
    });
    let confirmations = 0;
    page.on("dialog", async dialog => { confirmations++; await dialog.accept(); });
    await page.goto("http://127.0.0.1:4339/");
    await page.locator('[data-runtime-tab="workbuddy"]').waitFor({state:'attached'});
    if(width<=820) await page.locator('[data-runtime-picker]').selectOption('workbuddy');
    else await page.locator('[data-runtime-tab="workbuddy"]').click();
    const wb = page.locator(".sdk-card").filter({has: page.getByRole("heading", {name: "WorkBuddy", exact: true})});
    const codex = page.locator(".sdk-card").filter({has: page.getByRole("heading", {name: "Codex", exact: true})});
    const site = wb.getByRole("combobox", {name: locale === "zh" ? "服务站点" : "Service site"});
    await wb.locator('.settings-site-editor > summary').click();
    await site.waitFor();
    assert.equal(await codex.locator(".sdk-service-site").count(), 0);
    assert.equal(await page.evaluate(() => window.syntheticWrites.length), 0);
    await site.selectOption("international");
    assert.equal(await page.evaluate(() => window.syntheticWrites.length), 0);
    const save = wb.getByRole("button", {name: locale === "zh" ? "保存站点" : "Save site", exact: true});
    await save.focus(); await page.keyboard.press("Enter");
    await page.waitForFunction(() => window.syntheticWrites.length === 1);
    await page.waitForFunction(() => document.querySelector(".sdk-service-site select")?.value === "international");
    assert.equal(confirmations, 1);
    assert.equal(await page.evaluate(() => window.syntheticWrites[0].body.expected_revision), 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path: path.join(destination, `${locale}-${width}.png`), fullPage: true});
    await page.reload();
    await wb.locator('.settings-site-editor > summary').click();
    await site.waitFor(); assert.equal(await site.inputValue(), "international");
    const login = wb.getByRole("button", {name: locale === "zh" ? "登录" : "Sign in", exact: true});
    await login.focus(); await page.keyboard.press("Enter");
    await page.waitForFunction(() => window.syntheticWrites.length === 1);
    assert.equal(await page.evaluate(() => window.syntheticWrites[0].body.service_site_digest), "b".repeat(64));
    assert.equal(await page.evaluate(() => window.syntheticWrites.some(p => /models|enable|default/.test(p.url))), false);
    records.push({locale, width, keyboard: true, explicit_save: true, reload: true, no_overflow: true, codex_unchanged: true, synthetic_only: true});
    await context.close();
  }
} finally {await browser.close();}
await writeFile(path.join(destination, "report.json"), JSON.stringify(records, null, 2));
console.log(JSON.stringify(records));
