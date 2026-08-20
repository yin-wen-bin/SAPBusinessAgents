# Three-stage live SAP acceptance: billing-output-monitor

## Verdict

`BLOCKED` / `executable=false`

- Case: `billing-output-monitor-live-001`
- Tested at: `2026-08-20T05:19:33.967556+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `billing_output_status_evidence`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:a7a92f226755ff08f2b84d4efb4ff0dce5eef4d5f143df8f3ddada7cba53971f`
- SAPBusinessAgents free query: `sha256:a7a92f226755ff08f2b84d4efb4ff0dce5eef4d5f143df8f3ddada7cba53971f`
- Adjudicated result: `sha256:a7a92f226755ff08f2b84d4efb4ff0dce5eef4d5f143df8f3ddada7cba53971f`
- Fixed Agent: `sha256:a7a92f226755ff08f2b84d4efb4ff0dce5eef4d5f143df8f3ddada7cba53971f`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `billing_document` (values remain in ignored artifacts).
- Business-condition fields: `billing_document` (values remain in ignored artifacts).
- Accepted business grain: `billing_document, output_request`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| billing_header | API_BILLING_DOCUMENT_SRV | 2.0 | A_BillingDocument | 1 | 1 | BillingDocument | true | true |
| billing_items | API_BILLING_DOCUMENT_SRV | 2.0 | A_BillingDocumentItem | 1 | 1 | BillingDocument, BillingDocumentItem | true | true |

Schema/query manifests:
- `billing_header` schema `sha256:a7bd8d8ea98baead41d14d74e7a061a9b5de9013f3b59ea96b47a1ad9899d7d2`; query `sha256:c6f8c3bbb4b305c0bd20d446270f0de9ccb2c231a36278749042b2b86b5ae95c`.
- `billing_items` schema `sha256:a7bd8d8ea98baead41d14d74e7a061a9b5de9013f3b59ea96b47a1ad9899d7d2`; query `sha256:4d3eb11af20f4828efa0f6489aee44e249d4156216d3d65f0afaf709a5a47b81`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
