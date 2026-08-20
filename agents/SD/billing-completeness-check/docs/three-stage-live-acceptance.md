# Three-stage live SAP acceptance: billing-completeness-check

## Verdict

`PASS` / `executable=true`

- Case: `billing-completeness-check-live-001`
- Tested at: `2026-08-20T05:19:30.761233+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `1`
- Required limitations preserved: `none`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:00b7871fee8308f1cf6b40b4878de129add79dbda579b4e9476df1e1d2359943`
- SAPBusinessAgents free query: `sha256:b2d84cc5215af05e70c8341e9588df3ee42318cd1d657e5142b290c82ca06ed7`
- Adjudicated result: `sha256:00b7871fee8308f1cf6b40b4878de129add79dbda579b4e9476df1e1d2359943`
- Fixed Agent: `sha256:00b7871fee8308f1cf6b40b4878de129add79dbda579b4e9476df1e1d2359943`
- Fixed comparison: `sha256:e125a5196270ebc3c679e237b8b2fee16258946c08f0f81f9abb06535a866af6`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `billing_document` (values remain in ignored artifacts).
- Business-condition fields: `billing_document` (values remain in ignored artifacts).
- Accepted business grain: `billing_document, billing_document_item`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| billing_header | API_BILLING_DOCUMENT_SRV | 2.0 | A_BillingDocument | 1 | 1 | BillingDocument | true | true |
| billing_items | API_BILLING_DOCUMENT_SRV | 2.0 | A_BillingDocumentItem | 1 | 1 | BillingDocument, BillingDocumentItem | true | true |
| billing_source_sales_items | API_SALES_ORDER_SRV | 2.0 | A_SalesOrderItem | 1 | 1 | SalesOrder, SalesOrderItem | true | true |
| billing_source_delivery_items | API_OUTBOUND_DELIVERY_SRV | 2.0 | A_OutbDeliveryItem | 1 | 1 | DeliveryDocument, DeliveryDocumentItem | true | true |

Schema/query manifests:
- `billing_header` schema `sha256:a7bd8d8ea98baead41d14d74e7a061a9b5de9013f3b59ea96b47a1ad9899d7d2`; query `sha256:c6f8c3bbb4b305c0bd20d446270f0de9ccb2c231a36278749042b2b86b5ae95c`.
- `billing_items` schema `sha256:a7bd8d8ea98baead41d14d74e7a061a9b5de9013f3b59ea96b47a1ad9899d7d2`; query `sha256:4d3eb11af20f4828efa0f6489aee44e249d4156216d3d65f0afaf709a5a47b81`.
- `billing_source_sales_items` schema `sha256:4fe5d6b08a6e1e4496ad0596cec20d554ee0cd51abe39b448aad750143549328`; query `sha256:478af6b43698addb8e3ad30d35dafe2159aa72d693df899de8ca351ba91dc173`.
- `billing_source_delivery_items` schema `sha256:77414c3d7fd8ad508bcc94f160799b17e7bc3382165e377ac7e077ff914628c5`; query `sha256:674225bdb589995b625d440446879e5fc504e9de828382486759ab58bd2838f9`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
