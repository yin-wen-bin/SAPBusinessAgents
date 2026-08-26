# Three-stage live SAP acceptance: product-cost-variance 0.2.0

## Verdict

`BLOCKED` / `executable=false`

- Tested at: `2026-08-25`
- Sample: manufacturing order `1001233`, fiscal period `2020/011`, target-cost variant `1`
- Cost source: `I_MfgOrderActlPlanTgtLdgrCost`
- SAP write operations: none
- Blocking limitation: `free_query_skill_execution`

## Reconciliation

| Stage | Result | Evidence |
| --- | --- | --- |
| Direct ADT multiline POST baseline | MATCH | HTTP 200; `totalRows=21`; 21 returned rows; complete |
| Standalone `sap-production-order-cost-analysis` | MATCH | 21 raw rows; 8 cost elements; source and evidence complete |
| Fixed SAPBusinessAgents Agent | MATCH | `acceptance_b0d862aca7c64733`; complete and validated |
| Free query | BLOCKED | `run_dadcfcabc3da4c93` used invalid bindings; corrected `run_17d7a4dd62814b11` was rejected by the Harness single-use gap-token gate |

The three successful stages agree on plan `-164.26 USD`, target `-140.08 USD`, actual `211.96 USD`, and actual-minus-target `352.04 USD`, as well as order, ledger `0L`, currency, period, raw-row count, cost-element count, and completeness.

## Acceptance decision

The production-cost evidence gap is closed. Promotion to `PASS/executable=true` is intentionally withheld because the required free-query comparison did not execute the Skill and therefore cannot be claimed as `MATCH`. After the Harness execution gate is repaired, the free query must reproduce the same complete evidence and totals before promotion.
