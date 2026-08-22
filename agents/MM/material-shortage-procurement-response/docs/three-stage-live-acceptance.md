# Three-stage live SAP acceptance: material-shortage-procurement-response

## Verdict

`PASS` / `executable=true`

- Case: `material-shortage-procurement-response-live-002`
- Tested at: `2026-08-22T18:13:42.891729+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `1`
- Required limitations preserved: `none`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:7e51a9110bc9a5b1c5febf0a2b929b100c7b21beb12957807e82edb5679ea4c3`
- SAPBusinessAgents free query: `sha256:ad493104d6c9c115be0eb8d1b929ce2cb01bd467da4ac5c2e1ff64f97380738c`
- Adjudicated result: `sha256:7e51a9110bc9a5b1c5febf0a2b929b100c7b21beb12957807e82edb5679ea4c3`
- Fixed Agent: `sha256:7e51a9110bc9a5b1c5febf0a2b929b100c7b21beb12957807e82edb5679ea4c3`
- Fixed comparison: `sha256:1e284adbd792bb1ffcf56a7f77546be84566125e991c4529619253c5ad5af5ad`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `as_of, material, mrp_area, plant, purchasing_organization, shortage_counter, shortage_profile` (values remain in ignored artifacts).
- Business-condition fields: `as_of, material, mrp_area, plant, purchasing_organization, shortage_counter, shortage_profile` (values remain in ignored artifacts).
- Accepted business grain: `material, plant, requirement_id`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| shortage_mrp_master | API_MRP_MATERIALS_SRV_01 | 2.0 | A_MRPMaterial | 1 | 1 | Material, MRPPlant, MRPArea | true | true |
| shortage_mrp | API_MRP_MATERIALS_SRV_01 | 2.0 | MaterialCoverages | 1 | 1 | Material, MaterialShortageProfile, MaterialShortageProfileCount, MRPArea, MRPPlanningSegmentNumber, MRPPlanningSegmentType, MRPPlant | true | true |
| shortage_pr | API_PURCHASEREQ_PROCESS_SRV | 2.0 | A_PurchaseRequisitionItem | 0 | 1 | PurchaseRequisition, PurchaseRequisitionItem | true | true |
| shortage_po_items | API_PURCHASEORDER_PROCESS_SRV | 2.0 | A_PurchaseOrderItem | 0 | 1 | PurchaseOrder, PurchaseOrderItem | true | true |
| shortage_sources | API_INFORECORD_PROCESS_SRV | 2.0 | A_PurgInfoRecdOrgPlantData | 0 | 1 | PurchasingInfoRecord, PurchasingInfoRecordCategory, PurchasingOrganization, Plant | true | true |

Schema/query manifests:
- `shortage_mrp_master` schema `sha256:1adaff0c7faaab6671558af7516c825cc212e8c7dc17058bc2aa8ea2aa3921cd`; query `sha256:908358c3ec488f361b7623dcd23961d2a561683f88d17305c6681e66c0b623ac`.
- `shortage_mrp` schema `sha256:1adaff0c7faaab6671558af7516c825cc212e8c7dc17058bc2aa8ea2aa3921cd`; query `sha256:fd2d3be8645c1d98b27ce4b516499a04e90450465e6df2667d71e7383df734fe`.
- `shortage_pr` schema `sha256:9f1abb5eba3bd75c225c00ee708af33330fb7b1b05979a566887cb2502389df3`; query `sha256:bea7e25f4695bc4337860bacdc1f07660cf0eb86916ddcd05cfc75673f0f5547`.
- `shortage_po_items` schema `sha256:4b533b032e4c22cd43e2ec1ed8c3e41d57621e6f37ffa4894468e8f32bc91f32`; query `sha256:cb0635f7992a3c4d2800168e9ca37985e2c48799cd1cc1451bafeea952f274b4`.
- `shortage_sources` schema `sha256:4f9de6ec7817a29976a4f17ed17c89d5ea91ed6fbfdd0726b6a764844948eab0`; query `sha256:aa312a167d6f46e7260169ae86c87f2fd9aa18dbda0ad62b9edc06f36db9d2d3`.

Non-blocking observations:
- `mrp_snapshot_stale`: last MRP date `2026-05-12`, snapshot age `103` day(s); blocking=`false`.

- Test-data qualification: `qualified`.
- Qualification evidence: `shortage_mrp_master, shortage_mrp`; reasons: `none`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
