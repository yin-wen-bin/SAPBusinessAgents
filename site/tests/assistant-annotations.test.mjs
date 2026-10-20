import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { annotationFeedback, annotationLimit, upsertAnnotation, workflowAnnotationReference } from "../src/lib/assistantAnnotations.ts";
import { workflowAnnotationHost, workflowConnectionKey } from "../src/lib/workflowAnnotationHost.ts";

test("batch controller supports distinct targets, explicit replacement and immutable comments", () => {
  const first = { key: "one", label: "Name", reference: { revision: 1 }, comment: "  change name  " };
  const one = upsertAnnotation([], first);
  assert.equal(one[0].comment, "change name");
  assert.equal(first.comment, "  change name  ");
  const two = upsertAnnotation(one, { ...first, key: "two", comment: "Other field" });
  assert.equal(two.length, 2);
  const revised = upsertAnnotation(two, { ...first, comment: "New comment", reference: { revision: 2 } });
  assert.equal(revised.length, 2);
  assert.equal(revised[0].reference.revision, 2);
  assert.equal(two[0].reference.revision, 1);
});

test("batch limits reject rather than truncate or invent comments", () => {
  const item = { key: "one", label: "Name", reference: {}, comment: "x" };
  for (const comment of ["", "  ", "x".repeat(2001)]) assert.throws(() => upsertAnnotation([], { ...item, comment }));
  const full = Array.from({ length: annotationLimit }, (_, i) => ({ ...item, key: String(i) }));
  assert.throws(() => upsertAnnotation(full, item), /annotation_limit/);
  assert.equal(upsertAnnotation(full, { ...item, key: "0", comment: "updated" }).length, annotationLimit);
});

test("annotation-only send uses a fixed instruction without rewriting user input", () => {
  assert.match(annotationFeedback("", 2, "zh"), /2 处/);
  assert.match(annotationFeedback("", 2, "en"), /2 annotation/);
  assert.equal(annotationFeedback("  User instruction  ", 2, "en"), "User instruction");
});

const workflow = { title: { zh: "标题", en: "Title" }, nodes: [{ id: "a" }, { id: "b" }], connections: [
  { from: { scope: "node_output", nodeId: "a", port: "rows" }, to: { nodeId: "b", port: "input" } },
] };
test("workflow mapping follows stable node and connection identities, not guessed indexes", () => {
  const node = workflowAnnotationReference(workflow, "draft", 3, { nodeId: "b" }, workflowConnectionKey);
  assert.deepEqual(node, { kind: "workflow_field", draftId: "draft", revision: 3, path: "/nodes/1", objectId: "b", objectKind: "node" });
  const reordered = { ...workflow, nodes: [...workflow.nodes].reverse() };
  assert.equal(workflowAnnotationReference(reordered, "draft", 4, { nodeId: "b" }, workflowConnectionKey).path, "/nodes/0");
  const key = workflowConnectionKey(workflow.connections[0]);
  const connection = workflowAnnotationReference(workflow, "draft", 3, { connectionId: key }, workflowConnectionKey);
  assert.equal(connection.objectId, "a:rows->b:input");
  assert.equal(connection.path, "/connections/0");
  assert.equal(workflowAnnotationReference(workflow, "draft", 3, { nodeId: "absent" }, workflowConnectionKey), null);
  assert.equal(workflowAnnotationReference({ ...workflow, nodes: [{ id: "a" }, { id: "a" }] }, "draft", 3, { nodeId: "a" }, workflowConnectionKey), null);
});

test("page adapter rejects obsolete object paths even with an unchanged revision", () => {
  const host = workflowAnnotationHost({ workflow: { ...workflow, nodes: [...workflow.nodes].reverse() }, draftId: "draft", revision: 3, root: () => null, reveal() {} });
  assert.equal(host.stale({ kind: "workflow_field", draftId: "draft", revision: 3, path: "/nodes/1", objectId: "b", objectKind: "node" }), true);
  assert.equal(host.stale({ kind: "workflow_field", draftId: "draft", revision: 2, path: "/title" }), true);
  assert.equal(host.stale({ kind: "workflow_field", draftId: "other", revision: 3, path: "/title" }), true);
});

