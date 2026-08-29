# Three-stage live SAP acceptance: gr-ir-clearing 0.2.0

## Verdict

`PASS` / `executable=true`

- Tested at: `2026-08-29T05:34:59.867989+00:00`.
- Direct baseline runtime: `codex_app_direct_sap`.
- Used SAPBusinessAgents for the direct baseline: `false`.
- Free-query comparison: `MATCH`.
- Fixed-Agent comparison: `MATCH`.
- SAP write operations: none.

## Sanitized acceptance scope

The case used an exact company code, GR/IR account, and one-day activity window in
ignored local artifacts. The activity window discovered candidate purchase-order
items; each candidate was then expanded through the cutoff date using complete
GR/IR G/L, purchase-order item, material-document item and header, and supplier-
invoice item and header evidence.

The accepted business grain is one record per purchase order and item. The result
contained 72 records: 72 matched, 0 confirmed follow-up, and 0 inconclusive. All
three paths reported `source_complete=true` and complete business evidence.

## Independent direct-SAP baseline

- Every SAP request used HTTP GET.
- Live metadata was read for every entity before execution.
- All 23 bounded source chunks used complete stable-key paging.
- The six entity sets returned 288 G/L rows across candidate and history reads,
  72 purchase-order items, 72 material-document items, 11 material-document
  headers, 72 supplier-invoice items, and 11 supplier-invoice headers.
- The baseline business logic independently applied signed receipt quantity,
  signed invoice or credit quantity, signed company-code-currency G/L amount,
  quantity tolerance `0.001`, and amount tolerance `0.01`.

## Codex free-query path

- Run: `run_17b5f9dcebca4a59`.
- The Harness used live catalog discovery, live schemas, validation, GET-only SAP
  execution, evidence assessment, bounded pure computation, and final-report
  validation.
- Two initial order expressions were rejected before execution and corrected from
  live schema evidence. Rejected plans did not reach SAP.
- Seven executed evidence plans were complete and the final result matched the
  independent baseline at record, fact, unit, currency, metric, limitation, and
  completeness level.

## Fixed-Agent path

- Run: `acceptance_f998462d81fe465e`.
- Rule: `gr_ir_clearing_deterministic_v2`.
- The deterministic Agent executed only its declared GET-only evidence plan and
  produced the same 72 records and four metrics as the baseline.
- The business UI exposes only confirmed follow-up and evidence-incomplete lists;
  the complete reconciliation remains a hidden acceptance table and downloadable
  audit CSV.

## Evidence hashes

- Direct baseline: `sha256:1dad0132ac54844e52d4339e50af327e4cbaed7a100dd1c1db8f4db1d2808a8d`.
- Free query: `sha256:a336b8cade77f2befb0f913093aff9c023157c02d722f3bce59dee265e6f11c2`.
- Adjudicated result: `sha256:1dad0132ac54844e52d4339e50af327e4cbaed7a100dd1c1db8f4db1d2808a8d`.
- Fixed Agent: `sha256:c727e5bbe5f97eb9aa8b2bd09e72800636075fa5160a651da7d40c1cb980b889`.
- Fixed comparison: `sha256:118dee099766a7bd99d63fd16799eeca25eaf7ceee4855f605857ed7351228c7`.

Raw SAP rows, URLs, credentials, connection details, business identifiers, and
amounts remain only in ignored local artifacts.
