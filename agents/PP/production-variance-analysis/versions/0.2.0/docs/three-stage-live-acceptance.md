# Three-stage live SAP acceptance: production-variance-analysis 0.2.0

## Verdict

`PASS` / `executable=true`

- Tested at: `2026-08-25`
- Sample: manufacturing order `1001233`
- Direct baseline runtime: direct GET-only SAP OData
- Free-query run: `run_8a16162c6f7046d9`
- Fixed-Agent run: `acceptance_e904e97916334be1`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- Evidence scope: `complete`
- Blocking limitations: none
- SAP write operations: none

## Confirmed business evidence

| Evidence | Result |
|---|---:|
| TECO | true |
| Planned order quantity | 7 PC |
| Proven final-operation confirmed yield | 7 PC |
| Finished-goods receipt | 6 PC |
| Receipt variance | -1 PC |
| Components | 3 of 3 fully withdrawn |
| Material movements | five 261 issues and one 101 receipt |
| 102/262 reversals | none |

The deterministic conclusion is `attention` with root-cause candidate
`receipt_shortfall_after_confirmation`: the final operation confirms 7 PC, but
inventory received only 6 PC. This is not reported as “only 6 PC were produced.”
Production cost is deliberately `not_assessed` and belongs to the independent
`product-cost-variance` Agent.

## Source coverage

The direct baseline, constrained free query, and fixed Agent each read the same
five GET-only source grains: production-order header, order item, operations,
components, and material-document items. Stable ordering and paging completed for
all five sources. The comparison uses the manufacturing order and exact Decimal
quantities rather than display prose or row order.

## Evidence hashes

- Fixed result: `sha256:ac7c2cbfc7ccd17e0ec4e77f3fa1113da2e71b1e82c21df26da7b79c9913c48f`
- Three-stage comparison: `sha256:fbc4aaa80e45273754b77efa902c8a5b2a3d8febc499880ae89a980f6b37628f`

Raw SAP rows, URLs, credentials, and connection details remain in ignored local
artifacts under `.local-data/live-tests/20260825-production-variance-v2/`.
