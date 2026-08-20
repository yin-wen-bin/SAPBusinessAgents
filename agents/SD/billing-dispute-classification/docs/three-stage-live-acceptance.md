# Three-stage live SAP acceptance: billing-dispute-classification

## Verdict

`BLOCKED` / `executable=false`

- Case: `billing-dispute-classification-live-001`
- Tested at: `2026-08-20T05:19:32.404578+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `billing_dispute_case_evidence`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:a211de071e62a23d22c3947666a091aa5df3c1d1719b610d1ec9197172a6374a`
- SAPBusinessAgents free query: `sha256:47fe70278151cdc93b04c88fd791526c9a8507dc2da4c9505bc8a89610c1abca`
- Adjudicated result: `sha256:a211de071e62a23d22c3947666a091aa5df3c1d1719b610d1ec9197172a6374a`
- Fixed Agent: `sha256:a211de071e62a23d22c3947666a091aa5df3c1d1719b610d1ec9197172a6374a`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

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
| billing_accounting_items | API_OPLACCTGDOCITEMCUBE_SRV | 2.0 | A_OperationalAcctgDocItemCube | 1 | 1 | CompanyCode, FiscalYear, AccountingDocument, AccountingDocumentItem | true | true |

Schema/query manifests:
- `billing_header` schema `sha256:a7bd8d8ea98baead41d14d74e7a061a9b5de9013f3b59ea96b47a1ad9899d7d2`; query `sha256:c6f8c3bbb4b305c0bd20d446270f0de9ccb2c231a36278749042b2b86b5ae95c`.
- `billing_items` schema `sha256:a7bd8d8ea98baead41d14d74e7a061a9b5de9013f3b59ea96b47a1ad9899d7d2`; query `sha256:4d3eb11af20f4828efa0f6489aee44e249d4156216d3d65f0afaf709a5a47b81`.
- `billing_accounting_items` schema `sha256:65f619d00b9b12395e17fdff88b15260b6256e80405991dd2279c133c1ec6b67`; query `sha256:5e44de6e2ddcad11cfd1b13e9be26d8a97b0559be2f6b1388d7e4d101067d5cc`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
