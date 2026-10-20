import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const require = createRequire(import.meta.url);
const source = await readFile(new URL("../src/components/RoleRuntimeDiagnostic.tsx", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
const exports = {};
new Function("require", "exports", compiled.outputText)(require, exports);

test("role stage failures expose elapsed/budget and only confirmed safe counters", () => {
  for (const locale of ["zh", "en"]) {
    const html = renderToStaticMarkup(createElement(exports.default, { locale, code: "workbuddy_deadline_exceeded",
      detail: { phase: "role_understanding", budget_seconds: 3600, elapsed_ms: 3600100, message_count: 7,
        first_response_ms: 10, terminal_result_received: false, password: "do-not-render", raw_log: "private-data" } }));
    assert.match(html, /3600 s/);
    assert.match(html, /role_understanding/);
    assert.match(html, /message_count/);
    assert.doesNotMatch(html, /password|do-not-render|raw_log|private-data|assistant_message_count/);
  }
});

test("unsafe phase/code and invalid counters are not rendered", () => {
  const html = renderToStaticMarkup(createElement(exports.default, { locale: "en", code: "password=PRIVATE",
    detail: { phase: "token=PRIVATE", budget_seconds: -1, elapsed_ms: NaN, message_count: Infinity } }));
  assert.doesNotMatch(html, /PRIVATE|password|token|NaN|Infinity|Stage budget/);
});

test("native failures show allowlisted classes, terminal and numeric RPC codes only", () => {
  for (const locale of ["zh", "en"]) {
    const html = renderToStaticMarkup(createElement(exports.default, { locale, code: "runtime_native_rpc_error",
      detail: { native_error_class: "InvalidParamsError", native_rpc_code: -32602, native_error_code: "badRequest",
        native_terminal_status: "failed", message: "secret-password", data: { token: "secret-token" } } }));
    assert.match(html, /InvalidParamsError/);
    assert.match(html, /-32602/);
    assert.match(html, /badRequest/);
    assert.doesNotMatch(html, /secret-password|secret-token/);
    const unsafe = renderToStaticMarkup(createElement(exports.default, { locale,
      detail: { native_error_class: "private-class", native_error_code: "private-code", native_rpc_code: NaN,
        native_terminal_status: "private-status" } }));
    assert.doesNotMatch(unsafe, /private-class|private-code|private-status|NaN/);
  }
});
