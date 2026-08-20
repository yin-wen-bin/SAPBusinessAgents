# Three-stage live SAP acceptance: delivered-not-billed

## Verdict

`PASS` / `executable=true`

- Case: `delivered-not-billed-live-001`
- Tested at: `2026-08-20T05:19:35.535162+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `18`
- Required limitations preserved: `none`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:f0914a309e6b70d11547f25d226fa13ff56dc75d11eedded39418d0599d6c242`
- SAPBusinessAgents free query: `sha256:5b632c66285997b7df20321554eff1c96d4eb97c60f369608c3fec5e274dde01`
- Adjudicated result: `sha256:f0914a309e6b70d11547f25d226fa13ff56dc75d11eedded39418d0599d6c242`
- Fixed Agent: `sha256:f0914a309e6b70d11547f25d226fa13ff56dc75d11eedded39418d0599d6c242`
- Fixed comparison: `sha256:65cb364e4a87e035afd2f9bd84201f2f14f2841ebd8991341445eb68d179d0d6`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `date_from, date_to, sales_organization` (values remain in ignored artifacts).
- Business-condition fields: `date_from, date_to, sales_organization` (values remain in ignored artifacts).
- Accepted business grain: `delivery_document, delivery_document_item`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| range_delivery_headers | API_OUTBOUND_DELIVERY_SRV | 2.0 | A_OutbDeliveryHeader | 18 | 1 | DeliveryDocument | true | true |
| range_delivery_items | API_OUTBOUND_DELIVERY_SRV | 2.0 | A_OutbDeliveryItem | 18 | 1 | DeliveryDocument, DeliveryDocumentItem | true | true |
| range_billing_items | API_BILLING_DOCUMENT_SRV | 2.0 | A_BillingDocumentItem | 18 | 1 | BillingDocument, BillingDocumentItem | true | true |

Schema/query manifests:
- `range_delivery_headers` schema `sha256:77414c3d7fd8ad508bcc94f160799b17e7bc3382165e377ac7e077ff914628c5`; query `sha256:eb07e863ab4060f66e9c549be5bdf9e39576adb455f37991bfb198c87312025b`.
- `range_delivery_items` schema `sha256:77414c3d7fd8ad508bcc94f160799b17e7bc3382165e377ac7e077ff914628c5`; query `sha256:5137044ddfc91b7094f4d06e4564aa909e3a00d441dbd67b70fd47f4f6a5cfc9`.
- `range_billing_items` schema `sha256:a7bd8d8ea98baead41d14d74e7a061a9b5de9013f3b59ea96b47a1ad9899d7d2`; query `sha256:5c0a0e5a2894f03e627c32d78741a50bb0dbe2b35568cfddb725aae0702cce2e`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
