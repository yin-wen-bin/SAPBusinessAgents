# Three-stage live SAP acceptance: billing-block-diagnosis

## Verdict

`PASS` / `executable=true`

- Case: `billing-block-diagnosis-live-001`
- Tested at: `2026-08-23T01:41:11.877166+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `1`
- Required limitations preserved: `none`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:54fec876c8844eb9cd4edf4e71ac7a5afdee0aa5dd5e055ef765d638aecadb13`
- SAPBusinessAgents free query: `sha256:d949ff765d9a1d9e119d9d4212af717c1ce708c31eb13fa9e8ec054bf15055db`
- Adjudicated result: `sha256:54fec876c8844eb9cd4edf4e71ac7a5afdee0aa5dd5e055ef765d638aecadb13`
- Fixed Agent: `sha256:54fec876c8844eb9cd4edf4e71ac7a5afdee0aa5dd5e055ef765d638aecadb13`
- Fixed comparison: `sha256:3c956466c5a78bb614bf289218daf36f165f68076b93fb0a17709570fd0e4019`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `sales_order` (values remain in ignored artifacts).
- Business-condition fields: `sales_order` (values remain in ignored artifacts).
- Accepted business grain: `sales_order, sales_order_item`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| order_header | API_SALES_ORDER_SRV | 2.0 | A_SalesOrder | 1 | 1 | SalesOrder | true | true |
| order_items | API_SALES_ORDER_SRV | 2.0 | A_SalesOrderItem | 1 | 1 | SalesOrder, SalesOrderItem | true | true |
| order_delivery_items | API_OUTBOUND_DELIVERY_SRV | 2.0 | A_OutbDeliveryItem | 1 | 1 | DeliveryDocument, DeliveryDocumentItem | true | true |
| order_delivery_headers | API_OUTBOUND_DELIVERY_SRV | 2.0 | A_OutbDeliveryHeader | 1 | 1 | DeliveryDocument | true | true |
| order_billing_items | API_BILLING_DOCUMENT_SRV | 2.0 | A_BillingDocumentItem | 1 | 1 | BillingDocument, BillingDocumentItem | true | true |
| order_billing_headers | API_BILLING_DOCUMENT_SRV | 2.0 | A_BillingDocument | 1 | 1 | BillingDocument | true | true |

Schema/query manifests:
- `order_header` schema `sha256:4fe5d6b08a6e1e4496ad0596cec20d554ee0cd51abe39b448aad750143549328`; query `sha256:5c41122ad576530bf943e8449073cd3d0262923e91ddbcfed2a042a71e5245d0`.
- `order_items` schema `sha256:4fe5d6b08a6e1e4496ad0596cec20d554ee0cd51abe39b448aad750143549328`; query `sha256:0d4ac07a1d60129345cc21e2f58d8db782bc3476a9a8c367c48142735a35521c`.
- `order_delivery_items` schema `sha256:77414c3d7fd8ad508bcc94f160799b17e7bc3382165e377ac7e077ff914628c5`; query `sha256:ad5f9da727e8e215efe7f7f274f87ef1640fae2a5409b9716589e2bfab6f37c5`.
- `order_delivery_headers` schema `sha256:77414c3d7fd8ad508bcc94f160799b17e7bc3382165e377ac7e077ff914628c5`; query `sha256:d94b4f1e7f1947c0eadc2c4d0f9f4a191979274c2a8288ad3b0a3a9b89a55615`.
- `order_billing_items` schema `sha256:a7bd8d8ea98baead41d14d74e7a061a9b5de9013f3b59ea96b47a1ad9899d7d2`; query `sha256:1fbedc1fb3bf53cc4ea7fb99885d66a2394bbf3f05cf1e6f5908fc62da2eaa6d`.
- `order_billing_headers` schema `sha256:a7bd8d8ea98baead41d14d74e7a061a9b5de9013f3b59ea96b47a1ad9899d7d2`; query `sha256:64d66669a4f9a75f670eaf611dc979f638d53bb756b2c6f27096ca50e9dfd961`.

## Supplemental read-only evidence

| Source | Provider | Object | Fields | Rows | Paging complete | Source complete | Hash verified |
|---|---|---|---|---:|:---:|:---:|:---:|
| item_incompletion_log | sap-adt-table-export | VBUV | VBELN, POSNR, ETENR, TBNAM, FDNAM, FEHGR, STATG | 0 | true | true | true |

Supplemental evidence hashes:
- `item_incompletion_log` filter `sha256:1c9cabfdb4a782e0a2d5e82f2723a706322b55f0a5adc524ab4fe6192d455453`; manifest `sha256:a657a84c7914aa58cf1019ccbb35d202d5cd7c182b7f8f2f0e83397652854c4a`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.

## 2026-08-23 code-text consistency replay

- Embedded stage: complete GET-only order, item, delivery and billing branches; header block and credit codes remained separate from item rows.
- ADT stage: complete and hash-verified reads for `VBUV`, `TVFST`, `TVLST`, `DD07T`, `DD03T`, `DD03L`, and `DD04T`; `DD03T` complete-zero was resolved through the authoritative DDIC data-element fallback.
- Fixed-Agent stage: run `acceptance_7b93a3c35b124e9d` completed with `source_complete=true`, `business_complete=true`, four visible findings, and no missing evidence; focused result hash `sha256:d6b1600e0b59759b38402077d467c25555dcbd7ca1028b0f60635751270f709f`.
- Semantic outcome: the raw values `00`, `07`, `B`, and `VBAP.VSTEL` were preserved and paired with live SAP texts; header values were propagated to the item record with `scope=header`.
- Safety: Embedded business requests were GET-only, ADT remained read-only, and no SAP write operation was executed.
