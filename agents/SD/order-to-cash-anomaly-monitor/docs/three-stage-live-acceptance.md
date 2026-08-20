# Three-stage live SAP acceptance: order-to-cash-anomaly-monitor

## Verdict

`BLOCKED` / `executable=false`

- Case: `order-to-cash-anomaly-monitor-live-001`
- Tested at: `2026-08-20T07:05:40.026769+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `billing_dispute_case_evidence, billing_output_status_evidence`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:b6783d5bf1e08ad2142c6356c18b4ad30cad66b1be476e0bb0a64e4bd5f1ec75`
- SAPBusinessAgents free query: `sha256:d3c8da42505ebcccd3f9e0c0d28e3c505843af8db5cda3a1a7ebb349c5c1ec76`
- Adjudicated result: `sha256:b6783d5bf1e08ad2142c6356c18b4ad30cad66b1be476e0bb0a64e4bd5f1ec75`
- Fixed Agent: `sha256:d506c6e59aa878da409009a751b54821bf27992f1cec19393a210ac9930d4172`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `date_from, date_to, sales_organization` (values remain in ignored artifacts).
- Business-condition fields: `date_from, date_to, sales_organization` (values remain in ignored artifacts).
- Accepted business grain: `sales_order, sales_order_item`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| range_order_headers | API_SALES_ORDER_SRV | 2.0 | A_SalesOrder | 1 | 1 | SalesOrder | true | true |
| range_order_items | API_SALES_ORDER_SRV | 2.0 | A_SalesOrderItem | 1 | 1 | SalesOrder, SalesOrderItem | true | true |
| range_order_deliveries | API_OUTBOUND_DELIVERY_SRV | 2.0 | A_OutbDeliveryItem | 1 | 1 | DeliveryDocument, DeliveryDocumentItem | true | true |
| order_billing_headers | API_BILLING_DOCUMENT_SRV | 2.0 | A_BillingDocument | 1 | 1 | BillingDocument | true | true |
| order_billing_items | API_BILLING_DOCUMENT_SRV | 2.0 | A_BillingDocumentItem | 1 | 1 | BillingDocument, BillingDocumentItem | true | true |
| order_accounting_items | API_OPLACCTGDOCITEMCUBE_SRV | 2.0 | A_OperationalAcctgDocItemCube | 3 | 1 | CompanyCode, FiscalYear, AccountingDocument, AccountingDocumentItem | true | true |

Schema/query manifests:
- `range_order_headers` schema `sha256:4fe5d6b08a6e1e4496ad0596cec20d554ee0cd51abe39b448aad750143549328`; query `sha256:69c6d267632365f3d78cd90909d8b8fbc7593dc87cc67091a110d6b120c13bcc`.
- `range_order_items` schema `sha256:4fe5d6b08a6e1e4496ad0596cec20d554ee0cd51abe39b448aad750143549328`; query `sha256:4d5477902dfbc1d2e41f0514705b60d89d87f8ecb6c47753277a8778c1fcdacb`.
- `range_order_deliveries` schema `sha256:77414c3d7fd8ad508bcc94f160799b17e7bc3382165e377ac7e077ff914628c5`; query `sha256:f468c854804a2bc5a45bf93d2a9d8cd78c28b433bd203f116cc64ee0a5aa668d`.
- `order_billing_headers` schema `sha256:a7bd8d8ea98baead41d14d74e7a061a9b5de9013f3b59ea96b47a1ad9899d7d2`; query `sha256:64d66669a4f9a75f670eaf611dc979f638d53bb756b2c6f27096ca50e9dfd961`.
- `order_billing_items` schema `sha256:a7bd8d8ea98baead41d14d74e7a061a9b5de9013f3b59ea96b47a1ad9899d7d2`; query `sha256:1fbedc1fb3bf53cc4ea7fb99885d66a2394bbf3f05cf1e6f5908fc62da2eaa6d`.
- `order_accounting_items` schema `sha256:65f619d00b9b12395e17fdff88b15260b6256e80405991dd2279c133c1ec6b67`; query `sha256:0cde3c9c43799f3cbc4969453bb98bfb97e004cb35c8da3bf89a16977846d0c4`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
