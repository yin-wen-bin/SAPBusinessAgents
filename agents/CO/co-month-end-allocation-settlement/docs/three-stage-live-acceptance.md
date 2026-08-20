# Three-stage live SAP acceptance: co-month-end-allocation-settlement

## Verdict

`BLOCKED` / `executable=false`

- Case: `co-month-end-allocation-settlement-live-001`
- Tested at: `2026-08-20T08:13:43.407126+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `allocation_cycle_evidence, object_status_evidence, settlement_rule_evidence`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:d72756725392458110700ac45601aa74fd2417c0301658fd3205b580511e9430`
- SAPBusinessAgents free query: `sha256:39c6f1f3b94b4a30224fb4daa875051d9f0e6b60f1e9a8d21ce1fd842b8fee52`
- Adjudicated result: `sha256:d72756725392458110700ac45601aa74fd2417c0301658fd3205b580511e9430`
- Fixed Agent: `sha256:85e481a75a68c56dd616cf01b8a72325d68fafeed06234b27e41d443b129c491`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `allocation_cycle, company_code, controlling_area, fiscal_year, internal_order, period` (values remain in ignored artifacts).
- Business-condition fields: `allocation_cycle, company_code, controlling_area, fiscal_year, internal_order, period` (values remain in ignored artifacts).
- Accepted business grain: `company_code, controlling_area, fiscal_year, period, internal_order`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| allocation_posting | API_OPLACCTGDOCITEMCUBE_SRV | 2.0 | A_OperationalAcctgDocItemCube | 1 | 1 | CompanyCode, FiscalYear, AccountingDocument, AccountingDocumentItem | true | true |

Schema/query manifests:
- `allocation_posting` schema `sha256:65f619d00b9b12395e17fdff88b15260b6256e80405991dd2279c133c1ec6b67`; query `sha256:822a1b547cdc406fd9c0f93457857cb1053db619bb1226960d88b4d4ad152fd7`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