test("unmapped and platform-maintained workflow controls have no annotation reference", () => {
  for (const path of ["/published", "/validation", "/id", "/references", "/missing"]) {
    assert.equal(workflowAnnotationReference(workflow, "draft", 3, { path }, workflowConnectionKey), null);
  }
});

test("both hosts use the same overlay and heading annotation control, with no old buttons", async () => {
  for (const name of ["AgentDraftWorkspace", "WorkflowAssistant"]) {
    const source = await readFile(new URL(`../src/components/${name}.tsx`, import.meta.url), "utf8");
    assert.match(source, /useAssistantAnnotations/);
    assert.match(source, /className="assistant-heading-tools"><h2>[\s\S]*?<AnnotationButton/);
    assert.match(source, /<AssistantAnnotationOverlay controller=/);
    assert.doesNotMatch(source, /标注控件|引用页面所选内容|setWorkbuddyTrusted/);
  }
  const shared = await readFile(new URL("../src/components/AssistantAnnotations.tsx", import.meta.url), "utf8");
  assert.match(shared, /pointerdown/);
  assert.match(shared, /stopImmediatePropagation/);
  assert.match(shared, /ResizeObserver/);
  assert.match(shared, /MutationObserver/);
  assert.match(shared, /preferred\.current/);
  assert.match(shared, /commentRef\.current/);
  assert.match(shared, /matchMedia\("\(max-width: 720px\)"\)/);
  assert.match(shared, /"Delete"|delete graph nodes/);
  assert.doesNotMatch(shared, /workbuddy|codex|localStorage|sessionStorage/i);
  const builder = await readFile(new URL("../src/components/WorkflowBuilder.tsx", import.meta.url), "utf8");
  assert.match(builder, /if \(change.type !== "position" \|\| !change.position\) return \[\]/);
  assert.match(builder, /if \(moves.length\) mutateWorkflow/);
});

test("WorkBuddy draft default does not change workflow permissions or uncertain request bodies", async () => {
  const draft = await readFile(new URL("../src/components/AgentDraftWorkspace.tsx", import.meta.url), "utf8");
  assert.match(draft, /const workbuddyTrusted = true/);
  assert.match(draft, /executionMode: "trusted_local" as const, trustedLocalConfirmed: workbuddyTrusted/);
  assert.match(draft, /uncertainFeedback && pendingFeedbackRequest.current \? pendingFeedbackRequest.current/);
  const flow = await readFile(new URL("../src/components/WorkflowAssistant.tsx", import.meta.url), "utf8");
  assert.match(flow, /useState<"restricted" \| "full_access">\("restricted"\)/);
  assert.match(flow, /mode === "full_access" && !window.confirm/);
  assert.match(flow, /JSON.stringify\(pending.current.body\)/);
});

test("round disclosures render all history but only latest is initially open", async () => {
  const source = await readFile(new URL("../src/components/AssistantRound.tsx", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true } });
  const exports = {}, require = createRequire(import.meta.url);
  new Function("require", "exports", compiled.outputText)(require, exports);
  const old = renderToStaticMarkup(createElement(exports.default, { latest: false, summary: "Old turn" }, "Full old content"));
  const latest = renderToStaticMarkup(createElement(exports.default, { latest: true, summary: "Latest turn" }, "Latest content"));
  assert.doesNotMatch(old, /<details[^>]* open/);
  assert.match(latest, /<details[^>]* open/);
  assert.match(old, /Full old content/);
  assert.match(source, /choice \?\? latest/);
  assert.doesNotMatch(source, /useEffect|setInterval/);
});
