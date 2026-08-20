# Three-stage live SAP acceptance: supplier-performance-risk

## Verdict

`PASS` / `executable=true`

- Case: `supplier-performance-risk-live-001`
- Tested at: `2026-08-20T08:36:27.815819+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `9`
- Required limitations preserved: `none`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:93883ca9345e60f57f901e8c5ecd8e0910203f71830028e1c3db5fb43fb7854c`
- SAPBusinessAgents free query: `sha256:628f79c06e9c401a1200590c81f96d8a04fea45ac5c09334944c0ece777e1087`
- Adjudicated result: `sha256:93883ca9345e60f57f901e8c5ecd8e0910203f71830028e1c3db5fb43fb7854c`
- Fixed Agent: `sha256:93883ca9345e60f57f901e8c5ecd8e0910203f71830028e1c3db5fb43fb7854c`
- Fixed comparison: `sha256:0eed5ec024d41fa394283203a4c27b4f7cb774a5c7adabb7f539546394d243c0`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `date_from, date_to, purchasing_organization, supplier` (values remain in ignored artifacts).
- Business-condition fields: `date_from, date_to, purchasing_organization, supplier` (values remain in ignored artifacts).
- Accepted business grain: `purchase_order, purchase_order_item, schedule_line`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| supplier_schedules | API_PURCHASEORDER_PROCESS_SRV | 2.0 | A_PurchaseOrderScheduleLine | 12 | 1 | PurchasingDocument, PurchasingDocumentItem, ScheduleLine | true | true |
| supplier_receipts | API_MATERIAL_DOCUMENT_SRV | 2.0 | A_MaterialDocumentItem | 9 | 1 | MaterialDocumentYear, MaterialDocument, MaterialDocumentItem | true | true |
| supplier_receipt_headers | API_MATERIAL_DOCUMENT_SRV | 2.0 | A_MaterialDocumentHeader | 9 | 1 | MaterialDocumentYear, MaterialDocument | true | true |
| supplier_status | API_BUSINESS_PARTNER | 2.0 | A_SupplierPurchasingOrg | 1 | 1 | Supplier, PurchasingOrganization | true | true |
| supplier_po_header | API_PURCHASEORDER_PROCESS_SRV | 2.0 | A_PurchaseOrder | 13 | 1 | PurchaseOrder | true | true |

Schema/query manifests:
- `supplier_schedules` schema `sha256:4b533b032e4c22cd43e2ec1ed8c3e41d57621e6f37ffa4894468e8f32bc91f32`; query `sha256:0ddc99dee3645069c9894ba5c734a8ffde7cf3d8849807c92099bb6d048a6b86`.
- `supplier_receipts` schema `sha256:4de095109c47983878622572c2d95c6383e7c2fbf520cb15241582078d28852e`; query `sha256:54276744a697807b47e75aeb0dde55c43af356cad406bb2cd52b80375d1e4fd7`.
- `supplier_receipt_headers` schema `sha256:4de095109c47983878622572c2d95c6383e7c2fbf520cb15241582078d28852e`; query `sha256:3de5178c823cf25ace940477d91b00a2f86f9bba0bcf9b1cf084634a5cd4ea39`.
- `supplier_status` schema `sha256:e00911f83b2b24ed8b6dd36e7c4465c619ae30514bb02b04212c9d36e1c7b2b3`; query `sha256:cdd6b6bfdeb139832a03fc043f664e3ceea45cd855093d36d515eb16c02f6c96`.
- `supplier_po_header` schema `sha256:4b533b032e4c22cd43e2ec1ed8c3e41d57621e6f37ffa4894468e8f32bc91f32`; query `sha256:432961bfcb8930a7f67842553d34e400fa72ea6cfdfbc4ae25cd4fbeffbf0a50`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
