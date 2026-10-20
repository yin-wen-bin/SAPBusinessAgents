// Built settings page, synthetic API only; never opens official login/SDKs.
import assert from "node:assert/strict";
import {readFile, mkdir, writeFile} from "node:fs/promises";
import {createRequire} from "node:module";
import path from "node:path";
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.SAPBA_PLAYWRIGHT_MODULE || "playwright");
const destination = process.env.SAPBA_BROWSER_REPORT;
assert.ok(destination, "Provide a fresh isolated report directory");
await mkdir(destination, {recursive: false});
const buildRoot = path.resolve(process.env.SAPBA_BROWSER_DIST || "../.local-data/site-auth-build");
const styles = await readFile("src/styles/global.css", "utf8");
const browser = await chromium.launch({headless: true, channel: process.platform === "win32" ? "msedge" : undefined});
const records = [];
try {
  for (const locale of ["zh", "en"]) for (const width of [1440, 550, 390]) {
    const context = await browser.newContext({viewport: {width, height: 900}});
    const page = await context.newPage();
    await page.addInitScript(({locale}) => {
      const caps = {check: true, login: true, logout: true, switch_account: true};
      const sdk = {provider_id: "codex", sdk_id: "codex-python-sdk", name: {zh: "Codex", en: "Codex"},
        description: {zh: "合成测试账号", en: "Synthetic test account"}, authenticated: null,
        enabled: true, installed: true, selected: true, selectable: true, can_enable: true, platform_supported: true,
        model_discovery_supported: true, current_version: "test", cli_version: "test", check_status: "checked",
        authentication: {status: "not_checked", authenticated: null, identity: null, capabilities: caps,
          method: "unknown", source: "unknown", blockers: [], account_url: "https://chatgpt.com/"}};
      window.syntheticPosts = [];
      window.fetch = async (url, options = {}) => {
        let payload;
        if (options.method === "POST") {
          window.syntheticPosts.push({url, body: options.body ? JSON.parse(options.body) : null});
          payload = {operation: {operation_id: "auth_synthetic", status: url.endsWith("/cancel") ? "cancelled" : "waiting_authentication",
            auth_url: url.endsWith("/cancel") ? undefined : "https://auth.openai.com/login?state=synthetic-only"}};
          sdk.authentication.operation = payload.operation;
        } else if (url.includes("/authentication/operations/")) {
          payload = {operation: {operation_id: "auth_synthetic", status: "waiting_authentication", auth_url: "https://auth.openai.com/login?state=synthetic-only"}};
        } else if (url.includes("/models")) {
          payload = {items: [], catalog_status: "ready", model_catalog_complete: true};
        } else payload = {items: [sdk], default_provider_id: "codex"};
        return {ok: true, json: async () => payload};
      };
    }, {locale});
    const html = await readFile(path.join(buildRoot, locale, "settings/index.html"), "utf8");
    await page.route("**/*", async route => {
      const url = new URL(route.request().url());
      if (url.origin !== "http://127.0.0.1:4339") return route.abort();
      if (url.pathname === "/") return route.fulfill({contentType: "text/html", body: html.replace("</head>", `<style>${styles}</style></head>`)});
      const relative = url.pathname.replace(/^\/SAPBusinessAgents\//, "").replace(/^\//, "");
      const file = path.resolve(buildRoot, relative);
      if (!file.startsWith(buildRoot + path.sep)) return route.abort();
      try { return route.fulfill({body: await readFile(file), contentType: file.endsWith(".css") ? "text/css" : file.endsWith(".js") ? "text/javascript" : "application/octet-stream"}); }
      catch { return route.abort(); }
    });
    await page.goto("http://127.0.0.1:4339/");
    const section = page.locator(".sdk-authentication-section");
    await section.getByRole("heading", {name: locale === "zh" ? "账号与认证" : "Account & authentication"}).waitFor();
    assert.equal(await page.evaluate(() => window.syntheticPosts.length), 0);
    const login = section.getByRole("button", {name: locale === "zh" ? "登录" : "Sign in", exact: true});
    await login.focus(); await page.keyboard.press("Enter");
    await section.getByRole("link", {name: locale === "zh" ? "打开官方登录页面" : "Open official sign-in page"}).waitFor();
    assert.equal(await page.evaluate(() => window.syntheticPosts[0].body.action), "login");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path: path.join(destination, `${locale}-${width}.png`), fullPage: true});
    const cancel = section.getByRole("button", {name: locale === "zh" ? "取消" : "Cancel", exact: true});
    await cancel.focus(); await page.keyboard.press("Enter");
    await section.getByText(locale === "zh" ? "已取消" : "Cancelled", {exact: true}).waitFor();
    assert.equal(await page.evaluate(() => window.syntheticPosts.length), 2);
    assert.equal(await page.evaluate(() => window.syntheticPosts.some(p => /models|enable|default/.test(p.url))), false);
    assert.equal(await page.evaluate(() => JSON.stringify(Object.values(sessionStorage)).includes("synthetic-only")), false);
    records.push({locale, width, keyboard: true, no_overflow: true, no_implicit_probe: true, cancel: true, synthetic_only: true});
    await context.close();
  }
} finally {await browser.close();}
await writeFile(path.join(destination, "report.json"), JSON.stringify(records, null, 2));
console.log(JSON.stringify(records));
