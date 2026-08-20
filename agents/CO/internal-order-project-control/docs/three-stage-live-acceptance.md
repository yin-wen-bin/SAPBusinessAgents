# Three-stage live SAP acceptance: internal-order-project-control

## Verdict

`BLOCKED` / `executable=false`

- Case: `internal-order-project-control-live-001`
- Tested at: `2026-08-20T08:46:57.717907+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `budget_evidence, commitment_evidence, control_object_not_found, master_evidence, plan_evidence`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:4fdd3fe60659759f1eb79074ffec18c615a3a00013e950449d05f37291b22c97`
- SAPBusinessAgents free query: `sha256:4fdd3fe60659759f1eb79074ffec18c615a3a00013e950449d05f37291b22c97`
- Adjudicated result: `sha256:4fdd3fe60659759f1eb79074ffec18c615a3a00013e950449d05f37291b22c97`
- Fixed Agent: `sha256:cec9e64dba5e41998e033e54b36fa016672307e5b19be508403681b2dd95c450`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `company_code, fiscal_year, object_id, object_type, planning_category` (values remain in ignored artifacts).
- Business-condition fields: `company_code, fiscal_year, object_id, object_type, planning_category` (values remain in ignored artifacts).
- Accepted business grain: `company_code, object_type, object_id, fiscal_year`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| internal_order_actual | API_OPLACCTGDOCITEMCUBE_SRV | 2.0 | A_OperationalAcctgDocItemCube | 1 | 1 | CompanyCode, FiscalYear, AccountingDocument, AccountingDocumentItem | true | true |
| internal_order_plan | API_FINPLANNINGENTRYITEM_SRV | 2.0 | A_FinPlanningEntryItem | 0 | 1 | ID | true | true |

Schema/query manifests:
- `internal_order_actual` schema `sha256:65f619d00b9b12395e17fdff88b15260b6256e80405991dd2279c133c1ec6b67`; query `sha256:3768db0c3f95401f3a607c7401f2f9d108b426173469c656944f47ce6e33622b`.
- `internal_order_plan` schema `sha256:2e3a3a0b113bf55428ffcddf129bc27ec1bee9b05f5da17f39ae308b4183eacd`; query `sha256:71241692ce764c33604d314a0c4ded970ba3ccc7e68e1b46c9843705966852c4`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
