# Three-stage live SAP acceptance: production-order-monitoring

## Current version

`0.2.0` — `PASS` / `executable=true`

Version 0.2.0 adds normalized order, status, operation, component, and material-movement outputs plus a richer bilingual presentation. The revised contract passed a fresh three-stage live comparison; the prior version's hashes were not reused.

- Case: `production-order-monitoring-live-002`
- Tested at: `2026-09-08T07:18:50.040305+08:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query run: `run_4008aee171414771` / `completed` / `MATCH`
- Fixed-Agent run: `acceptance_9af8ec7d9b984be6` / `completed` / `MATCH`
- Normalized business records: `2`
- Aggregate scope: `2` operations, `9` components, `0` material-movement items
- Required limitations preserved: `none`
- SAP write operations: none; every request was `GET`

## Current 0.2.0 evidence hashes

- Agent execution digest: `sha256:33b3faac34c06de3a6973b70ca967886a726ab8aad4b94add2d0f5fb01f064ec`
- Acceptance contract digest: `sha256:2ff8a143f20751a28b765e3de7301516fe1fbdd76f81d7e0690cebb4735dd8e3`
- Codex direct baseline: `sha256:ed2c9d9f750998ac772f5ca9db63ad63fb602f88541014fc6a81907190f391a8`
- SAPBusinessAgents free query: `sha256:7f8c6afedc8d082c75242862141f896c9821c2e75f2fc42b0cc8c9391d7665fd`
- Adjudicated result: `sha256:ed2c9d9f750998ac772f5ca9db63ad63fb602f88541014fc6a81907190f391a8`
- Fixed Agent: `sha256:302be3ff5259eccc7511f8a35305198ca8699c605f2f23d78e2d2a4284623c51`
- Fixed comparison: `sha256:606afd717e27724b29f6b8a99ce43aefd61f4638d5e540e485f71d4684daa62f`

## Historical 0.1.0 verdict

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

## Historical 0.1.0 evidence hashes

- Codex direct baseline: `sha256:cd61a8d1cc3e745b3b406214414062e58a2c01cefdab93efdb3dbc29ec5e9576`
- SAPBusinessAgents free query: `sha256:abbe4908757c747a03f7390f6a136f1910f1feb7e8731ce43f5b4a813a3679ef`
- Adjudicated result: `sha256:cd61a8d1cc3e745b3b406214414062e58a2c01cefdab93efdb3dbc29ec5e9576`
- Fixed Agent: `sha256:cd61a8d1cc3e745b3b406214414062e58a2c01cefdab93efdb3dbc29ec5e9576`
- Fixed comparison: `sha256:1cafb94a01b27b0d06e21e82e68b3b8c86fc061fa50d5884a413fc507195d166`

## Current 0.2.0 sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `manufacturing_order` (values remain in ignored artifacts).
- Business-condition fields: `manufacturing_order` (values remain in ignored artifacts).
- Accepted business grain: `manufacturing_order, operation`.

## Current 0.2.0 direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| production_order | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrder_2 | 1 | 1 | ManufacturingOrder | true | true |
| production_items | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrderItem_2 | 1 | 1 | ManufacturingOrder, ManufacturingOrderItem | true | true |
| production_statuses | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrderStatus_2 | 5 | 1 | ManufacturingOrder, StatusCode | true | true |
| production_operations | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrderOperation_2 | 2 | 1 | OrderInternalBillOfOperations, OrderIntBillOfOperationsItem | true | true |
| production_components | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrderComponent_2 | 9 | 1 | Reservation, ReservationItem | true | true |
| production_movements | API_MATERIAL_DOCUMENT_SRV | 2.0 | A_MaterialDocumentItem | 0 | 1 | MaterialDocumentYear, MaterialDocument, MaterialDocumentItem | true | true |

Schema/query manifests:
- `production_order` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:104fb3eeac58b3848d92b9f948d41eff34abf4123db96b49987f1df4b741956b`.
- `production_items` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:58976cc017d9e95f45595d6b8bfb5bdda94fa0e94648c446118e9ecb40f3e284`.
- `production_statuses` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:4ae6b7d3432fb1fcf91b24c5918ed726d9cd5a323ce94c73dd2db9f69e40df6c`.
- `production_operations` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:0bf1dbaa348b9180a2fd9aa734f4d5681a5fc853e7f301e7968f226c91b8c925`.
- `production_components` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:b059d7d4b859dbdb0453232bd69982a6277bee96c3591eff9fae6e2f7353bda0`.
- `production_movements` schema `sha256:4de095109c47983878622572c2d95c6383e7c2fbf520cb15241582078d28852e`; query `sha256:0a20469d8b25df2c446df44565d9ee87d66e4951cbf624b9962ecd0e40f471ec`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
