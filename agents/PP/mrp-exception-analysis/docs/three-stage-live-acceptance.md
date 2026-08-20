# Three-stage live SAP acceptance: mrp-exception-analysis

## Verdict

`PASS` / `executable=true`

- Case: `mrp-exception-analysis-live-001`
- Tested at: `2026-08-20T08:17:30.484764+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Normalized business records: `7`
- Required limitations preserved: `none`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:af574435c1605118eb406f5263eb80725237f15d47bc7392dd62e3169e0c06ac`
- SAPBusinessAgents free query: `sha256:79fb90a73ee2adcf59556190ca73e3b13a42f0f25c15b60a24e69c713623b19b`
- Adjudicated result: `sha256:af574435c1605118eb406f5263eb80725237f15d47bc7392dd62e3169e0c06ac`
- Fixed Agent: `sha256:af574435c1605118eb406f5263eb80725237f15d47bc7392dd62e3169e0c06ac`
- Fixed comparison: `sha256:59cdb8d2dbee8272c29b65f5f982d62b66db73229ff3ed3b8190846fe190aca8`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `material, mrp_area, plant, shortage_counter, shortage_profile` (values remain in ignored artifacts).
- Business-condition fields: `material, mrp_area, plant, shortage_counter, shortage_profile` (values remain in ignored artifacts).
- Accepted business grain: `material, plant, mrp_element`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| mrp_master | API_MRP_MATERIALS_SRV_01 | 2.0 | A_MRPMaterial | 1 | 1 | Material, MRPPlant, MRPArea | true | true |
| mrp_coverages | API_MRP_MATERIALS_SRV_01 | 2.0 | MaterialCoverages | 1 | 1 | Material, MaterialShortageProfile, MaterialShortageProfileCount, MRPArea, MRPPlanningSegmentNumber, MRPPlanningSegmentType, MRPPlant | true | true |
| mrp_supply_demand | API_MRP_MATERIALS_SRV_01 | 2.0 | SupplyDemandItems | 7 | 1 | Material, MaterialShortageProfile, MaterialShortageProfileCount, MRPArea, MRPPlanningSegment, MRPPlanningSegmentType, MRPPlant, MRPElement, MRPElementItem, MRPElementScheduleLine | true | true |

Schema/query manifests:
- `mrp_master` schema `sha256:1adaff0c7faaab6671558af7516c825cc212e8c7dc17058bc2aa8ea2aa3921cd`; query `sha256:75427e1dd835eb56e6f81ad50eba10c0bc2212060e8fce41df9dfdae15aa282f`.
- `mrp_coverages` schema `sha256:1adaff0c7faaab6671558af7516c825cc212e8c7dc17058bc2aa8ea2aa3921cd`; query `sha256:80bb8447c9e3d6dae2d7c7fb6b377b1650a17a44bd59336eca2c8b3f7f45fefd`.
- `mrp_supply_demand` schema `sha256:1adaff0c7faaab6671558af7516c825cc212e8c7dc17058bc2aa8ea2aa3921cd`; query `sha256:d6dba5478db7225eae3d18bc791755e62815ee96eb6481dc706dc8a474dec1b7`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
