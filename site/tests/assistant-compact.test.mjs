import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { assistantWidth, businessSummaryVersion, resultPresentation } from "../src/lib/assistantPresentation.ts";

test("width is clamped for viewport without overwriting the preferred width", () => {
  assert.deepEqual(assistantWidth(500, 616, 690), { width: 500, max: 616, resizable: true });
  assert.equal(assistantWidth(640, 400, 690).width, 400);
  assert.equal(assistantWidth(640, 1000, 1440).width, 640);
  for (const width of [550, 390]) {
    assert.equal(assistantWidth(600, width - 24, width).width, width - 24);
    assert.equal(assistantWidth(600, width - 24, width).resizable, false);
  }
  assert.equal(assistantWidth(600, 300, 900).width, 300);
});

test("a model success claim cannot override failed, unknown or unchanged save outcomes", () => {
  for (const locale of ["zh", "en"]) {
    const source = { intent: "revise", reply: "MODEL CLAIMS SAVED", version: businessSummaryVersion, baseRevision: 1, resultRevision: 2, diff: [{}] };
    for (const status of ["failed", "running", "cancelled", "timed_out", "needs_review", "cleanup_pending", "unknown"]) {
      assert.deepEqual(resultPresentation({ ...source, status }, locale), { text: "", saved: false });
    }
    const unchanged = resultPresentation({ ...source, status: "completed", resultRevision: 1, diff: [] }, locale);
    assert.equal(unchanged.saved, false); assert.doesNotMatch(unchanged.text, /MODEL CLAIMS/);
    assert.equal(resultPresentation({ ...source, status: "completed" }, locale).text, source.reply);
  }
});

test("legacy modification replies do not leak technical prose or rewrite source records", () => {
  const old = { status: "completed", action: "revise_agent", reply: "PRIVATE tool output /manifest sha256",
    baseRevision: 1, resultRevision: 2, diff: [{ path: "/manifest/title" }] };
  const before = JSON.stringify(old);
  const result = resultPresentation(old, "zh");
  assert.equal(result.saved, true); assert.doesNotMatch(result.text, /PRIVATE|sha256|manifest/);
  assert.equal(JSON.stringify(old), before);
  assert.match(resultPresentation({ ...old, resultRevision: undefined }, "zh").text, /依据不足/);
  for (const action of ["reply", "explain", "clarify"]) assert.equal(resultPresentation({ ...old, action }, "zh").text, old.reply);
});

test("15 rounds and audit entries are one hidden history group, not 14 visible headings", async () => {
  const source = await readFile(new URL("../src/components/AssistantHistory.tsx", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true } });
  const exports = {}; new Function("require", "exports", compiled.outputText)(createRequire(import.meta.url), exports);
  const entries = Array.from({ length: 15 }, (_, i) => ({ id: String(i), latest: i === 14, label: String(i), content: createElement("p", null, String(i)), attention: i === 2 }));
  entries.unshift({ id: "create", audit: true, label: "Create", content: createElement("p", null, "Created") });
  for (const locale of ["zh", "en"]) {
    const html = renderToStaticMarkup(createElement(exports.default, { entries, locale }));
    assert.equal((html.match(/hidden=""/g) || []).length, 15);
    assert.match(html, /aria-expanded="false"/); assert.match(html, /\(14\)/);
    assert.match(html, /assistant-history-attention/);
    assert.equal((html.match(/assistant-history-latest/g) || []).length, 1);
  }
});
