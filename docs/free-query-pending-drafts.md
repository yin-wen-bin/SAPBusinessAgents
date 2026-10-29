# Confirmed incomplete results as authoring scaffolds

User confirmation and business acceptance are separate facts. A confirmed latest
free-query result can become a `needs_review` scaffold even when source/business
evidence remains incomplete. It does not acquire PASS, execution authority or
publication eligibility. Codex and WorkBuddy use the same platform implementation.

## Creation boundary

The existing completed/full-evidence conversion remains compatible. The new
incomplete-result path additionally requires a satisfied session, matching latest
accepted iteration and recomputed result digest, a persisted verified report,
successful platform handoff/cleanup receipts and an owned GET-only replay plan.
Cancelled, running, failed-handoff, cleanup-pending, tampered, cross-session or
unverifiable references remain blocked. Unsupported historical receipts are not
silently repaired. No model or SAP call is needed to create this scaffold.

The pending path bypasses optional SDK draft authoring; it generates only the
existing deterministic read-only template and explicit review tasks. Original
filters and source identity remain attached; existing conservative input
parameterization does not authorize broader reads. Unknown/rejected aliases are
retained as gap diagnostics, never registered as evidence.

## Preserved provenance and follow-up

`origin.pending_review`, `authoring.pendingReview` and the private
`docs/pending-review.json` record the accepted session/round/result, execution source,
replay-plan digest, report reference, runtime digests, owned evidence references and
content digests, original completeness flags, evidence gaps and review tasks.
Raw SAP rows, SDK output and credentials are not copied into the scaffold.
Source run records and previous PASS records are not rewritten.

Management import uses the existing identity/import mapping. Its initial revision
is `needs_review`, `NOT_TESTED`, `executable=false`. The workspace distinguishes
historical source gaps from subsequent checks of the current revision. Business
output, source semantics, deterministic rules and parameter scope still need review;
trial, formal acceptance, identity confirmation and publication keep their existing
checkers. Editing a note cannot grant PASS. Deleted import targets are not restored.

## Existing API compatibility

Existing session/run/history GETs add `draft_creation_mode` (`standard` or
`needs_review`) and `draft_warnings`; `can_create_draft` means **creation only**.
`draft_blocker` describes hard creation blockers, not business qualification.
The existing session draft POST accepts optional `expectedIteration` and
`expectedResultDigest`. The new UI sends the frozen confirmation; stale sources
are rejected. Legacy callers retain server-side latest-confirmation checks.
Existing per-session locks, creation reservations and deterministic import mapping
recover identical requests without creating another draft. Session attachment is
conditional so a concurrent reopen/new confirmation cannot be overwritten.
No new execution endpoint or database table is introduced.

## Offline verification and rollout

Use synthetic isolated stores, the offline network/vendor guard, existing lifecycle
tests and mocked frontend APIs. Verify both Providers, incomplete/zero-result
distinctions, invalid plans/references, handoff/cleanup, stale confirmation,
interrupted linkage, deleted imports and downstream publication refusal. Browser
tests cover Chinese/English, 642px and 390px, including the non-PASS warning.

This change is delivered as an offline candidate. Before a separate authorized
deployment, freeze only reviewed changes, audit applicable execution/certificate
dependencies, confirm idle tasks/writers/cleanup, then use the existing controlled
API/frontend switch and GET-only verification. Do not modify certificate algorithms,
rewrite PASS records, replay a real query, create a current-user draft or restore a
deleted draft as a deployment check. Preserve the existing rollback build/data.
