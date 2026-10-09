import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = await readFile(new URL("../src/components/RoleConsolidationStatus.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: {
  jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true,
} });
const exports = {};
new Function("require", "exports", compiled.outputText)(require, exports);
const Component = exports.default;

test("role consolidation is separate from session/catalog completion in both languages", () => {
  for (const locale of ["zh", "en"]) {
    const html = renderToStaticMarkup(createElement(Component, { locale,
      evaluation: { agent_catalog_complete: true, consolidation_complete: false }, diagnostics: [{
        failure_code: "runtime_deadline_exceeded", budget_seconds: 300, elapsed_ms: 300010,
        validation_issues: [{ path: "/summary_zh", constraint: "required" }],
        message: "private-model-text-must-not-render", raw_output: "secret",
      }] }));
    assert.match(html, locale === "zh" ? /汇总未完成/ : /consolidation incomplete/);
    assert.match(html, /runtime_deadline_exceeded/);
    assert.match(html, /300 s/);
    assert.match(html, /\/summary_zh/);
    assert.match(html, /role="status"/);
    assert.doesNotMatch(html, /private-model-text|secret|<button/);
    assert.match(html, /<details>/);
  }
});

test("complete results and historical missing flags do not acquire a new failure", () => {
  for (const evaluation of [{ agent_catalog_complete: true, consolidation_complete: true },
    { agent_catalog_complete: true }, { agent_catalog_complete: false, consolidation_complete: false }]) {
    assert.equal(renderToStaticMarkup(createElement(Component, { locale: "zh", evaluation })), "");
  }
});

test("diagnostic field paths render as escaped text, never HTML", () => {
  const html = renderToStaticMarkup(createElement(Component, { locale: "en",
    evaluation: { agent_catalog_complete: true, consolidation_complete: false }, diagnostics: [{
      failure_code: "<script>ignored()</script>", validation_issues: [{ path: "<img src=x>", constraint: "required" }],
    }] }));
  assert.doesNotMatch(html, /<script>|<img/);
  assert.match(html, /&lt;img/);
});
