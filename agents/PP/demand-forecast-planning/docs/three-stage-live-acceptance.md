# Three-stage live SAP acceptance: demand-forecast-planning 0.3.0

## Verdict

`PASS` / `executable=true` on 2026-09-01.

All SAP operations were strictly GET-only. The accepted sample used plant and MRP area `3000`, horizon `2026-09-01` through `2026-12-31`, and three independently scoped materials:

- `YS_FG_001`
- `YS_ROH_01`
- `YS_SF_01`

## Accepted runs

| Path | Run | Result |
|---|---|---|
| Codex free query with the exact deterministic rule contract | `run_e0672d76c9c944d0` | complete / source complete |
| Batch fixed Agent | `acceptance_1df95d4f08774da6` | complete / evidence complete |
| Individual fixed Agent: YS_FG_001 | `acceptance_623e6eb2ad9d4ca1` | match |
| Individual fixed Agent: YS_ROH_01 | `acceptance_c9d303bda3e2437c` | match |
| Individual fixed Agent: YS_SF_01 | `acceptance_e55fa044b3014143` | match |

The direct provider baseline independently returned complete `SupplyDemandItems` sequences for the same three material scopes. Batch and individual fixed runs matched on PIR, sales demand, planned orders, lowest MRP availability, status, and completeness.

## Reconciled business result

| Material | Sales demand | PIR | Planned orders | PIR status | MRP status | Business status |
|---|---:|---:|---:|---|---|---|
| YS_FG_001 | 0 PC | 500 PC | 500 PC | `pir_over_coverage` | `covered` | `attention` |
| YS_ROH_01 | 0 PC | 0 PC | 200 PC | `no_activity` | `covered` | `normal` |
| YS_SF_01 | 0 PC | 0 PC | 400 PC | `no_activity` | `covered` | `normal` |

Recommendations are ordered deterministically: review PIR first and only then adjust planned orders.

## Safety and resilience checks

- The accepted batch artifact contains 18 GET declarations and no POST, PUT, PATCH, or DELETE declaration.
- `SupplyDemandItems` is not sortable in the live SAP metadata. The Agent therefore queries one material per chunk, with at most two concurrent chunks, and preserves SAP's native MRP sequence.
- Automated failure injection proved that a failed material chunk leaves successful material results available while the batch becomes `inconclusive`.
- The first exploratory free-query interpretation introduced an unapproved custom coverage ratio. It was rejected; the accepted free-query run fixed the rule contract explicitly and matched the deterministic Agent.
