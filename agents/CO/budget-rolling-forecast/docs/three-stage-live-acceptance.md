# Three-stage live SAP acceptance: budget-rolling-forecast

## Verdict

`BLOCKED` / `executable=false`

- Case: `budget-rolling-forecast-live-001`
- Tested at: `2026-08-20T07:45:39.722374+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `budget_evidence_missing`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:198d6907df94f0a7b9c085509aad7c3d3a866cbf9c44f0d310f5c09594eeb1b4`
- SAPBusinessAgents free query: `sha256:2302bd42a14de21267a4c146d3bffa9cb3b6c21630cc0dc59a9ec546188a894c`
- Adjudicated result: `sha256:198d6907df94f0a7b9c085509aad7c3d3a866cbf9c44f0d310f5c09594eeb1b4`
- Fixed Agent: `sha256:79c53bab4e46b4b1d758637fc1e863e8f9182c5e0afd5880dd4bc2a61f260e2a`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `company_code, cost_center, current_period, fiscal_year, planning_category, risk_threshold_pct` (values remain in ignored artifacts).
- Business-condition fields: `company_code, cost_center, current_period, fiscal_year, planning_category, risk_threshold_pct` (values remain in ignored artifacts).
- Accepted business grain: `company_code, cost_center, fiscal_year, current_period`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| forecast_actual | API_OPLACCTGDOCITEMCUBE_SRV | 2.0 | A_OperationalAcctgDocItemCube | 4 | 1 | CompanyCode, FiscalYear, AccountingDocument, AccountingDocumentItem | true | true |
| forecast_plan | API_FINPLANNINGENTRYITEM_SRV | 2.0 | A_FinPlanningEntryItem | 0 | 1 | ID | true | true |

Schema/query manifests:
- `forecast_actual` schema `sha256:65f619d00b9b12395e17fdff88b15260b6256e80405991dd2279c133c1ec6b67`; query `sha256:e290b0e597a9b7df536b578b982c95b3132fd49642caa6d0674536ec3d960f97`.
- `forecast_plan` schema `sha256:2e3a3a0b113bf55428ffcddf129bc27ec1bee9b05f5da17f39ae308b4183eacd`; query `sha256:3644db8dd67456eee20f19135b6924374b8176d33c2d0800c6a714f2d37b7496`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
