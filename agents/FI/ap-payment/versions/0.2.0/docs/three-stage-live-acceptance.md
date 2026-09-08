# Three-stage live SAP acceptance: ap-payment

## Verdict

`PASS` / `executable=true`

- Case: `ap-payment-live-001`
- Tested at: `2026-08-19T16:53:02.948562+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `6`
- Required limitations preserved: `bank_settlement_not_proven, payment_run_and_bank_master_evidence`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:1eea802a5ff8c9a67e42126bf74880ab8a429be25f3b9dc26a5cfab904773065`
- SAPBusinessAgents free query: `sha256:8794d72192043679af8bad0c1c14b0db3236a73f0769093c5cbff41100a9c51c`
- Adjudicated result: `sha256:1eea802a5ff8c9a67e42126bf74880ab8a429be25f3b9dc26a5cfab904773065`
- Fixed Agent: `sha256:0e9e58d83b63a6e526cb024242d27410ad6a6d86684205a2885430b1a3591f9e`
- Fixed comparison: `sha256:b2a6b9251dabd592910c57529a2dc8b47559f89a2131604eddc31325b94b7d2b`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `as_of, company_code, supplier` (values remain in ignored artifacts).
- Business-condition fields: `as_of, company_code, financial_account_type, historical_open_rule, posting_date_operator, supplier` (values remain in ignored artifacts).
- Accepted business grain: `company_code, fiscal_year, accounting_document, accounting_document_item`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
