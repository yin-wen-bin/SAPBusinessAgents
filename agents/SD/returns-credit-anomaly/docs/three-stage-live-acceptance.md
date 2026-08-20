# Three-stage live SAP acceptance: returns-credit-anomaly

## Verdict

`BLOCKED` / `executable=false`

- Case: `returns-credit-anomaly-live-001`
- Tested at: `2026-08-20T08:39:52.862210+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `return_receipt_evidence, return_refund_type_evidence`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:67266901a77c59887e38c8abcdaf957f5ecad29d4f77a63feefe47e3dcfd0a26`
- SAPBusinessAgents free query: `sha256:619a72fb6b4e8361938d361e37461e3302c7e43e540bfe1098c8a22952ee9058`
- Adjudicated result: `sha256:67266901a77c59887e38c8abcdaf957f5ecad29d4f77a63feefe47e3dcfd0a26`
- Fixed Agent: `sha256:67266901a77c59887e38c8abcdaf957f5ecad29d4f77a63feefe47e3dcfd0a26`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `date_from, date_to, sales_organization` (values remain in ignored artifacts).
- Business-condition fields: `date_from, date_to, sales_organization` (values remain in ignored artifacts).
- Accepted business grain: `customer_return, customer_return_item`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| return_headers | API_CUSTOMER_RETURN_SRV | 2.0 | A_CustomerReturn | 2 | 1 | CustomerReturn | true | true |
| return_items | API_CUSTOMER_RETURN_SRV | 2.0 | A_CustomerReturnItem | 4 | 1 | CustomerReturn, CustomerReturnItem | true | true |
| return_credit_headers | API_CREDIT_MEMO_REQUEST_SRV | 2.0 | A_CreditMemoRequest | 0 | 1 | CreditMemoRequest | true | true |
| return_credit_items | API_CREDIT_MEMO_REQUEST_SRV | 2.0 | A_CreditMemoRequestItem | 0 | 1 | CreditMemoRequest, CreditMemoRequestItem | true | true |
| returns_follow_on_billing | API_BILLING_DOCUMENT_SRV | 2.0 | A_BillingDocumentItem | 2 | 1 | BillingDocument, BillingDocumentItem | true | true |

Schema/query manifests:
- `return_headers` schema `sha256:c377c2c4910ed39478ecaa076c441bde93ea75d68dc7382b0dd58ee16704add8`; query `sha256:f70a6335dadfe81117673f2580a199166de6baeca4f6c95ca331f418a386dc2f`.
- `return_items` schema `sha256:c377c2c4910ed39478ecaa076c441bde93ea75d68dc7382b0dd58ee16704add8`; query `sha256:52bdc604c6214289b8d993752d6b5317183b8c6e6fd8f6fcd8d579ce5181dffd`.
- `return_credit_headers` schema `sha256:c6ea18e409cd2bb9abe06c22890acad794e48028e74c0075ccc608b715809026`; query `sha256:3701ffd4fc282219df002c5e29547fc91e1fb8a371e7c70008a31a73c2065be9`.
- `return_credit_items` schema `sha256:c6ea18e409cd2bb9abe06c22890acad794e48028e74c0075ccc608b715809026`; query `sha256:138479609859b315ac9474118ed176ca6e12ebbc2570a17b44cec20526e269bf`.
- `returns_follow_on_billing` schema `sha256:a7bd8d8ea98baead41d14d74e7a061a9b5de9013f3b59ea96b47a1ad9899d7d2`; query `sha256:ad01efd00f6f2eb4f553d8607715b1d0c5111a4c413b57922d0409d4ab13589a`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
