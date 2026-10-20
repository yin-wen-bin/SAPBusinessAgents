import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
async function component(name, dependencies = {}) {
  dependencies["../lib/assistantPresentation"] = await import("../src/lib/assistantPresentation.ts");
  if (["AgentDraftConversation", "WorkflowAssistant"].includes(name)) dependencies["./AssistantHistory"] = await component("AssistantHistory");
  if (["AgentDraftWorkspace", "WorkflowAssistant"].includes(name)) dependencies["./AssistantResize"] = await component("AssistantResize");
  if (name === "WorkflowAssistant") {
    dependencies["./AssistantRound"] = await component("AssistantRound");
    dependencies["./AssistantAnnotations"] = await component("AssistantAnnotations");
    dependencies["../lib/assistantAnnotations"] = await import("../src/lib/assistantAnnotations.ts");
  }
  if (name === "AssistantAnnotations") dependencies["../lib/assistantAnnotations"] = await import("../src/lib/assistantAnnotations.ts");
  const source = await readFile(new URL(`../src/components/${name}.tsx`, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true,
  } });
  const exports = {};
  new Function("require", "exports", compiled.outputText)(id => id.endsWith(".css") ? {} : dependencies[id] || require(id), exports);
  return exports.default ? Object.assign(exports.default, exports) : exports;
}

test("workflow assistant exposes two direct intents in both languages and all phases", async () => {
  const Assistant = await component("WorkflowAssistant", { "./SafeMarkdown": await component("SafeMarkdown") });
  for (const locale of ["zh", "en"]) for (const step of ["compose", "review", "validate", "publish"]) {
    const markup = renderToStaticMarkup(createElement(Assistant, {
      locale, step, apiBase: "", published: false, busy: false, dirty: false,
      prepare() {}, refresh() {},
    }));
    assert.match(markup, locale === "zh" ? /解释问题/ : /Explain/);
    assert.match(markup, locale === "zh" ? /修改工作流/ : /Revise workflow/);
    assert.match(markup, /restricted/);
    assert.match(markup, /full_access/);
    assert.doesNotMatch(markup, new RegExp(` · ${step}</p>`));
    assert.doesNotMatch(markup, /workflow-feedback-categories/);
  }
});

test("workflow assistant labels save-and-revise and published revisions cannot submit", async () => {
  const Assistant = await component("WorkflowAssistant", { "./SafeMarkdown": await component("SafeMarkdown") });
  const props = { locale: "en", step: "review", apiBase: "", draftId: "test", revision: 3,
    published: false, busy: false, dirty: true, fieldPath: "/nodes/0", prepare() {}, refresh() {} };
  const markup = renderToStaticMarkup(createElement(Assistant, props));
  assert.match(markup, /Save and revise/);
  assert.match(markup, /\/nodes\/0/);
  const published = renderToStaticMarkup(createElement(Assistant, { ...props, published: true, dirty: false }));
  assert.match(published, /disabled=""[^>]*>Revise workflow/);
});

test("workflow new interaction preserves intent on uncertain delivery and has bounded fallback", async () => {
  const source = await readFile(new URL("../src/components/WorkflowAssistant.tsx", import.meta.url), "utf8");
  assert.match(source, /JSON.stringify\(pending.current.body\)/);
  assert.match(source, /pending.current.body.intent !== "explain"/);
  assert.match(source, /pending.current.body.intent !== "revise"/);
  assert.match(source, /last-event|\/events\?after=/);
  assert.match(source, /Math.min\(30000/);
  assert.match(source, /sessionStorage.setItem/);
});

test("new workflow rounds use one hour without rewriting uncertain requests", async () => {
  const source = await readFile(new URL("../src/components/WorkflowAssistant.tsx", import.meta.url), "utf8");
  assert.match(source, /budgetSeconds: 3600/);
  assert.doesNotMatch(source, /budgetSeconds: 600\b/);
  assert.match(source, /if \(!pending.current\)/);
  assert.match(source, /JSON.stringify\(pending.current.body\)/);
  assert.doesNotMatch(source, /pending.current.body.budgetSeconds\s*=/);
});

test("scope checkbox is opt-in, reference-bound and hidden without selection", async () => {
  const Assistant = await component("WorkflowAssistant", { "./SafeMarkdown": await component("SafeMarkdown") });
  const props = { locale: "zh", step: "review", apiBase: "", published: false, busy: false, dirty: false, prepare() {}, refresh() {} };
  const absent = renderToStaticMarkup(createElement(Assistant, props));
  assert.doesNotMatch(absent, /仅修改已选字段/);
  const selected = renderToStaticMarkup(createElement(Assistant, { ...props, fieldPath: "/title", fieldLabel: "名称" }));
  assert.match(selected, /仅修改已选字段：.*名称/);
  assert.doesNotMatch(selected, /checked=""/);
  assert.match(selected, /type="checkbox"/);
  const dirty = renderToStaticMarkup(createElement(Assistant, { ...props, fieldPath: "/title", dirty: true }));
  assert.match(dirty, /type="checkbox"[^>]*disabled=""/);
  const source = await readFile(new URL("../src/components/WorkflowAssistant.tsx", import.meta.url), "utf8");
  assert.match(source, /intent === "revise" && restrictFields/);
  assert.match(source, /allowedChangePaths: \[props.fieldPath\]/);
  assert.match(source, /setRestrictFields\(false\)/);
});
