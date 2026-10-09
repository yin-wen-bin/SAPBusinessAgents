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

test("unresolved operations show specific bilingual diagnostics without raw error content", () => {
  for (const locale of ["zh", "en"]) {
    const html = renderToStaticMarkup(createElement(exports.default, { locale,
      evaluation: { agent_catalog_complete: true, consolidation_complete: false },
      issues: [{ code: "role_matching_operation_unresolved", operation_id: "op-one", message: "private SDK output" }],
    }));
    assert.match(html, /role_matching_operation_unresolved/);
    assert.match(html, /op-one/);
    assert.match(html, locale === "zh" ? /未解决操作/ : /outstanding operations/);
    assert.doesNotMatch(html, /private SDK/);
  }
});

test("workflow creation also requires consolidation while historical reports remain compatible", async () => {
  const component = await readFile(new URL("../src/components/RoleMatchingAssistant.tsx", import.meta.url), "utf8");
  assert.match(component, /consolidationReady = catalogEvaluation\.consolidation_complete !== false/);
  assert.match(component, /disabled=\{busy \|\| !catalogComplete \|\| !consolidationReady\}/);
});
