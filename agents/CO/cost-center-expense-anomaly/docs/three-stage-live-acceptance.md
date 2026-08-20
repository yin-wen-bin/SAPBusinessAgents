# Three-stage live SAP acceptance: cost-center-expense-anomaly

## Verdict

`BLOCKED` / `executable=false`

- Case: `cost-center-expense-anomaly-live-001`
- Tested at: `2026-08-20T07:21:48.654922+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `plan_evidence_missing`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:b473579bef2af5acbbf3af2a34e6b875efff6afc120b39bad7d46901cc1d6cd0`
- SAPBusinessAgents free query: `sha256:82c0d39db4d27a3635edf41bd6972c49fff2c7c6bcd1968357bb59ce66759d8c`
- Adjudicated result: `sha256:b473579bef2af5acbbf3af2a34e6b875efff6afc120b39bad7d46901cc1d6cd0`
- Fixed Agent: `sha256:21c1f6668afaf9b4d42e9d5a2430562c105bf02e2da630d8a8b4ddfc59167a29`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `company_code, controlling_area, cost_center, fiscal_year, period_from, period_to, planning_category, variance_threshold_pct` (values remain in ignored artifacts).
- Business-condition fields: `company_code, controlling_area, cost_center, fiscal_year, period_from, period_to, planning_category, variance_threshold_pct` (values remain in ignored artifacts).
- Accepted business grain: `company_code, controlling_area, cost_center, fiscal_year, period_from, period_to`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| cost_center_actual | API_OPLACCTGDOCITEMCUBE_SRV | 2.0 | A_OperationalAcctgDocItemCube | 4 | 1 | CompanyCode, FiscalYear, AccountingDocument, AccountingDocumentItem | true | true |
| cost_center_master | API_COSTCENTER_SRV | 2.0 | A_CostCenter | 1 | 1 | ControllingArea, CostCenter, ValidityEndDate | true | true |
| cost_center_plan | API_FINPLANNINGENTRYITEM_SRV | 2.0 | A_FinPlanningEntryItem | 0 | 1 | ID | true | true |

Schema/query manifests:
- `cost_center_actual` schema `sha256:65f619d00b9b12395e17fdff88b15260b6256e80405991dd2279c133c1ec6b67`; query `sha256:2c2a4691dc12b635cdd0c02fa50c89c9ef6f4a58aa9662d1cf6103065212c3e5`.
- `cost_center_master` schema `sha256:8ed827490f147452e6e62638653b0643735a24032b69a83080cc5cbd2755efca`; query `sha256:616729cd43b7ef8018079c7077867766ba2a64afbb90bff29e59c6c1431ad5c3`.
- `cost_center_plan` schema `sha256:2e3a3a0b113bf55428ffcddf129bc27ec1bee9b05f5da17f39ae308b4183eacd`; query `sha256:68cf7923f2e7e2b911739c661bcd07691498a054959aa10b2cc42be6775fd4ec`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
