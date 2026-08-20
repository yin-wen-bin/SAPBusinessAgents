# Three-stage live SAP acceptance: product-cost-variance

## Verdict

`BLOCKED` / `executable=false`

- Case: `product-cost-variance-live-001`
- Tested at: `2026-08-20T08:22:30.673801+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `standard_cost_evidence`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:c53fe026dffdfe735a5de448371e87ff6400739fb1eb27791b76c14b351bd3d1`
- SAPBusinessAgents free query: `sha256:560a31a1fc8d9b0104f5ebc91ca6bd806e1fdc50ae9ead441cda9c891d15bd75`
- Adjudicated result: `sha256:c53fe026dffdfe735a5de448371e87ff6400739fb1eb27791b76c14b351bd3d1`
- Fixed Agent: `sha256:de9ea44b1355310c48d82de6494b7869ea96d3ee6a1a52d788f74280ce8c1a5a`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `company_code, fiscal_year, manufacturing_order, material, period, valuation_area` (values remain in ignored artifacts).
- Business-condition fields: `company_code, fiscal_year, manufacturing_order, material, period, valuation_area` (values remain in ignored artifacts).
- Accepted business grain: `company_code, manufacturing_order, material, fiscal_year, period`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| product_order | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrder_2 | 1 | 1 | ManufacturingOrder | true | true |
| product_actual | API_OPLACCTGDOCITEMCUBE_SRV | 2.0 | A_OperationalAcctgDocItemCube | 0 | 1 | CompanyCode, FiscalYear, AccountingDocument, AccountingDocumentItem | true | true |

Schema/query manifests:
- `product_order` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:1785f6483cf19f90dfdafc56519c4d790cb6da8f6a51e9a0f1c205f02dbcb9c5`.
- `product_actual` schema `sha256:65f619d00b9b12395e17fdff88b15260b6256e80405991dd2279c133c1ec6b67`; query `sha256:575011f2509cfa35287939b1d849d9bd79adc7820f2ce97c5eb5e4adfc14962a`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
