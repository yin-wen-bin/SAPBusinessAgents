# Three-stage live SAP acceptance: inventory-health-balancing

## Verdict

`BLOCKED` / `executable=false`

- Case: `inventory-health-balancing-live-001`
- Tested at: `2026-08-20T08:26:17.006283+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `historical_stock_balance_evidence`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:b3db07473a3fe464def821cc7a8331f44bdf771fe05e973b8e4d8dec0a569dc0`
- SAPBusinessAgents free query: `sha256:7b6d461b4696897d6eb8ac5a481cc346ea64e48df00ab5e7f8881cf056520bba`
- Adjudicated result: `sha256:b3db07473a3fe464def821cc7a8331f44bdf771fe05e973b8e4d8dec0a569dc0`
- Fixed Agent: `sha256:b3db07473a3fe464def821cc7a8331f44bdf771fe05e973b8e4d8dec0a569dc0`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `as_of, date_from, expiry_days, material, obsolete_days, plant, slow_moving_days, storage_location` (values remain in ignored artifacts).
- Business-condition fields: `as_of, date_from, expiry_days, material, obsolete_days, plant, slow_moving_days, storage_location` (values remain in ignored artifacts).
- Accepted business grain: `material, plant, storage_location, batch`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| inventory_stock | API_MATERIAL_STOCK_SRV | 2.0 | A_MatlStkInAcctMod | 1 | 1 | Material, Plant, StorageLocation, Batch, Supplier, Customer, WBSElementInternalID, SDDocument, SDDocumentItem, InventorySpecialStockType, InventoryStockType | true | true |
| inventory_movements | API_MATERIAL_DOCUMENT_SRV | 2.0 | A_MaterialDocumentItem | 20 | 1 | MaterialDocumentYear, MaterialDocument, MaterialDocumentItem | true | true |
| inventory_movement_headers | API_MATERIAL_DOCUMENT_SRV | 2.0 | A_MaterialDocumentHeader | 20 | 1 | MaterialDocumentYear, MaterialDocument | true | true |
| inventory_batch_plants | API_BATCH_SRV | 2.0 | BatchPlant | 0 | 1 | Material, Batch, Plant | true | true |
| inventory_parameters | API_MRP_MATERIALS_SRV_01 | 2.0 | A_MRPMaterial | 1 | 1 | Material, MRPPlant, MRPArea | true | true |

Schema/query manifests:
- `inventory_stock` schema `sha256:5cfd9f82fd7abad22dfb6fb4354c9b26caaeb263fc36f8098befb1a2e19676f9`; query `sha256:a0fbc1207440ea3d15155f244add00aefdc81796071af8b758a416aeaa1b73ec`.
- `inventory_movements` schema `sha256:4de095109c47983878622572c2d95c6383e7c2fbf520cb15241582078d28852e`; query `sha256:6f97ed6f9d03a7c291a7787312959913d4f7b12505b4686e0d115ae0a32feb76`.
- `inventory_movement_headers` schema `sha256:4de095109c47983878622572c2d95c6383e7c2fbf520cb15241582078d28852e`; query `sha256:a9e1388c60c5fa82eb77f4fc1475c001a5f89125fec178d0c50915d055fe4d67`.
- `inventory_batch_plants` schema `sha256:d5814a007e939666797721b01b874cb653910e855ea5c3ceeacc99eabd85338d`; query `sha256:5a0c1fcae9f6bdd0f21aa1a256e238821d9e2407ca74ab8ee7c1d27a5590c60a`.
- `inventory_parameters` schema `sha256:1adaff0c7faaab6671558af7516c825cc212e8c7dc17058bc2aa8ea2aa3921cd`; query `sha256:726c63e602914ea14dc58dd29cc9dd9da946314e34d1a7528b6e1f165fc4e6b6`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
