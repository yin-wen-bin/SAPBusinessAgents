# Three-stage live SAP acceptance: month-end-closing

## Verdict

`PASS` / `executable=true`

- Case: `month-end-closing-live-001`
- Tested at: `2026-08-20T05:18:59.094337+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `1`
- Required limitations preserved: `period_control_asset_depreciation_and_specialized_closing_checks`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:8fb9e50de541e7b067598e6c9a2b43b41254924fe756ec7ac7c922e6ff102189`
- SAPBusinessAgents free query: `sha256:044493fed970cb5bad536720d6eeddd73ac1a56a5e93c883b728e65950c67e88`
- Adjudicated result: `sha256:8fb9e50de541e7b067598e6c9a2b43b41254924fe756ec7ac7c922e6ff102189`
- Fixed Agent: `sha256:8fb9e50de541e7b067598e6c9a2b43b41254924fe756ec7ac7c922e6ff102189`
- Fixed comparison: `sha256:14b3db192dee2d5a952b91506caf91ec47f7923d22386b1996b9da6f642fdbd4`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `company_code, fiscal_year, period` (values remain in ignored artifacts).
- Business-condition fields: `company_code, fiscal_year, period` (values remain in ignored artifacts).
- Accepted business grain: `company_code, fiscal_year, period`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| fi_period_items | API_OPLACCTGDOCITEMCUBE_SRV | 2.0 | A_OperationalAcctgDocItemCube | 2037 | 1 | PostingDate, CompanyCode, FiscalYear, AccountingDocument, AccountingDocumentItem | true | true |

Schema/query manifests:
- `fi_period_items` schema `sha256:65f619d00b9b12395e17fdff88b15260b6256e80405991dd2279c133c1ec6b67`; query `sha256:8f78c0e04c0d5305f78f1d78eb2b4d77507173fd2f61096a023c23eb810cd64c`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
