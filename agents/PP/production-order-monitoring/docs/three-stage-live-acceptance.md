# Three-stage live SAP acceptance: production-order-monitoring

## Verdict

`PASS` / `executable=true`

- Case: `production-order-monitoring-live-001`
- Tested at: `2026-08-20T08:05:03.419526+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `2`
- Required limitations preserved: `none`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:cd61a8d1cc3e745b3b406214414062e58a2c01cefdab93efdb3dbc29ec5e9576`
- SAPBusinessAgents free query: `sha256:abbe4908757c747a03f7390f6a136f1910f1feb7e8731ce43f5b4a813a3679ef`
- Adjudicated result: `sha256:cd61a8d1cc3e745b3b406214414062e58a2c01cefdab93efdb3dbc29ec5e9576`
- Fixed Agent: `sha256:cd61a8d1cc3e745b3b406214414062e58a2c01cefdab93efdb3dbc29ec5e9576`
- Fixed comparison: `sha256:1cafb94a01b27b0d06e21e82e68b3b8c86fc061fa50d5884a413fc507195d166`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `manufacturing_order` (values remain in ignored artifacts).
- Business-condition fields: `manufacturing_order` (values remain in ignored artifacts).
- Accepted business grain: `manufacturing_order, operation`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| production_order | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrder_2 | 1 | 1 | ManufacturingOrder | true | true |
| production_items | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrderItem_2 | 1 | 1 | ManufacturingOrder, ManufacturingOrderItem | true | true |
| production_statuses | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrderStatus_2 | 5 | 1 | ManufacturingOrder, StatusCode | true | true |
| production_operations | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrderOperation_2 | 2 | 1 | OrderInternalBillOfOperations, OrderIntBillOfOperationsItem | true | true |
| production_components | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrderComponent_2 | 9 | 1 | Reservation, ReservationItem | true | true |
| production_movements | API_MATERIAL_DOCUMENT_SRV | 2.0 | A_MaterialDocumentItem | 0 | 1 | MaterialDocumentYear, MaterialDocument, MaterialDocumentItem | true | true |

Schema/query manifests:
- `production_order` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:f4300d53c4b9134db8a9a5032d0ecd2f3fa1909cca73918f1b89345fc04176be`.
- `production_items` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:edd78e253a95271d1093bb8c6a39c898db0923029d8514846b35916160bee21c`.
- `production_statuses` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:81eb194b04aabf711e3c280947998baf565b1c1b664f5ac96276f8a2d4bc58f5`.
- `production_operations` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:564f1a9579f0b50a8e049563b0c0eca4fce7c1304b767b5d16814edc6412d7c1`.
- `production_components` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:7c3056c0cf8d4af8c1d8faaa6f2790ddc3647c8fe48c4fe38a18e2acc865e2c4`.
- `production_movements` schema `sha256:4de095109c47983878622572c2d95c6383e7c2fbf520cb15241582078d28852e`; query `sha256:1c84a6e398c872c2b6372f609ffafc60ad0031a546dbc7e961b891432c129844`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
