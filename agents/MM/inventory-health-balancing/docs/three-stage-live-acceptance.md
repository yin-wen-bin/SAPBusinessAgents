# Three-stage live SAP acceptance: inventory-health-balancing

## Verdict

`PASS` / `executable=true`

- Case: `inventory-health-v4-expiry-fix-fg29-20260823`
- Tested at: `2026-08-24T14:04:28.218015+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `1`
- Required limitations preserved: `batch_expiry_date_missing`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:795e3db52dc35fa9ba6876f4a6ac877348dcc5bc19bb555678db3c5150495ecc`
- SAPBusinessAgents free query: `sha256:8f7ecf23d717d290371d6f02d94bb0c5595c75afcf46398402e4bce932ed8ef0`
- Adjudicated result: `sha256:795e3db52dc35fa9ba6876f4a6ac877348dcc5bc19bb555678db3c5150495ecc`
- Fixed Agent: `sha256:e2da0f9ef4dcaa5613874632a41e46a16052ae0cbd3bc6f44f9be2e3d3c053ed`
- Fixed comparison: `sha256:208364b12c7622ff17f5784b4115a04df809fe7dc5177f186f49936e193122e9`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `expiry_days, material, obsolete_days, plant, slow_moving_days, storage_location` (values remain in ignored artifacts).
- Business-condition fields: `expiry_days, material, obsolete_days, plant, slow_moving_days, storage_location` (values remain in ignored artifacts).
- Accepted business grain: `material, plant, storage_location`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| inventory_stock_initial | API_MATERIAL_STOCK_SRV | 2.0 | A_MatlStkInAcctMod | 6 | 1 | Material, Plant, StorageLocation, Batch, Supplier, Customer, WBSElementInternalID, SDDocument, SDDocumentItem, InventorySpecialStockType, InventoryStockType | true | true |
| inventory_movement_items | API_MATERIAL_DOCUMENT_SRV | 2.0 | A_MaterialDocumentItem | 7 | 1 | MaterialDocumentYear, MaterialDocument, MaterialDocumentItem | true | true |
| inventory_movement_headers_001 | API_MATERIAL_DOCUMENT_SRV | 2.0 | A_MaterialDocumentHeader | 7 | 1 | MaterialDocumentYear, MaterialDocument | true | true |
| inventory_stock_confirmation | API_MATERIAL_STOCK_SRV | 2.0 | A_MatlStkInAcctMod | 6 | 1 | Material, Plant, StorageLocation, Batch, Supplier, Customer, WBSElementInternalID, SDDocument, SDDocumentItem, InventorySpecialStockType, InventoryStockType | true | true |
| inventory_batch_expiry | API_BATCH_SRV | 2.0 | Batch | 8 | 1 | Material, BatchIdentifyingPlant, Batch | true | true |

Schema/query manifests:
- `inventory_stock_initial` schema `sha256:5cfd9f82fd7abad22dfb6fb4354c9b26caaeb263fc36f8098befb1a2e19676f9`; query `sha256:2ac69dbcf42520058e83e2b80801e39a115e7b438e954581f49a4789d231d374`.
- `inventory_movement_items` schema `sha256:4de095109c47983878622572c2d95c6383e7c2fbf520cb15241582078d28852e`; query `sha256:9ca87a61e39e9a8dcaa40fc003c7d44976d34d1403abb6af71a67b03f43fb14f`.
- `inventory_movement_headers_001` schema `sha256:4de095109c47983878622572c2d95c6383e7c2fbf520cb15241582078d28852e`; query `sha256:9b03870b5058c016b356cf17ed46d89258f3197180504c83ab7bac14ea465447`.
- `inventory_stock_confirmation` schema `sha256:5cfd9f82fd7abad22dfb6fb4354c9b26caaeb263fc36f8098befb1a2e19676f9`; query `sha256:2ac69dbcf42520058e83e2b80801e39a115e7b438e954581f49a4789d231d374`.
- `inventory_batch_expiry` schema `sha256:d5814a007e939666797721b01b874cb653910e855ea5c3ceeacc99eabd85338d`; query `sha256:c0df700de0be2a254aa4cbf492d8861e4f3ad28ce7b4bf4d0b887f5bf0358427`.

- Test-data qualification: `qualified`.
- Qualification evidence: `inventory_stock_initial, inventory_movement_items, inventory_movement_headers_001, inventory_stock_confirmation, inventory_batch_expiry`; reasons: `none`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
