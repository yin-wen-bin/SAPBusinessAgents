---
name: sapba-auto-test
description: 在 Codex App 当前聊天内自动测试 SAPBusinessAgents：列出并勾选功能、生成脚本、只读 SAP 选样与独立基线、真实前台操作、整轮收集错误后批量修复和定向重测。支持“测试 SAPBusinessAgents”、开始、停止、继续上次测试和重测失败项；不启动额外 AI Runtime。
---

# SAPBusinessAgents 自动测试

You are the orchestrator in the **current native Codex conversation**. Use available native file, terminal, browser, Computer Use and MCP tools as appropriate; this skill does not replace or narrow the host toolset. Do not start another Codex SDK, `codex exec`, or backend AI loop. The product under test keeps its own real Runtime chain.

Read [the execution contract](references/execution.md) when preparing or executing a test. Resolve the repository from the user's workspace or this installed skill's `repository.json`; never assume that this skill's directory is the repository. Use its `.venv` Python and `scripts/run.py` as the helper entrypoint.

## Select and generate

1. Run `inventory`. Discover current features, source references, Agents, workflows, tests and missing coverage. Do not treat an active Agent as accepted/executable. Existing `sapclawtestcase` principles apply, but its historical `sap_odata_agent` runner paths are not present in this repository.
2. Run `card --output <durable-absolute-path.html>` and render it inline using the host's visualization surface. Read the available visualization guidance before displaying it. Default selection is free-query, workflows and agent-management; all eight categories are selectable. If the host bridge is unavailable, accept the card's textual action in this same chat. Do not open a separate console or website.
3. On `prepare` with `selected` and `inventory_digest`, check inventory freshness. Inspect the selected UI and source contracts, generate realistic cases for **each applicable selected operation**, then pass a JSON cases file to `prepare --select ... --cases ... --inventory-digest ...`. The no-cases CLI seed is explicitly offline regression only and must not be presented as full coverage.
4. Cases must have distinct business scenarios, not different object IDs. Generate main paths, relevant invalid/boundary paths, and cancel/recovery for asynchronous features. Natural-language questions must use business wording. Freeze independent baseline queries, comparison rules, scope, dependencies and impact paths before execution. Never derive an oracle from the product's answer or implementation rules.
5. Display the generated coverage and a `card --plan ...` with **开始测试**. Selection, generation and saving widget state never start tests. Explicit user instructions to start may substitute for the button; do not ask twice for already granted scope.

## Execute in this chat

On a `start` action, verify the plan digest and use its stable `start:<plan_id>` request ID. `start` snapshots the actual working tree, including existing edits. Use `environment` and `target-marker` to prepare the disposable product target; start its API and site with native tools and the specified isolated directories. Bind verified loopback endpoints with `bind-target`. Never point lifecycle tests at the user's normal product instance.

For every round, drain **all** selected runnable cases before repairs. `run-round` runs deterministic cases and returns native-tool work without launching an AI. Use current browser/Computer Use tools for native cases, save screenshots, tool observations and typed assertions under the returned evidence directory, then `finish-case`. Prefer reproducible browser scripts when actions are known. Persist results promptly so context compaction or connection loss does not lose progress.

For live cases, call the approved `test_sap_read` MCP tool in order: optional sample → baseline → frontend execution → fresh anchor → deterministic comparison/finish. Sample values must come from verified receipts; use `$sample.Field` bindings. Never read SAP with terminal HTTP requests, browser requests to SAP, copied credentials, or guessed object IDs. If the MCP read capability is absent, record the affected live cases as blocked and continue offline/independent cases. Do not invent evidence. Queries stay bounded to the account's existing readable range and approved read-only sources.

Lifecycle operations use only run-owned temporary objects in the disposable target. Test both allowed deletion and refusal when references/history prevent deletion. Product publication/acceptance gates stay intact. Mail sending, account changes and SDK replacement use explicit simulation fixtures; do not perform those real external actions merely to test the feature.

## Batch repair and finish

- An ordinary failing case is evidence to collect, not a reason to start editing. `begin-repair` is allowed only when the complete round has terminal outcomes.
- Classify failures before editing. Data gaps, changed SAP data, credentials, permissions and unavailable tools are blockers, not product defects. Group repeated failures by root cause and retain the original observations.
- Repair product code in the returned **candidate source** directory, not the disposable lifecycle target or original checkout. Keep plan/oracle/acceptance requirements intact; do not delete cases, relax assertions, fake baselines or bypass gates. Fix genuine locator/driver bugs with an audited explanation and preserve original evidence.
- Run relevant unit regressions in the candidate, then `verify-repair`. It calculates failed/affected cases and prerequisite chains. Rebuild/rebind a fresh product target for the new round before browser cases. Drain the complete retest round before considering another repair.
- Default maximum is three batch repairs; a root cause with two non-improving rounds is reported and retired from automatic attempts. Other issues continue. Do not loop indefinitely.
- Run `begin-final`, complete the once-per-cycle selected-suite review, and `complete`. `apply` automatically applies the verified cumulative patch with conflict checks and recovery backups. Existing edits form the baseline; later user edits are not overwritten. Do not commit, push or publish the project.
- Use `report` and show the resulting Markdown/JSON, failure groups, screenshot links and actual Diff. Distinguish live SAP, offline and simulated evidence and passed/failed/blocked/inconclusive/not-run states. A test report does not grant formal product acceptance.

For stop, call `cancel` and stop only task-owned processes. For resume, inspect status and reconcile active/unknown operations before dispatch; `resume --interrupted-case ...` preserves an unknown operation as blocked rather than replaying it. For “重测失败项”, use `retest`; it does not consume a repair batch.
