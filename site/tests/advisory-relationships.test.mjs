import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


test("run results separate advisory guidance from execution errors in both languages", async () => {
  const zh = await readFile(new URL("../dist/zh/run/index.html", import.meta.url), "utf8");
  const en = await readFile(new URL("../dist/en/run/index.html", import.meta.url), "utf8");

  assert.match(zh, /参考提示/);
  assert.match(zh, /不会阻止查询/);
  assert.match(en, /Advisory guidance/);
  assert.match(en, /does not block the query/);
  assert.match(zh, /data-advisories/);
  assert.match(en, /data-advisories/);
});


test("draft workbench keeps the controlled policy out of the business editing UI", async () => {
  const source = await readFile(
    new URL("../src/components/AgentDraftWorkspace.tsx", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(source, /关系资料使用方式/);
  assert.doesNotMatch(source, /Relationship guidance/);
  assert.doesNotMatch(source, /Adopt advisory relationship guidance/);
  assert.doesNotMatch(source, /relationshipPolicy === "advisory"/);
});
