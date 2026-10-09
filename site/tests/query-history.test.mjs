import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
test("both languages render a history list, conversion decisions and retention settings", async () => {
  for (const locale of ["zh", "en"]) {
    const read = (page) => readFile(new URL(`../dist/${locale}/${page}/index.html`, import.meta.url), "utf8");
    const ask = await read("ask");
    assert.match(ask, new RegExp(`/SAPBusinessAgents/${locale}/query-history/`));
    assert.match(ask, /data-query-history-link/);
    const history = await read("query-history");
    assert.match(history, /<table[^>]*data-history-table/);
    assert.match(history, /data-history-blocked/);
    assert.match(history, /data-history-retry/);
    assert.match(history, /data-history-abandon/);
    assert.match(history, /data-history-prev/);
    assert.match(history, /data-history-next/);
    assert.match(await read("settings"), /data-history-settings/);
    assert.match(history, locale === "zh" ? /只有成功的查询才能转为 Agent/ : /Only successful queries can become Agents/);
  }
});
