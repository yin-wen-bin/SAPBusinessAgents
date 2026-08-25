# Three-stage live SAP acceptance: product-cost-variance 0.2.0

## Verdict

`BLOCKED` / `executable=false`

- Tested at: `2026-08-25`
- Sample: manufacturing order `1001233`
- Direct/Embedded SAP access: GET-only
- Fixed-Agent run: `acceptance_fe28cce43bbd431e`
- Fixed-Agent result: `inconclusive`
- Blocking limitation: `production_cost_evidence`
- SAP write operations: none

## Confirmed evidence

- The production-order API returned exactly one order with company code `1710`, controlling area context `A000`, material `EWMS4-50`, and plant `1710`.
- The operational accounting OData query returned six complete actual-cost rows, all in fiscal year `2020`, period `011`, ledger/currency context compatible with the order.
- AUFK returned exactly one complete row and proved `AUFNR -> OBJNR/KOKRS/BUKRS` for the same order.
- Live ADT DDL metadata confirms both `C_MfgOrdActlPlnTgtLdgrCost` and `I_MfgOrderActlPlanTgtLdgrCost`, including the five parameters and plan, target, and actual cost fields.

## Blocking evidence

Bounded ADT Data Preview rejected both released parameterized CDS queries with HTTP 400. The dedicated Skill therefore returned:

```text
status=partial
source_complete=false
evidence_complete=false
validation_issue=parameterized_production_cost_cds_unavailable
```

No plan cost, target cost, actual cost total, variance, or cost-element row is emitted as zero. The Agent remains blocked until the released CDS can be executed or an `AUFK + ACDOCA + COSP/COSS` fallback is both available and reconciled to SAP standard cost analysis on a live sample.

## Acceptance decision

The new interface, period derivation, AUFK relationship contract, read-only Skill, deterministic cost rule, and fail-closed report are implemented. The current target does not meet the plan/target/actual evidence acceptance gate, so free-query comparison and PASS promotion are intentionally not claimed.
