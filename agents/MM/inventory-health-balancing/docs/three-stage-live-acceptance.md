# Three-stage live SAP acceptance: inventory-health-balancing

## Verdict

`PASS` / `executable=true`

- Case: `inventory-health-balancing-live-002`
- Tested at: `2026-08-23T05:51:12.385559+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `1`
- Required limitations preserved: `none`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:a58f4d1d9da3fa5b7c2429f6a49f53637a9fa556238c2ea8961b30f09c505e8b`
- SAPBusinessAgents free query: `sha256:666755faabb786d3a1999072b66893bf5cfe180c527a8d995d224be954bab6f0`
- Adjudicated result: `sha256:a58f4d1d9da3fa5b7c2429f6a49f53637a9fa556238c2ea8961b30f09c505e8b`
- Fixed Agent: `sha256:11aa663adf2de35416776cd3de77c941cf3ec813411293c2d022793c60678a52`
- Fixed comparison: `sha256:e76459c37eb909b8aee7a29e2aab4d31ccb10989bdbdb0e9ee82b21199bde51e`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `expiry_days, material, obsolete_days, plant, slow_moving_days, storage_location` (values remain in ignored artifacts).
- Business-condition fields: `expiry_days, material, obsolete_days, plant, slow_moving_days, snapshot_date, storage_location` (values remain in ignored artifacts).
- Accepted business grain: `material, plant, storage_location`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| inventory_stock_initial | API_MATERIAL_STOCK_SRV | 2.0 | A_MatlStkInAcctMod | 1 | 1 | Material, Plant, StorageLocation, Batch, Supplier, Customer, WBSElementInternalID, SDDocument, SDDocumentItem, InventorySpecialStockType, InventoryStockType | true | true |
| inventory_movement_items | API_MATERIAL_DOCUMENT_SRV | 2.0 | A_MaterialDocumentItem | 12 | 1 | MaterialDocumentYear, MaterialDocument, MaterialDocumentItem | true | true |
| inventory_movement_headers_001 | API_MATERIAL_DOCUMENT_SRV | 2.0 | A_MaterialDocumentHeader | 12 | 1 | MaterialDocumentYear, MaterialDocument | true | true |
| inventory_stock_confirmation | API_MATERIAL_STOCK_SRV | 2.0 | A_MatlStkInAcctMod | 1 | 1 | Material, Plant, StorageLocation, Batch, Supplier, Customer, WBSElementInternalID, SDDocument, SDDocumentItem, InventorySpecialStockType, InventoryStockType | true | true |

Schema/query manifests:
- `inventory_stock_initial` schema `sha256:5cfd9f82fd7abad22dfb6fb4354c9b26caaeb263fc36f8098befb1a2e19676f9`; query `sha256:0253a3b700db809a0adc3c8654cc600e45fed339516a4bd4521ef10790b31533`.
- `inventory_movement_items` schema `sha256:4de095109c47983878622572c2d95c6383e7c2fbf520cb15241582078d28852e`; query `sha256:f7fa6a1c6c1ba7a03359925a1e5dadcf981619de79ff63daedd113c8a06370d2`.
- `inventory_movement_headers_001` schema `sha256:4de095109c47983878622572c2d95c6383e7c2fbf520cb15241582078d28852e`; query `sha256:7eecf6927b78bf5c06e57ffc9e8d9db04ddb5f416959070d9d6ec72d2d67b5f9`.
- `inventory_stock_confirmation` schema `sha256:5cfd9f82fd7abad22dfb6fb4354c9b26caaeb263fc36f8098befb1a2e19676f9`; query `sha256:0253a3b700db809a0adc3c8654cc600e45fed339516a4bd4521ef10790b31533`.

- Test-data qualification: `qualified`.
- Qualification evidence: `inventory_stock_initial, inventory_movement_items, inventory_movement_headers_001, inventory_stock_confirmation`; reasons: `none`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
