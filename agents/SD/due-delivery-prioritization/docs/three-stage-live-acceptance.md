# Three-stage live SAP acceptance: due-delivery-prioritization

## Verdict

`PASS` / `executable=true`

- Case: `due-delivery-prioritization-live-001`
- Tested at: `2026-08-20T05:19:39.268273+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `1`
- Required limitations preserved: `current_stock_not_historical_atp`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:f5a60643173bc1af270d225f7f3fbb84041ac8ecdda9c4c5888e5472cfe8955f`
- SAPBusinessAgents free query: `sha256:38800157f82ca28cb31d008d6cb5155276fa599787981df2590b83d807a13403`
- Adjudicated result: `sha256:f5a60643173bc1af270d225f7f3fbb84041ac8ecdda9c4c5888e5472cfe8955f`
- Fixed Agent: `sha256:f5a60643173bc1af270d225f7f3fbb84041ac8ecdda9c4c5888e5472cfe8955f`
- Fixed comparison: `sha256:312883f683afca34ccc2798eeb704b1a65d36beb9ead64157c9b723cb1cdeafb`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `date_from, date_to, plant, sales_organization` (values remain in ignored artifacts).
- Business-condition fields: `date_from, date_to, plant, sales_organization` (values remain in ignored artifacts).
- Accepted business grain: `sales_order, sales_order_item, schedule_line`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| range_order_headers | API_SALES_ORDER_SRV | 2.0 | A_SalesOrder | 1 | 1 | SalesOrder | true | true |
| range_order_items | API_SALES_ORDER_SRV | 2.0 | A_SalesOrderItem | 1 | 1 | SalesOrder, SalesOrderItem | true | true |
| range_schedule_lines | API_SALES_ORDER_SRV | 2.0 | A_SalesOrderScheduleLine | 2 | 1 | SalesOrder, SalesOrderItem, ScheduleLine | true | true |
| range_stock | API_MATERIAL_STOCK_SRV | 2.0 | A_MatlStkInAcctMod | 36 | 1 | Material, Plant, StorageLocation, Batch, Supplier, Customer, WBSElementInternalID, SDDocument, SDDocumentItem, InventorySpecialStockType, InventoryStockType | true | true |

Schema/query manifests:
- `range_order_headers` schema `sha256:4fe5d6b08a6e1e4496ad0596cec20d554ee0cd51abe39b448aad750143549328`; query `sha256:69c6d267632365f3d78cd90909d8b8fbc7593dc87cc67091a110d6b120c13bcc`.
- `range_order_items` schema `sha256:4fe5d6b08a6e1e4496ad0596cec20d554ee0cd51abe39b448aad750143549328`; query `sha256:4d5477902dfbc1d2e41f0514705b60d89d87f8ecb6c47753277a8778c1fcdacb`.
- `range_schedule_lines` schema `sha256:4fe5d6b08a6e1e4496ad0596cec20d554ee0cd51abe39b448aad750143549328`; query `sha256:1a36cc0c82ede3af419fad9c2fdd247a74add8bc76678475938e438d3bb8156f`.
- `range_stock` schema `sha256:5cfd9f82fd7abad22dfb6fb4354c9b26caaeb263fc36f8098befb1a2e19676f9`; query `sha256:c30faf833e1ed2b18ffe9b6fc25570986936a55f126ebba1c58d9c5fc5a6d33b`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
