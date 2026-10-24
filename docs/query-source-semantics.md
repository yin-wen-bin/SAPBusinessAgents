# Shared query and acceptance source semantics

Codex and WorkBuddy use the same platform checks for source scope, true row grain,
economic category and comparable coverage. SDK request parameters, permissions, native
terminal protocols and task budgets remain unchanged. These are new business checks,
not new SDK authentication or model-compatibility probes.

## Responsibilities and binding

- `config/sap-source-semantics.json` declares reviewed source roles, full keys,
  amount/currency pairs, coverage and classification attributes. API explanations live
  under `data/api_skills/`; tenant values are not hardcoded in either place.
- `source_semantics.py` implements generic checks, lossless classification, historical
  state and ledger/category/currency-separated summaries. No model or SAP calls occur there.
- Each Broker session freezes the complete catalog and implementation hash. A changed
  binding blocks reads and late reports. The Provider holds the same immutable catalog.
- The shared Provider validates actual metadata first, then source projection. Identifier
  lookups do not claim item grain. Material detail requires the true key and category
  dimensions. Explicit ledger summaries need confirmed scope; no implicit 0L default.
- Source completeness, business completeness and comparison applicability are distinct.
  Original rows are never corrected, discarded, silently deduplicated or re-signed.

## New financial acceptance contracts

For a new or behavior-changing financial Agent, add the reviewed definition under
`execution.acceptance.sourceSemantics` (Agent schemaVersion does not change):

```json
{
  "version": "1.0",
  "view": "account_items",
  "ledgerScope": null,
  "categories": ["posted_normal", "posted_special", "noted_item"],
  "configurationTime": "posting_date"
}
```

Views are `account_items`, `ledger_lines` or `ledger_account_summary`. This field is a
business requirement, not read authorization. A ledger restriction must also satisfy the
original frozen input scope and provider metadata. `current` configuration applicability
must not be used to claim a historical posting-date conclusion.

Declare `record_kind` as a schema-typed fact with the reviewed enum. Keep source rows
represented at the declared grain; projection validation rejects dropped rows and category
relabeling. Multi-ledger scalar decimal totals are rejected; keep ledger-separated measures.
Multiple source views or overlapping scopes need a reviewed mapping: the platform does
not invent one or accept "take the last result". Unknown classifications/configuration
time, missing keys, conflicting records and unproved coverage remain explicit blockers.

Existing stored manifests, trials, Campaign reports and PASS are not migrated or rewritten.
An old financial draft without this reviewed definition must be revised and trialled before
a new formal acceptance can start. Merely adding a definition does not establish business
correctness. No source is automatically substituted and no Campaign is automatically retried.

## Evidence, independence and regression

Native transport remains Provider-specific; source validation, report feedback and semantic
comparison are shared. An independent baseline retains its empty candidate-free workspace,
independent reads, anchors, readonly audit and full report checks. A different source's
coverage/grain is BLOCKED, not MATCH, even when its numbers happen to agree. Genuine
same-definition business differences remain MISMATCH/FAIL.

Offline regressions use immutable saved evidence and synthetic source/Runtime adapters.
Synthetic verdicts live only in isolated test databases, never the production qualification
ledger. Real supplier/document identifiers appear only in ignored replay inputs, not generic
code or catalog rules. Never rerun live SAP or models to compensate for a binding difference.

## Deployment

Freeze the tested source/catalog together. Include whole responsibility files and the catalog
in dependency manifests; do not change the digest algorithm or use AST exemptions. Audit
SDK integration, model protocol and business-evaluation applicability separately. Preserve
original PASS even when a current business dependency is different. A missing valid SDK
certificate blocks rollout; offline tests cannot become a new real business PASS.

After tests, require the existing deployment lock, maintenance barrier, idle writers and
confirmed process cleanup. Verify only health/version/configuration GETs. Rollback retains
history and does not downgrade records; do not start acceptance, SAP reads or model probes.
