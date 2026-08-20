# Three-stage live SAP acceptance: gr-ir-clearing

## Verdict

`PASS` / `executable=true`

- Case: `gr-ir-clearing-live-001`
- Tested at: `2026-08-20T05:18:57.447730+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `72`
- Required limitations preserved: `none`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:17d34938c2aa4efe05609860b3a9011527b134c52107acc53f684a05f12a08d2`
- SAPBusinessAgents free query: `sha256:69d970ce8e6e6259c7e609fbbb236aa914ee93445d0d17bf3c962ba46ab6ba27`
- Adjudicated result: `sha256:17d34938c2aa4efe05609860b3a9011527b134c52107acc53f684a05f12a08d2`
- Fixed Agent: `sha256:5babb44d972d7da82a65b9faf37c687f87a06957ae13cced90e96206ce374bb0`
- Fixed comparison: `sha256:c5ee72a9960b9fd888401bdd7ef3cedf848b1ee0a92b5680b074b2de364de9b4`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `company_code, date_from, date_to, gl_account` (values remain in ignored artifacts).
- Business-condition fields: `company_code, date_from, date_to, gl_account` (values remain in ignored artifacts).
- Accepted business grain: `purchase_order, purchase_order_item`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| gl_items | API_OPLACCTGDOCITEMCUBE_SRV | 2.0 | A_OperationalAcctgDocItemCube | 144 | 1 | CompanyCode, FiscalYear, AccountingDocument, AccountingDocumentItem | true | true |
| purchase_order_items | API_PURCHASEORDER_PROCESS_SRV | 2.0 | A_PurchaseOrderItem | 72 | 1 | PurchaseOrder, PurchaseOrderItem | true | true |
| material_documents | API_MATERIAL_DOCUMENT_SRV | 2.0 | A_MaterialDocumentItem | 72 | 1 | MaterialDocumentYear, MaterialDocument, MaterialDocumentItem | true | true |
| supplier_invoice_items | API_SUPPLIERINVOICE_PROCESS_SRV | 2.0 | A_SuplrInvcItemPurOrdRef | 72 | 1 | SupplierInvoice, FiscalYear, SupplierInvoiceItem | true | true |

Schema/query manifests:
- `gl_items` schema `sha256:65f619d00b9b12395e17fdff88b15260b6256e80405991dd2279c133c1ec6b67`; query `sha256:447b1f4f7b32df949b19c1bfdc35dc373071309a1947483b784a7b6ffaa214d7`.
- `purchase_order_items` schema `sha256:4b533b032e4c22cd43e2ec1ed8c3e41d57621e6f37ffa4894468e8f32bc91f32`; query `sha256:92ae41d6744a6987bfd0a489b73407695b2ff6390abaeab3984e7d012aee2a89`.
- `material_documents` schema `sha256:4de095109c47983878622572c2d95c6383e7c2fbf520cb15241582078d28852e`; query `sha256:88ed2bf658dd98b11f766e1c903db9148089ce386283916b0cecd6d36ff53644`.
- `supplier_invoice_items` schema `sha256:e69492f061cc8f7d3fe1b63ac581ab129c30216531e2f0680ae92ab077335d49`; query `sha256:0e3784f827e806f1ecb43cd70a060e123cbda83ffd2f6756ad8c342279fb24ac`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
