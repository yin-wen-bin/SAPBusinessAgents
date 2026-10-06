import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../src/components/SystemSettingsPanel.astro", import.meta.url), "utf8");

test("model catalog exposes SDK-backed effort choices and distinct default/saved labels", () => {
  assert.match(source, /model\.supported_reasoning_efforts/);
  assert.match(source, /const effortSelect = element\("select"\)/);
  assert.match(source, /setAttribute\("aria-label"/);
  assert.match(source, /model\.default_reasoning_effort/);
  assert.match(source, /model\.saved_reasoning_effort/);
  for (const text of ["SDK 建议默认", "用户已保存", "Save reasoning effort", "保存推理强度"]) assert.ok(source.includes(text));
});

test("checking and independently saving send explicit chosen effort", () => {
  assert.match(source, /model_id: model\.model_id, reasoning_effort: effortSelect\.value/);
  assert.match(source, /\/models\/\$\{encodeURIComponent\(model\.model_id\)\}\/reasoning-effort/);
  assert.match(source, /body: JSON\.stringify\(\{ reasoning_effort: effortSelect\.value \}\)/);
  assert.match(source, /effortSelect\.disabled = model\.retired \|\| payload\.catalog_status !== "ready"/);
  assert.match(source, /正在运行的轮次不变/);
  assert.match(source, /if \(modelBusy\) return; setModelBusy\(true\)/);
  assert.match(source, /row\.setAttribute\("aria-busy", String\(busy\)\)/);
});

test("WorkBuddy candidates are explicit, unverified and cannot probe before authentication", () => {
  const branch = source.slice(source.indexOf('if (sdk.provider_id === "workbuddy")'), source.indexOf('const statusText = payload.catalog_status'));
  assert.match(branch, /refresh\.disabled = !sdk\.installed/);
  assert.match(branch, /\/workbuddy\/models\/refresh/);
  assert.match(branch, /check\.disabled = !sdk\.model_check_supported \|\| sdk\.authenticated !== true/);
  assert.match(branch, /modelPicker\(sdk, payload, true\)/);
  assert.match(branch, /const modelId = model\?\.model_id \|\| input\.value\.trim\(\)/);
  assert.match(branch, /if \(model && model\.selectable && !model\.selected\)/);
  for (const text of ["CLI 声明，尚未验证", "CLI-declared, not checked"]) assert.ok(branch.includes(text));
  assert.doesNotMatch(branch, /reasoning_effort: effortSelect/);
  assert.match(branch, /login-workbuddy-runtime\.ps1/);
  assert.match(branch, /if \(sdk\.installed\)/);
  assert.match(branch, /WorkBuddy is not enabled automatically/);
});

test("WorkBuddy sign-in command wraps without overflowing narrow settings panels", async () => {
  const css = await readFile(new URL("../src/styles/global.css", import.meta.url), "utf8");
  assert.match(source, /sdk-update-note sdk-login-command/);
  assert.match(css, /\.sdk-login-command\s*\{[^}]*white-space: normal;[^}]*overflow-wrap: anywhere;/);
});
