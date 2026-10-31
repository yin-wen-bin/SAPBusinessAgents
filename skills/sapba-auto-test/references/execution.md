# Native execution contract

Run `<repository>/.venv/Scripts/python.exe <skill>/scripts/run.py <command> ...` on Windows. Commands return `{ok,data}` or a safe diagnostic code. The helper is deterministic and never launches a model.

## Commands and lifecycle

| Command | Inputs / behavior |
|---|---|
| `inventory` | Current feature tree, sources, test references and digest; no live calls |
| `card --output PATH` | Inline feature selector; add `--plan ID` for Start, `--task ID` for status actions |
| `prepare --select FEATURE... --cases FILE --inventory-digest HASH` | Freeze generated cases and scripts; default max repairs 3 |
| `start --plan ID --digest HASH --request-id start:ID` | Idempotent snapshot; does not dispatch cases |
| `environment --task ID` | Prepare disposable target and return Python/site commands and isolated state paths |
| `target-marker --task ID` | Place target identity in that target's public assets |
| `bind-target --task ID --site-url URL --api-url URL` | Verify site marker and API data-root isolation |
| `run-round --task ID` / `run-case --task ID --case ID` | Collect outcomes; native cases return an owned claim for host tools |
| `finish-case --task ID --case ID --token TOKEN --result FILE` | Save actual terminal outcome and evidence; live passes require independent comparison |
| `begin-repair` / `verify-repair` | Whole-round repair gate / frozen targeted retest queue; both take `--task` |
| `begin-final` / `complete` / `apply` | Final selected-suite review, freeze result, conflict-checked local application |
| `status` / `report` / `cancel` / `resume` / `retest` | All take `--task`; resume may reconcile `--interrupted-case ID` |
| `recover-apply --task ID` | Reconcile an interrupted application; never overwrite unrelated later edits |

`source/` is the repair candidate. `targets/<round>/source/` is the disposable product repository where lifecycle tests may publish temporary Agents/workflows. They have separate Git metadata and separate state. Rebuild the site from the current target, configure its API origin, and rebind at each new round. Read `site` source to use its actual API-origin setting. Only credential configuration is reused through protected references/environment; do not copy the user's database, auth cache or mail connections into the target. Missing verified Runtime state is a preflight blocker to resolve through the product's normal setup, never by manufacturing admission receipts.

The active backend reports `data_root` from `/api/config/status`. Native tools may poll run status and fetch existing artifacts; polling does not resubmit queries. Record product run IDs and reconcile them after response loss. Cancelling the test runner alone does not prove a product run or SDK operation has stopped: cancel through the product UI/API and confirm its terminal state.

## Cases

Every case has `id`, `feature`, `operation`, `scenario`, `kind`, `mode`, `assertions`, optional `depends_on` and `impact_paths`. Operation names and existing regression files come from inventory. Features are `free-query`, `workflows`, `agent-management`, `fixed-agents`, `role-matching`, `plugins`, `settings`, `history`.

- `pytest` / `node-test`: explicit repository-relative `test_refs`; always `mode=offline`.
- `native`: host browser, Computer Use or connector interactions; include concrete `steps` and assertions. Save actual tool observations/screenshots, then a result JSON containing `status`, `code`, `layer` and run-directory-relative `evidence` paths. Do not record PASS just because no exception was visible.
- `browser`: declarative `actions`, compiled into a `.spec.mjs` runner. Locators accept `role`+`name`, `label`, `test_id` or `selector`. Actions: `navigate` with relative `path`, `click`, `fill`, `select`, `wait_for`, `expect_text`, `expect_count`, `screenshot`, `capture_json`, `capture_table`. Use UI selectors and API response pointers verified from current source, not guessed names. Configure `SAPBA_PLAYWRIGHT_MODULE` to an installed Playwright package if it is not normally resolvable.

Minimal offline example (test references are verified against inventory):

```json
[{"id":"free-query.lifecycle","feature":"free-query","operation":"recovery",
  "scenario":"查询刷新与恢复不重复提交","kind":"pytest","mode":"offline",
  "test_refs":["tests/test_free_query_lifecycle.py"],
  "assertions":["existing_regressions_pass"],
  "impact_paths":["src/sap_business_agents_platform/","site/src/"],"depends_on":[]}]
```

For meaningful business coverage, add the actual frontend scenarios rather than treating this offline example as live acceptance. Selected operations without cases are reported as coverage gaps.

## Live SAP recipes

`sample` and `baseline` have `{plan: <registered structured GET plan>}`. Use the current `sap_business_agents_platform.runtime_query_contract` schema. Credentials, URLs, SQL, write verbs and function imports are not allowed. A sample is a single bounded query of at most 100 rows. Field bindings use the entire string `$sample.Field`, preserving its type and the row's related key tuple. Baseline and anchor use the same frozen query and bindings. Each read has ten business GETs and 540 seconds for evidence, with a 600-second task boundary.

MCP tools: `test_inventory`, `test_prepare`, `test_start`, `test_status`, `test_sap_read`, `test_cancel`. Register with `install --register-mcp`; reload the host tools/new chat before expecting them. `test_sap_read` accepts only task ID, case ID and role (`sample`, `baseline`, `anchor`). It reads the already-frozen recipe, checks registered services/live schema through the existing Provider, owns credentials and persists a receipt. Repeated completed calls reuse that receipt; unknown calls are not replayed.

Capture frontend business rows as an `observation.json` containing `rows`, `scope` (the exact effective frozen baseline plan), `source_complete`, `source_truncated` and `captured_at`. `capture_json` takes `path`, `rows_pointer`, `complete_pointer`, optional `truncated_pointer`, and `scope`; `capture_table` takes row `selector`, field-to-cell-selector `fields`, `complete_selector` and `scope`. Only use completeness proven by the product; incomplete presentation is not an empty result.

`comparison` supports `key_set`, `key_subset`, `count_only`, `count_and_key_subset`, required `keys`, `required_fields`, and optional per-field `{type:decimal,tolerance:"0"}` checks. Independent baseline projection supports `field_map`, `group_by`, and `aggregates` with `count` or `sum`; all currency/unit dimensions of sums must be in `group_by`. Preserve SAP identifiers and leading zeros. No comparisons of totals across incomparable currencies or quantities.

After browser success, obtain the anchor, add `observation` to the pending result JSON and call `finish-case`. Live PASS is computed by the comparison helper, not accepted from a boolean supplied by Codex. Changed source data and incomplete evidence yield inconclusive results.

## Failure and repair evidence

Preserve failure code, layer, stable source location, screenshot/trace and run IDs. Reports omit business rows and secrets. Evidence files stay under ignored `.local-data/auto-test`, separately from immutable suites. Baselines are controller-owned artifacts outside the repair candidate; test code may not rewrite them.

Impact paths should name real dependencies. Missing/unknown mappings expand to the whole selected suite. Shared backend or UI changes can legitimately affect several categories. Full verification happens once at the end, not after each ordinary failure.
