import assert from "node:assert/strict";
import test from "node:test";
import { ListResource, managementRows, filterManagementRows, acceptanceState, pollingDelay } from "../src/lib/agentManagement.ts";

const agents = [
  { id: "a", module: "SD", title: { zh: "同名", en: "Same" }, lifecycle: { state: "active" }, validation: { verdict: "BLOCKED" } },
  { id: "b", module: "FI", title: { zh: "财务", en: "Finance" }, lifecycle: { state: "inactive" }, validation: { verdict: "PASS" } },
];
const drafts = [
  { draft_id: "d1", agent_id: "a", module: "SD", title: { zh: "同名", en: "Same" }, status: "validated", validation: { verdict: "NOT_TESTED" } },
  { draft_id: "d2", agent_id: "c", module: "SD", title: { zh: "空", en: "Empty" }, status: "draft", validation: {} },
];

test("unified filters intersect without merging the published Agent and its version draft", () => {
  const rows = managementRows(agents, drafts, "en");
  assert.equal(rows.length, 4);
  assert.equal(new Set(rows.map((row) => row.rowKey)).size, 4);
  const matching = filterManagementRows(rows, { module: "SD", state: "unpublished", acceptance: "NOT_TESTED" });
  assert.deepEqual(matching.map((row) => row.draft_id), ["d1"]);
  assert.ok(rows.findIndex((row) => row.rowKey === "agent:a") < rows.findIndex((row) => row.rowKey === "draft:d1"));
  assert.equal(filterManagementRows(rows, { module: "FI", state: "active", acceptance: "" }).length, 0);
  assert.equal(filterManagementRows(rows, { module: "", state: "", acceptance: "" }).length, 4);
});

test("draft progress cannot imply acceptance and synchronization errors fail closed", () => {
  assert.equal(acceptanceState(drafts[0]), "NOT_TESTED");
  assert.equal(acceptanceState(drafts[1]), "UNRECORDED");
  assert.equal(acceptanceState({ status: "validated" }), "UNRECORDED");
  assert.equal(acceptanceState({ validation: { verdict: "pending", status: "running" } }), "PENDING");
  assert.equal(acceptanceState({ sync_error: "missing_run", validation: { verdict: "PASS" } }), "UNRECORDED");
  assert.equal(acceptanceState({ validation: { verdict: "PARTIAL" } }), "PARTIAL");
});

test("list resources fail independently and keep a stale successful snapshot until retry", async () => {
  let fail = false;
  const catalog = new ListResource(async () => agents, () => {});
  const draftResource = new ListResource(async () => { if (fail) throw Error("offline"); return drafts; }, () => {});
  await Promise.all([catalog.refresh(), draftResource.refresh()]);
  const stamp = draftResource.state.updatedAt;
  fail = true;
  await Promise.all([catalog.refresh(), draftResource.refresh()]);
  assert.equal(catalog.state.error, "");
  assert.equal(draftResource.state.error, "offline");
  assert.equal(draftResource.state.loaded, true);
  assert.equal(draftResource.state.updatedAt, stamp);
  assert.equal(draftResource.state.data, drafts);
  fail = false;
  await draftResource.refresh();
  assert.equal(draftResource.state.error, "");
  const neverLoaded = new ListResource(async () => { throw Error("offline"); }, () => {});
  await neverLoaded.refresh();
  assert.equal(neverLoaded.state.loaded, false);
});

test("overlapping refreshes coalesce and disposed old responses cannot overwrite fresh data", async () => {
  const pending = [];
  const resource = new ListResource(() => new Promise((resolve) => pending.push(resolve)), () => {});
  const old = resource.refresh();
  assert.equal(resource.refresh(), old);
  await Promise.resolve();
  resource.dispose();
  const current = resource.refresh();
  await Promise.resolve();
  pending[1](["latest"]);
  await current;
  pending[0](["obsolete"]);
  await old;
  assert.deepEqual(resource.state.data, ["latest"]);
  assert.deepEqual([0, 1, 2, 3, 5].map(pollingDelay), [5000, 10000, 20000, 30000, 30000]);
});
