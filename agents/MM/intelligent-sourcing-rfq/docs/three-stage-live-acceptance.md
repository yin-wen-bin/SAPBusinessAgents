# Three-stage live SAP acceptance: intelligent-sourcing-rfq

## Verdict

`PASS` / `executable=true`

- Case: `intelligent-sourcing-rfq-live-001`
- Tested at: `2026-08-20T08:14:23.179244+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `2`
- Required limitations preserved: `none`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:bf14307923684d518a92bccd71fee0d2320c0357c843376b2d5c935b63578846`
- SAPBusinessAgents free query: `sha256:ae9d5edbf9c202e2fc8ae70df9fbd22af142beeb9817ffe563b043067e46fbde`
- Adjudicated result: `sha256:bf14307923684d518a92bccd71fee0d2320c0357c843376b2d5c935b63578846`
- Fixed Agent: `sha256:68c9e24a426672501b37fd2ddd3e8ca627ee65c56872d2e74bb2b1cc6effc0bb`
- Fixed comparison: `sha256:d918c8995a917d8065b09b3c0f8e884b1da8a90e13352a2078039296b1406ff0`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `as_of, purchasing_organization, rfq` (values remain in ignored artifacts).
- Business-condition fields: `as_of, purchasing_organization, rfq` (values remain in ignored artifacts).
- Accepted business grain: `rfq, rfq_item, supplier`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| rfq_header | API_RFQ_PROCESS_SRV | 2.0 | A_RequestForQuotation | 1 | 1 | RequestForQuotation | true | true |
| rfq_quotation_items_v2 | API_QTN_PROCESS_SRV | 2.0 | A_SupplierQuotationItem | 2 | 1 | SupplierQuotation, SupplierQuotationItem | true | true |
| rfq_quotation_headers | API_QTN_PROCESS_SRV | 2.0 | A_SupplierQuotation | 2 | 1 | SupplierQuotation | true | true |
| rfq_supplier_status | API_BUSINESS_PARTNER | 2.0 | A_SupplierPurchasingOrg | 0 | 1 | Supplier, PurchasingOrganization | true | true |
| rfq_sources | API_INFORECORD_PROCESS_SRV | 2.0 | A_PurgInfoRecdOrgPlantData | 6 | 1 | PurchasingInfoRecord, PurchasingInfoRecordCategory, PurchasingOrganization, Plant | true | true |

Schema/query manifests:
- `rfq_header` schema `sha256:c903aece9bbd2f7618f83a85580d044a18c4f67e6918f6479e7f1d7fbb91e99d`; query `sha256:d86fddd15cb7fde6633b218484b89a995cd8d75880eb7ab1f41f33b1646abb01`.
- `rfq_quotation_items_v2` schema `sha256:67a30818f07cd57a46e9f2b45fcf08a2049466d8960214a2bc9a5aed8d2219a0`; query `sha256:14a6fbff77966523bcd00756f0f8224dfef7a3be2cea8005be796b0e2c4b9271`.
- `rfq_quotation_headers` schema `sha256:67a30818f07cd57a46e9f2b45fcf08a2049466d8960214a2bc9a5aed8d2219a0`; query `sha256:1dacf2a71bd938ecefa070b0fd44fa4ed680d58761a1541661d5daf1fff00ff8`.
- `rfq_supplier_status` schema `sha256:e00911f83b2b24ed8b6dd36e7c4465c619ae30514bb02b04212c9d36e1c7b2b3`; query `sha256:b1d4c6793fa92a1798a50c89546c16a85db04d38eb5262d8d13e9a54b67edbc3`.
- `rfq_sources` schema `sha256:4f9de6ec7817a29976a4f17ed17c89d5ea91ed6fbfdd0726b6a764844948eab0`; query `sha256:a6f1b58f4f7f1f05cd79db17dd1883169b7e8760310effaa2c00cea5f19db14e`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
