# Three-stage live SAP acceptance: ar-collection

## Verdict

`PASS` / `executable=true`

- Case: `ar-collection-live-001`
- Tested at: `2026-08-20T05:18:55.847532+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `37`
- Required limitations preserved: `historical_dunning_evidence`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:b3206f0bbafb8431b0af10a3ed7c15d583e781657172d5821332a181e6efc458`
- SAPBusinessAgents free query: `sha256:dd2690c1171a66ca223e0b23cc8ebe03eccbdd0fe01b74e13120fd92f614ea40`
- Adjudicated result: `sha256:b3206f0bbafb8431b0af10a3ed7c15d583e781657172d5821332a181e6efc458`
- Fixed Agent: `sha256:380611b1fdfd624f7b9d7c2d3c7475bbaad571d0ee2de5d80ecb88c959822433`
- Fixed comparison: `sha256:89ec4505dae5c4d8f46c89861f3e9d37ad1c4fec44ada38a5446eb316588dd45`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `as_of, company_code, customer` (values remain in ignored artifacts).
- Business-condition fields: `as_of, company_code, customer, financial_account_type, historical_open_rule` (values remain in ignored artifacts).
- Accepted business grain: `company_code, fiscal_year, accounting_document, accounting_document_item`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| customer_items | API_OPLACCTGDOCITEMCUBE_SRV | 2.0 | A_OperationalAcctgDocItemCube | 51 | 1 | CompanyCode, FiscalYear, AccountingDocument, AccountingDocumentItem | true | true |
| customer_dunning | API_BUSINESS_PARTNER | 2.0 | A_CustomerDunning | 1 | 1 | Customer, CompanyCode, DunningArea | true | true |

Schema/query manifests:
- `customer_items` schema `sha256:65f619d00b9b12395e17fdff88b15260b6256e80405991dd2279c133c1ec6b67`; query `sha256:283dafb1930d9ee1835a19d5e5f1eeb53bd84bb1468ed0430de94fa225d45fed`.
- `customer_dunning` schema `sha256:e00911f83b2b24ed8b6dd36e7c4465c619ae30514bb02b04212c9d36e1c7b2b3`; query `sha256:f24403b80deb87088d5355b9d8e17b8a7f2d285f55c1f1f23b60ed7b8ffbe323`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
