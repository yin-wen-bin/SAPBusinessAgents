# Three-stage live SAP acceptance: material-shortage-procurement-response

## Verdict

`BLOCKED` / `executable=false`

- Case: `material-shortage-procurement-response-live-001`
- Tested at: `2026-08-20T09:28:09.598221+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `qualified_shortage_test_data_missing`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:ecf0a6949d3836cdbacbbbc2cc6a01f07743987f6fa18290aea869f76a5d5d26`
- SAPBusinessAgents free query: `sha256:33eb1a446305c20cfc868103368ae148a0a7ebda29ad46d380363964e23e70f9`
- Adjudicated result: `sha256:ecf0a6949d3836cdbacbbbc2cc6a01f07743987f6fa18290aea869f76a5d5d26`
- Fixed Agent: `sha256:0ae023456ff12363975bbf553ef3ab8c5e425501f443019afb9cdf4476ef40da`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `as_of, material, mrp_area, plant, purchasing_organization, shortage_counter, shortage_profile` (values remain in ignored artifacts).
- Business-condition fields: `as_of, material, mrp_area, plant, purchasing_organization, shortage_counter, shortage_profile` (values remain in ignored artifacts).
- Accepted business grain: `material, plant, requirement_id`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| shortage_mrp | API_MRP_MATERIALS_SRV_01 | 2.0 | MaterialCoverages | 1 | 1 | Material, MaterialShortageProfile, MaterialShortageProfileCount, MRPArea, MRPPlanningSegmentNumber, MRPPlanningSegmentType, MRPPlant | true | true |
| shortage_pr | API_PURCHASEREQ_PROCESS_SRV | 2.0 | A_PurchaseRequisitionItem | 137 | 1 | PurchaseRequisition, PurchaseRequisitionItem | true | true |
| shortage_po_items | API_PURCHASEORDER_PROCESS_SRV | 2.0 | A_PurchaseOrderItem | 40 | 1 | PurchaseOrder, PurchaseOrderItem | true | true |
| shortage_po_schedules | API_PURCHASEORDER_PROCESS_SRV | 2.0 | A_PurchaseOrderScheduleLine | 42 | 1 | PurchasingDocument, PurchasingDocumentItem, ScheduleLine | true | true |
| shortage_sources | API_INFORECORD_PROCESS_SRV | 2.0 | A_PurgInfoRecdOrgPlantData | 3 | 1 | PurchasingInfoRecord, PurchasingInfoRecordCategory, PurchasingOrganization, Plant | true | true |

Schema/query manifests:
- `shortage_mrp` schema `sha256:1adaff0c7faaab6671558af7516c825cc212e8c7dc17058bc2aa8ea2aa3921cd`; query `sha256:8623cee3a00028b82cdd2a6d0674f187e29d449a94b1b4c64b4ae35af17fee6e`.
- `shortage_pr` schema `sha256:9f1abb5eba3bd75c225c00ee708af33330fb7b1b05979a566887cb2502389df3`; query `sha256:cc02d350a5b1f164cdccf91957a9167a1206bb657e70eb149b1417494f84fde2`.
- `shortage_po_items` schema `sha256:4b533b032e4c22cd43e2ec1ed8c3e41d57621e6f37ffa4894468e8f32bc91f32`; query `sha256:7a0aea21df4911eb9c744ae055b68c4a6178548b34875bb94a51c6f3e342bdbd`.
- `shortage_po_schedules` schema `sha256:4b533b032e4c22cd43e2ec1ed8c3e41d57621e6f37ffa4894468e8f32bc91f32`; query `sha256:692c45539cae8cf1c637f50783473f58e9a1247ab3bb0dc211be544ebc54ccbd`.
- `shortage_sources` schema `sha256:4f9de6ec7817a29976a4f17ed17c89d5ea91ed6fbfdd0726b6a764844948eab0`; query `sha256:95737d9161758c57d0b1e95a6d1adcb578455453b822cbc003670588848863e2`.

- Test-data qualification: `blocked`.
- Qualification evidence: `shortage_mrp`; reasons: `qualified_shortage_test_data_missing`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
