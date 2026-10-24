# Explicit future authoring selection and source-contract revision

An old draft binding is not a statement that the user is signed out. First audit
the existing same-account recovery receipt against the original model, account,
site, worker and model check. Missing evidence is not filled with current values.

An authorized user can use the existing draft PUT with `runtimeBindingMode:
"current_default"`, `expectedRuntimeDigest` and `requestId`. The digest uses
`authoring_binding.selection_digest` over the disclosed selection fields. The
server resolves the **current default** through its original strict snapshot
path, not from a client-supplied snapshot. It rechecks the selection before saving.
No login, model check, SDK call, SAP read or fallback is performed by this action.

The new draft revision atomically stores the explicit selection and an audit
history containing its prior snapshot, request fingerprint and expected digest.
Only future editing uses it. Old conversation turns, queued jobs, accepted
Campaigns and imported free-query provenance stay unchanged. The old native
thread is not resumed. Same-ID/same-body recovery happens before reading a new
default; conflicting content is rejected. Ordinary draft PUTs preserve binding.

This is not an account-recovery certificate or permission grant. Every future
dispatch retains authentication, current model/worker compatibility, operation,
scope, budget, Schema, evidence and terminal checks. A new saved business revision
does not inherit the old trial/acceptance result. Missing prerequisites still
block; no synthetic test becomes a real qualification PASS.

The supplier historical-item candidate retains its original account-item source,
GET plan, projection and filter/read limits. It declares account-item semantics,
preserves every source row, and distinguishes economic kind from cutoff status.
Duplicates/conflicts/missing keys are evidence gaps, never silently discarded or
given invented keys. Posted AP, noted requests, unknown items, cleared items and
the RE subset have separate measures by currency/ledger. Current special-GL
configuration cannot classify historical rows without applicable evidence.
General-ledger carryforward coverage is not asserted by this account-item source.

The candidate rule lives in `tests/fixtures/supplier_historical_scope_rule.py` for
review and offline sandbox regression; it is copied byte-for-byte into the saved
unpublished draft. This does not publish or enable an Agent. Preserve its managed
rule hash, the original immutable revisions and all historical qualification data.

Readiness uses the shared bilingual source-error labels. The user-facing prompt
explains the business scope/grain/category gap instead of only an internal path.
Diagnostics retain the original code and path for audit.
