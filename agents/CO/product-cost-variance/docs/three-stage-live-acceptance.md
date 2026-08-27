# Three-stage live SAP acceptance: product-cost-variance 0.2.0

## Verdict

`PASS` / `executable=true`

- Tested at: `2026-08-26`
- Sample: manufacturing order `1001233`, fiscal period `2020/011`, target-cost variant `1`
- Cost source: `I_MfgOrderActlPlanTgtLdgrCost`
- SAP write operations: none
- Blocking limitation: none

## Reconciliation

| Stage | Result | Evidence |
| --- | --- | --- |
| Direct ADT multiline POST baseline | MATCH | HTTP 200; `totalRows=21`; 21 returned rows; complete |
| Standalone `sap-production-order-cost-analysis` | MATCH | 21 raw rows; 8 cost elements; source and evidence complete |
| Fixed SAPBusinessAgents Agent | MATCH | `acceptance_43e0f4902a42456e`; complete and validated |
| Free query | MATCH | `run_2e948659365248b6`; approved Skill executed through a run-, Skill-, and input-bound single-use token; complete and validated |

All four stages agree on plan `-164.26 USD`, target `-140.08 USD`, actual `211.96 USD`, and actual-minus-target `352.04 USD`, as well as order, ledger `0L`, currency role `10`, period, 21 raw rows, eight cost elements, and completeness. The normalized free-query and fixed-Agent business evidence hashes are both `sha256:9dc8bb13e982ebba95640f73e96f7ac58033c3c88ee1304db1224b62759412b7`.

## Acceptance decision

The production-cost evidence and free-query execution gaps are closed. The Harness now issues generic gap tokens only for registered, available, `read_only=true`, `validated=true` Skills after OData evidence assessment. Each token is bound to one run, one Skill, and the exact validated input, expires, and is consumed once. The Agent is promoted to `PASS/executable=true` because both required comparison paths are `MATCH`.

Earlier runs `run_dadcfcabc3da4c93` and `run_17d7a4dd62814b11` remain immutable evidence of the pre-fix field-binding and gate failures; they are not used as acceptance evidence.
