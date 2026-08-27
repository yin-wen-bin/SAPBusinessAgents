# Four-path live SAP acceptance: internal-order-project-control 0.4.0

## Verdict

`BLOCKED` / `executable=false`

- Tested at: 2026-08-27
- Direct WBS relationship baseline: `MATCH`
- Standalone resolver: `MATCH`
- SAPBusinessAgents SkillRegistry resolver execution: `MATCH`
- Fixed-Agent complete comparison: `BLOCKED`
- Free-query complete comparison: `NOT_TESTED`
- SAP write operations: none

The WBS resolver itself is live-validated and independently executable, so `wbs_external_id_conversion` is closed. That does not make the full Agent executable: the commitment Skill remains unvalidated because the target has no approved period-bearing WBS SOAP binding and the internal-order COSP/COSS amount projection is unstable. Plan, budget-ledger/currency, complete test data, and full direct/Skill/free-query/fixed-Agent comparisons remain separate gates.

## Comparison

| Path | Direct target evidence | Skill / Agent result | Conclusion |
|---|---|---|---|
| WBS relationship | Project V2 and Financial WBS each return one exact row; metadata fingerprints match; six relationship checks agree | Standalone resolver and registry execution return `complete/resolved`, two rows total, `evidence_complete=true` | MATCH for resolver scope |
| WBS commitment | Official operation semantics documented, but no target SOAP binding | `partial/wbs_commitment_source_unavailable`, no details or totals | Stable fail-closed MATCH |
| Internal-order commitment | COSP key evidence proves a type22/ledger00/CNY row and COSS proves an empty key result; period amount projection repeatedly fails with `Unknown column VERS` | `partial/internal_order_commitment_source_unavailable`, no details or totals | Stable fail-closed MATCH; amount scope blocked |
| Fixed Agent | v4 manifest is wired to both Skills and exposes separate mode gates | Agent remains disabled; complete business comparison cannot run | BLOCKED |
| Free query | Resolver gap-token contract has automated regression coverage | No qualified complete object-mode business sample | NOT_TESTED |

## Blocking limitations

- `plan_evidence`
- `budget_evidence`
- `commitment_evidence`
- `budget_ledger_ambiguous`
- `currency_not_comparable`
- `wbs_commitment_source_unavailable`
- `internal_order_commitment_source_unavailable`
- `wbs_mode_acceptance`
- `internal_order_mode_acceptance`
- `test_data_gap`
- `free_query_comparison`

## Release gate

1. Activate and fingerprint an authoritative period-bearing WBS commitment source and, independently, an internal-order source.
2. Pass nonzero and authoritative-zero baselines for every supported object mode and requested value type.
3. Prove a versioned budget-ledger rule and comparable currency/currency-role scope.
4. Complete direct SAP, standalone Skill, free query, and fixed Agent comparison for each mode being declared complete.
5. Only a mode whose four paths all match may close `commitment_evidence`; unrelated gaps remain independent.

Until then, validation remains `BLOCKED`, `executable=false`, and historical evidence is not rewritten.
