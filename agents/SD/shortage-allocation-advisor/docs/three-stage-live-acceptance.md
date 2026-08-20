# Three-stage live SAP acceptance: shortage-allocation-advisor

## Verdict

`BLOCKED` / `executable=false`

- Case: `shortage-allocation-advisor-live-001`
- Tested at: `2026-08-20T08:57:26.766047+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `atp_availability_evidence`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:620331ab68cd243a953059e8990e383663c4a65ccdf807e1b871a091c64eb553`
- SAPBusinessAgents free query: `sha256:d92460daae1a3e934fdb45c788f718aebb77fbfc3ff7e570140a0cba50732b03`
- Adjudicated result: `sha256:620331ab68cd243a953059e8990e383663c4a65ccdf807e1b871a091c64eb553`
- Fixed Agent: `sha256:4d320452b0ecf71dbde59de79468d0444bd2999067c3187b7a36c5f644ca0200`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `date_from, date_to, material, plant` (values remain in ignored artifacts).
- Business-condition fields: `date_from, date_to, material, plant` (values remain in ignored artifacts).
- Accepted business grain: `sales_order, sales_order_item, schedule_line`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| range_order_items | API_SALES_ORDER_SRV | 2.0 | A_SalesOrderItem | 1 | 1 | SalesOrder, SalesOrderItem | true | true |
| range_schedule_lines | API_SALES_ORDER_SRV | 2.0 | A_SalesOrderScheduleLine | 2 | 1 | SalesOrder, SalesOrderItem, ScheduleLine | true | true |
| range_stock | API_MATERIAL_STOCK_SRV | 2.0 | A_MatlStkInAcctMod | 36 | 1 | Material, Plant, StorageLocation, Batch, Supplier, Customer, WBSElementInternalID, SDDocument, SDDocumentItem, InventorySpecialStockType, InventoryStockType | true | true |

Schema/query manifests:
- `range_order_items` schema `sha256:4fe5d6b08a6e1e4496ad0596cec20d554ee0cd51abe39b448aad750143549328`; query `sha256:4d5477902dfbc1d2e41f0514705b60d89d87f8ecb6c47753277a8778c1fcdacb`.
- `range_schedule_lines` schema `sha256:4fe5d6b08a6e1e4496ad0596cec20d554ee0cd51abe39b448aad750143549328`; query `sha256:1a36cc0c82ede3af419fad9c2fdd247a74add8bc76678475938e438d3bb8156f`.
- `range_stock` schema `sha256:5cfd9f82fd7abad22dfb6fb4354c9b26caaeb263fc36f8098befb1a2e19676f9`; query `sha256:c30faf833e1ed2b18ffe9b6fc25570986936a55f126ebba1c58d9c5fc5a6d33b`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
