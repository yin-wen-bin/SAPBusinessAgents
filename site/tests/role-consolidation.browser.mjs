// Synthetic component/layout check only. No production API, model or SAP.
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.SAPBA_PLAYWRIGHT_MODULE || "playwright");
const source = await readFile(new URL("../src/components/RoleConsolidationStatus.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: {
  jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true,
} });
const exports = {};
new Function("require", "exports", compiled.outputText)(require, exports);
const Component = exports.default;
const styles = await readFile(new URL("../src/styles/global.css", import.meta.url), "utf8");
const output = process.env.SAPBA_BROWSER_REPORT;
assert.ok(output, "Specify a fresh isolated SAPBA_BROWSER_REPORT directory");
await mkdir(output, { recursive: false });
const browser = await chromium.launch({ headless: true, channel: process.platform === "win32" ? "msedge" : undefined });
const records = [];
try {
  for (const locale of ["zh", "en"]) for (const width of [1440, 550, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 750 } });
    const page = await context.newPage();
    // The component receives only synthetic props, not an actual role result.
    await page.route("**/*", route => route.abort());
    const markup = renderToStaticMarkup(createElement(Component, { locale,
      evaluation: { agent_catalog_complete: true, consolidation_complete: false }, diagnostics: [{
        failure_code: "runtime_report_validation_failed", budget_seconds: 300, elapsed_ms: 300001,
        validation_issues: [{ path: "/analysis_json/workflow_suggestions/0/" + "declared_field_".repeat(12), constraint: "required" }],
      }] }));
    await page.setContent(`<html lang="${locale}"><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>${styles}</style></head><body><main style="padding:20px">${markup}</main></body></html>`);
    assert.equal(await page.locator("details").getAttribute("open"), null);
    await page.keyboard.press("Tab");
    assert.equal(await page.locator("summary").evaluate(element => element === document.activeElement), true);
    await page.keyboard.press("Enter");
    assert.notEqual(await page.locator("details").getAttribute("open"), null);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(output, `${locale}-${width}.png`), fullPage: true });
    records.push({ locale, width, keyboard_toggle: true, horizontal_overflow: false, synthetic_only: true });
    await context.close();
  }
} finally {
  await browser.close();
}
await writeFile(path.join(output, "report.json"), JSON.stringify(records, null, 2));
console.log(JSON.stringify(records));
