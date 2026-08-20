# Three-stage live SAP acceptance: production-variance-analysis

## Verdict

`BLOCKED` / `executable=false`

- Case: `production-variance-analysis-live-001`
- Tested at: `2026-08-20T07:45:35.827526+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `production_cost_evidence, production_cost_relationship`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:cdb2f30a571e1f8bc14b56bfc0fbb9b1c1243c88b43e90b06cd4929e231eef00`
- SAPBusinessAgents free query: `sha256:4d9afe1a85b0fc094d152253e5d821d0ca2fdcb24d9149be0cef65f845b9bd62`
- Adjudicated result: `sha256:cdb2f30a571e1f8bc14b56bfc0fbb9b1c1243c88b43e90b06cd4929e231eef00`
- Fixed Agent: `sha256:cdb2f30a571e1f8bc14b56bfc0fbb9b1c1243c88b43e90b06cd4929e231eef00`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `manufacturing_order` (values remain in ignored artifacts).
- Business-condition fields: `manufacturing_order` (values remain in ignored artifacts).
- Accepted business grain: `manufacturing_order, cost_element`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| production_items | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrderItem_2 | 1 | 1 | ManufacturingOrder, ManufacturingOrderItem | true | true |
| production_operations | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrderOperation_2 | 2 | 1 | OrderInternalBillOfOperations, OrderIntBillOfOperationsItem | true | true |
| production_components | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrderComponent_2 | 9 | 1 | Reservation, ReservationItem | true | true |
| production_movements | API_MATERIAL_DOCUMENT_SRV | 2.0 | A_MaterialDocumentItem | 0 | 1 | MaterialDocumentYear, MaterialDocument, MaterialDocumentItem | true | true |
| production_costs | API_OPLACCTGDOCITEMCUBE_SRV | 2.0 | A_OperationalAcctgDocItemCube | 0 | 1 | CompanyCode, FiscalYear, AccountingDocument, AccountingDocumentItem | true | true |

Schema/query manifests:
- `production_items` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:edd78e253a95271d1093bb8c6a39c898db0923029d8514846b35916160bee21c`.
- `production_operations` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:564f1a9579f0b50a8e049563b0c0eca4fce7c1304b767b5d16814edc6412d7c1`.
- `production_components` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:7c3056c0cf8d4af8c1d8faaa6f2790ddc3647c8fe48c4fe38a18e2acc865e2c4`.
- `production_movements` schema `sha256:4de095109c47983878622572c2d95c6383e7c2fbf520cb15241582078d28852e`; query `sha256:1c84a6e398c872c2b6372f609ffafc60ad0031a546dbc7e961b891432c129844`.
- `production_costs` schema `sha256:65f619d00b9b12395e17fdff88b15260b6256e80405991dd2279c133c1ec6b67`; query `sha256:85952e37fcc1c305fdba914c8405e62b7e98263bf7888095e8869b043bbcddd5`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
