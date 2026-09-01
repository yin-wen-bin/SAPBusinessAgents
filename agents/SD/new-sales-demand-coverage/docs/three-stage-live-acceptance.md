# Three-stage live SAP acceptance: new-sales-demand-coverage 0.1.0

## Verdict

`PASS` / `executable=true` on 2026-09-01.

The accepted sample used plant and MRP area `3000`, analysis date `2026-09-01`, horizon end `2026-11-30`, and three independent demand rows:

| Material | User demand | Demand date |
|---|---:|---|
| YS_FG_001 | 100000 PC | 2026-10-15 |
| YS_ROH_01 | 250 PC | 2026-10-15 |
| YS_SF_01 | 350 PC | 2026-10-15 |

## Accepted runs

| Path | Run | Result |
|---|---|---|
| Codex free query with the exact simulation contract | `run_781f04fdbe614b6b` | complete / source complete |
| Batch fixed Agent | `acceptance_b3a7facb56334f51` | complete / evidence complete |
| Individual fixed Agent: YS_FG_001 | `acceptance_a6c96df851824121` | match |
| Individual fixed Agent: YS_ROH_01 | `acceptance_8ff40cb64fcb4b66` | match |
| Individual fixed Agent: YS_SF_01 | `acceptance_cf8a7e79e3184c88` | match |

The direct provider baseline returned complete SAP MRP sequences for all three exact material scopes. Batch, individual, and Codex paths matched on the demand-date balance, simulated balance, subsequent minimum, first shortage date, business status, and completeness.

## Reconciled simulation result

| Material | Before demand | After demand | Subsequent minimum | First shortage | Result |
|---|---:|---:|---:|---|---|
| YS_FG_001 | 200 PC | -99800 PC | -99800 PC | 2026-10-15 | `creates_shortage` |
| YS_ROH_01 | 100 PC | -150 PC | -250 PC | 2026-10-15 | `creates_shortage` |
| YS_SF_01 | 200 PC | -150 PC | -150 PC | 2026-10-15 | `creates_shortage` |

For every material, `atp_status=not_assessed`. These results are an MRP snapshot simulation and are not a formal SAP ATP confirmation.

## Safety and resilience checks

- The accepted batch artifact contains 8 GET declarations and no POST, PUT, PATCH, or DELETE declaration.
- Current stock is displayed but never added again to `MRPAvailableQuantity`.
- `SupplyDemandItems` is queried one material per chunk because the live entity does not support server-side ordering; at most two chunks run concurrently.
- Automated failure injection proved that a failed material chunk preserves successful material results and makes the batch `inconclusive`.
