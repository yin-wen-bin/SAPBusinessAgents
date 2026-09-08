# MRP exception analysis v2: three-stage live SAP acceptance

## Verdict

`PASS` / `executable=true`

- Agent version: `0.2.0`
- Deterministic rule: `mrp_exception_analysis_deterministic_v2`
- Tested on: `2026-08-25` (Asia/Shanghai)
- Direct baseline runtime: `codex_app_direct_sap`
- Baseline used SAPBusinessAgents: `false`
- Free-query comparison: `MATCH`
- Fixed-Agent comparison: `MATCH`
- SAP write operations: none; every SAP business request was `GET`
- Required scope limitation: `sap_shortage_time_horizon_applies`

The priority shown by this Agent is the versioned SAPBusinessAgents business-handling priority. It is not presented as SAP native message priority.

## Live samples

| Sample | Business case | Baseline conclusion | Records / affected elements | Free query | Fixed Agent |
|---|---|---|---:|:---:|:---:|
| `TG10 / 1710 / 1710` | Zero-shortage candidate with rescheduling exceptions | shortage `0.000`; days of supply `999`; highest priority `high`; business status `attention` | `19 / 11` | `MATCH` | `MATCH` |
| `MZ-RM-C900-01 / 1710 / 1710` | Active shortage with mixed exception messages | shortage `188.000`; days of supply `-1376`; highest priority `critical`; business status `critical` | `66 / 33` | `MATCH` | `MATCH` |
| `SG21 / 1010 / 1010` | No shortage with overdue 06/07 messages | shortage `0.000`; days of supply `999`; highest priority `high`; business status `attention` | `12 / 6` | `MATCH` | `MATCH` |

The `SG21` result also proves that blank MRP element item and schedule-line segments can be legitimate parts of a stable business key; they are not treated as missing evidence.

## Source coverage

Every sample returned one complete `A_MRPMaterial` row and one complete `MaterialCoverages` row. `SupplyDemandItems` returned 12, 53, and 7 complete source rows respectively. All three sources completed in one page for every sample, and no source reached its maximum-result limit.

`SupplyDemandItems` marks its properties as non-sortable in live metadata. For the independent direct baseline only, a fully exhausted, non-truncated single page is sorted client-side after duplicate-key validation so the saved artifact is deterministic. Multi-page, truncated, or duplicate-key evidence still fails closed.

The live system exposes piece units as `PC`, `ST`, or `EA` across these APIs. The rule keeps source values for reporting and compares them through the versioned piece/each alias family; all other unequal units remain a blocking unit conflict.

## Canonical acceptance evidence

The active manifest uses the richest active-shortage sample as its canonical acceptance artifact:

- Case: `mrp-exception-analysis-v2-shortage`
- Free-query run: `run_ca1ce9e5605c4df6`
- Fixed-Agent run: `acceptance_50a0cfc8f3784d0b`
- Direct baseline: `sha256:7eb5cc138af2768c6154a8ddbbe4868f359e79af8d96527a5c06edb39b8285d9`
- Free query: `sha256:7dc2e71bd7ce5ee6882829f5314276417f2c0d70d5b9a01120b71c699ad95942`
- Adjudicated result: `sha256:7eb5cc138af2768c6154a8ddbbe4868f359e79af8d96527a5c06edb39b8285d9`
- Fixed Agent: `sha256:7ea1915b61ceebbd5fc702f9515ceb0c0461786025dc395cf1c7563bd95a0a98`
- Fixed comparison: `sha256:b9cc117a2c040d1710a7447d47c225617528bf077239bac61fc4922eb701ac16`

Supplemental acceptance artifacts:

- `TG10`: free `run_7a2ac25cecec41cb`; fixed `acceptance_535dd2a70f51435e`; both `MATCH`.
- `SG21`: free `run_fa826fc0349c4f5a`; fixed `acceptance_790fc2f11f8d44ad`; both `MATCH`.

Raw SAP rows, URLs, credentials, and connection details remain only in ignored local acceptance artifacts.

## Interpretation boundary

`source_complete=true` means the exact profile/counter queries and their paging completed. It does not mean the SAP shortage profile covers an unlimited future time axis. When `HasAcceptedShortage=X`, SAP returns the next unaccepted shortage; the report must not describe it as the first shortage on the entire timeline.
