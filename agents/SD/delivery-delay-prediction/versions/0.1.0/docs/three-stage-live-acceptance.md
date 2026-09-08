# Three-stage live SAP acceptance: delivery-delay-prediction

## Verdict

`PASS` / `executable=true`

- Case: `delivery-delay-prediction-live-001`
- Tested at: `2026-08-20T05:19:37.355666+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `1`
- Required limitations preserved: `schedule_line_delivery_evidence_discrepancy`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:1a6414311c72503715639b49dd90cd601b08ad65733bc91bb836f6c55870b1f1`
- SAPBusinessAgents free query: `sha256:52d8306f7eb100562972f6622f5e8c36068142983dc920497cefc9eb07da1a9e`
- Adjudicated result: `sha256:1a6414311c72503715639b49dd90cd601b08ad65733bc91bb836f6c55870b1f1`
- Fixed Agent: `sha256:5ed2cc8ee2e74793d0af814fb953bcb7119066a50ff1e1fdade44446cc67c654`
- Fixed comparison: `sha256:b1200c4bb0a453416d8d69d2e62d6ceaed45218628bf0b88ff21cf0325c0e3f0`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `date_from, date_to, sales_organization` (values remain in ignored artifacts).
- Business-condition fields: `date_from, date_to, sales_organization` (values remain in ignored artifacts).
- Accepted business grain: `sales_order, sales_order_item, schedule_line`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| range_order_headers | API_SALES_ORDER_SRV | 2.0 | A_SalesOrder | 1 | 1 | SalesOrder | true | true |
| range_order_items | API_SALES_ORDER_SRV | 2.0 | A_SalesOrderItem | 1 | 1 | SalesOrder, SalesOrderItem | true | true |
| range_schedule_lines | API_SALES_ORDER_SRV | 2.0 | A_SalesOrderScheduleLine | 2 | 1 | SalesOrder, SalesOrderItem, ScheduleLine | true | true |
| range_order_deliveries | API_OUTBOUND_DELIVERY_SRV | 2.0 | A_OutbDeliveryItem | 1 | 1 | DeliveryDocument, DeliveryDocumentItem | true | true |
| order_delivery_headers | API_OUTBOUND_DELIVERY_SRV | 2.0 | A_OutbDeliveryHeader | 1 | 1 | DeliveryDocument | true | true |

Schema/query manifests:
- `range_order_headers` schema `sha256:4fe5d6b08a6e1e4496ad0596cec20d554ee0cd51abe39b448aad750143549328`; query `sha256:69c6d267632365f3d78cd90909d8b8fbc7593dc87cc67091a110d6b120c13bcc`.
- `range_order_items` schema `sha256:4fe5d6b08a6e1e4496ad0596cec20d554ee0cd51abe39b448aad750143549328`; query `sha256:4d5477902dfbc1d2e41f0514705b60d89d87f8ecb6c47753277a8778c1fcdacb`.
- `range_schedule_lines` schema `sha256:4fe5d6b08a6e1e4496ad0596cec20d554ee0cd51abe39b448aad750143549328`; query `sha256:1a36cc0c82ede3af419fad9c2fdd247a74add8bc76678475938e438d3bb8156f`.
- `range_order_deliveries` schema `sha256:77414c3d7fd8ad508bcc94f160799b17e7bc3382165e377ac7e077ff914628c5`; query `sha256:f468c854804a2bc5a45bf93d2a9d8cd78c28b433bd203f116cc64ee0a5aa668d`.
- `order_delivery_headers` schema `sha256:77414c3d7fd8ad508bcc94f160799b17e7bc3382165e377ac7e077ff914628c5`; query `sha256:d94b4f1e7f1947c0eadc2c4d0f9f4a191979274c2a8288ad3b0a3a9b89a55615`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
