# Three-stage live SAP acceptance: procure-to-pay-status

## Verdict

`PASS` / `executable=true`

- Case: `procure-to-pay-status-live-001`
- Tested at: `2026-08-20T08:35:46.484710+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `1`
- Required limitations preserved: `p2p_supplier_invoice_evidence, bank_settlement_not_proven`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:05285c457573ca44a998fbfcc201c3e1b5ff7bf67db35636e33462617a6a6f6a`
- SAPBusinessAgents free query: `sha256:ee0184b898bb692672aca8d093fe4cb230fbe4f9b047b8ba9caee38b1b66be3c`
- Adjudicated result: `sha256:05285c457573ca44a998fbfcc201c3e1b5ff7bf67db35636e33462617a6a6f6a`
- Fixed Agent: `sha256:05285c457573ca44a998fbfcc201c3e1b5ff7bf67db35636e33462617a6a6f6a`
- Fixed comparison: `sha256:e6961bdc769cf4bfcf56b60b67be82dd692a96913286fdbb9aea85104debe6c7`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `purchase_order` (values remain in ignored artifacts).
- Business-condition fields: `purchase_order` (values remain in ignored artifacts).
- Accepted business grain: `purchase_order, purchase_order_item`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| p2p_po_header | API_PURCHASEORDER_PROCESS_SRV | 2.0 | A_PurchaseOrder | 1 | 1 | PurchaseOrder | true | true |
| p2p_po_items | API_PURCHASEORDER_PROCESS_SRV | 2.0 | A_PurchaseOrderItem | 1 | 1 | PurchaseOrder, PurchaseOrderItem | true | true |
| p2p_receipts | API_MATERIAL_DOCUMENT_SRV | 2.0 | A_MaterialDocumentItem | 1 | 1 | MaterialDocumentYear, MaterialDocument, MaterialDocumentItem | true | true |
| p2p_invoices | API_SUPPLIERINVOICE_PROCESS_SRV | 2.0 | A_SuplrInvcItemPurOrdRef | 0 | 1 | SupplierInvoice, FiscalYear, SupplierInvoiceItem | true | true |
| p2p_accounting | API_OPLACCTGDOCITEMCUBE_SRV | 2.0 | A_OperationalAcctgDocItemCube | 2 | 1 | CompanyCode, FiscalYear, AccountingDocument, AccountingDocumentItem | true | true |

Schema/query manifests:
- `p2p_po_header` schema `sha256:4b533b032e4c22cd43e2ec1ed8c3e41d57621e6f37ffa4894468e8f32bc91f32`; query `sha256:b15f8c6692acb639ebf561d9982b79d63108a94c7c99ab5cf5a8c08f3e167c83`.
- `p2p_po_items` schema `sha256:4b533b032e4c22cd43e2ec1ed8c3e41d57621e6f37ffa4894468e8f32bc91f32`; query `sha256:785a97b8ba40be6d93c15e87217614c72ed9729b48b25dbde2ab8bbe343a6608`.
- `p2p_receipts` schema `sha256:4de095109c47983878622572c2d95c6383e7c2fbf520cb15241582078d28852e`; query `sha256:dfcc78fc65fdeb73e30bd64d9f0cfb030e7b72014ef1458e95a4a69cc02c846a`.
- `p2p_invoices` schema `sha256:e69492f061cc8f7d3fe1b63ac581ab129c30216531e2f0680ae92ab077335d49`; query `sha256:6f4622348b0c8e4eea56439f7ad52833b7807257f6ddeec1e605290b7f2a5801`.
- `p2p_accounting` schema `sha256:65f619d00b9b12395e17fdff88b15260b6256e80405991dd2279c133c1ec6b67`; query `sha256:3db41939128ab9f16efc361ba0ba9886bc2df9f1747340126ad4b84052e07f2f`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
