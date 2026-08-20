# Three-stage live SAP acceptance: production-scheduling-capacity

## Verdict

`BLOCKED` / `executable=false`

- Case: `production-scheduling-capacity-live-001`
- Tested at: `2026-08-20T08:35:38.067890+00:00`
- Direct baseline runtime: `codex_app_direct_sap`
- Used SAPBusinessAgents for baseline: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Blocking limitations: `complete_capacity_bucket_evidence`
- SAP write operations: none

## Evidence hashes

- Codex direct baseline: `sha256:100a0b62a7a67c870076b6e74888d5492860a077f8a85551cf93eb7b3cabc352`
- SAPBusinessAgents free query: `sha256:ec12b80cd7f34ad24b2eb67f5ab0a12f9b7b6bfd1f5f416b5d12301c69a1e255`
- Adjudicated result: `sha256:100a0b62a7a67c870076b6e74888d5492860a077f8a85551cf93eb7b3cabc352`
- Fixed Agent: `sha256:038f8f983e73e98629d05c7ecc40f26ab54f512c5b2286d9d03f3300feb65e6d`

## Comparison diagnostics

- Free-query differences: `[]`
- Fixed-Agent differences: `[]`

## Sanitized case scope

- Selection rule: first independently discovered live sample satisfying the case criteria after real-time schema validation and stable-key ordering.
- Structured input fields: `date_from, date_to, plant, work_center` (values remain in ignored artifacts).
- Business-condition fields: `date_from, date_to, plant, work_center` (values remain in ignored artifacts).
- Accepted business grain: `plant, work_center, capacity_date`.

## Direct baseline source coverage

| Source | Service | OData | Entity | Rows | Pages | Stable order | Paging complete | Source complete |
|---|---|:---:|---|---:|---:|---|:---:|:---:|
| schedule_operations | API_PRODUCTION_ORDER_2_SRV | 2.0 | A_ProductionOrderOperation_2 | 130 | 1 | OrderInternalBillOfOperations, OrderIntBillOfOperationsItem | true | true |
| schedule_work_center | API_WORK_CENTERS | 2.0 | A_WorkCenters | 1 | 1 | WorkCenterInternalID, WorkCenterTypeCode | true | true |
| schedule_work_center_capacity | API_WORK_CENTERS | 2.0 | A_WorkCenterCapacity | 87 | 1 | CapacityInternalID | true | true |
| schedule_planned_orders | API_PLANNED_ORDERS | 2.0 | A_PlannedOrder | 0 | 1 | PlannedOrder | true | true |
| schedule_planned_capacity | API_PLANNED_ORDERS | 2.0 | A_PlannedOrderCapacity | 0 | 1 | CapacityRequirement, CapacityRequirementItem, CapacityRqmtItemCapacity | true | true |
| schedule_capacity_buckets | API_WORK_CENTERS | 2.0 | A_WorkCenterCapPerBucketSet | 0 | 1 | P_CapEvalStartDate, P_CapEvalEndDate, P_CapEvalBucketType, Plant, WorkCenter, CapacityInternalID, ShiftName, CapacityEvaluationTimePeriod, CapEvalBucketType | true | true |
| schedule_planned_operations | API_WORK_CENTERS | 2.0 | A_WorkCenterCapPplineOp | 3 | 1 | Plant, MRPController, WorkCenter, CapacityInternalID, CapacityRequirement, Material, OrderID, Operation | true | true |

Schema/query manifests:
- `schedule_operations` schema `sha256:dbf59ff69034dbb6c13b012c5cb8712e4d2cef8f9061cfba4d85a67efd6b8a63`; query `sha256:496bcbc3622c288712981a2b75c643c6614b9b64130319f8d409c9cd49ee54c8`.
- `schedule_work_center` schema `sha256:30782acb2556eb760a3a4ae64ec283c5e5ea7a74ca11ec40a3803684002ddb84`; query `sha256:8aa0933d84548ee350e5a7f47934161e0390b810a1930618eefd9ced7595e4a7`.
- `schedule_work_center_capacity` schema `sha256:30782acb2556eb760a3a4ae64ec283c5e5ea7a74ca11ec40a3803684002ddb84`; query `sha256:20412aaec25495823a47afa726e6c068bf0ab44112088834e4f9dbd2f42ecd15`.
- `schedule_planned_orders` schema `sha256:379cf75a62ac739739e9764a7862f3168912cf01fc081b0b7db790b7ddde0dd0`; query `sha256:7b52e3c2a85660c0714d02bddb0709ad01a070b9be405a9ce8dca73974a376ac`.
- `schedule_planned_capacity` schema `sha256:379cf75a62ac739739e9764a7862f3168912cf01fc081b0b7db790b7ddde0dd0`; query `sha256:65c3c4f9e05f4828d636a2bdf371c2c3fe8b37a6c757b282e9e706187b49bc4d`.
- `schedule_capacity_buckets` schema `sha256:30782acb2556eb760a3a4ae64ec283c5e5ea7a74ca11ec40a3803684002ddb84`; query `sha256:9d4d5b77272830909f80fddda818b4296a1bcc10c521697f5aab67b27a494ff1`.
- `schedule_planned_operations` schema `sha256:30782acb2556eb760a3a4ae64ec283c5e5ea7a74ca11ec40a3803684002ddb84`; query `sha256:1ae760ad15816bde55959e79b10859c9dc49f6f4adccf1c8f61382c96ca0b1cb`.

## Repair and adjudication outcome

The final comparison uses stable business keys, deterministic facts, Decimal-aware metrics, currencies, units, limitations, and completeness rather than display prose or row order. Platform and fixed-Agent corrections are covered by the campaign regression suite; runtime logic contains no test-document constants.

Raw SAP rows, URLs, credentials, business identifiers, and connection details remain in ignored local artifacts.
