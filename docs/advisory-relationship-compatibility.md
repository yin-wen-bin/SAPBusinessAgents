# Advisory relationship guidance compatibility record

## Scope

This record covers the platform change that makes curated relationship knowledge
non-blocking for Codex planning and for newly revised deterministic Agent
candidates. It does not certify any Agent, replace live acceptance, or change an
existing published Agent package.

## Recovery checkpoint

- Repository: `D:\SAPBusinessAgents`
- Checkpoint commit: `bb6dfcc933e17f6f6faeb629f2914f3a6250ec53`
- Commit message: `Save sample discovery improvements before advisory relationship changes`
- Implementation branch: `codex/advisory-relationship-guidance`
- Isolated worktree: `D:\SAPBusinessAgents-advisory-relationship-guidance`
- The checkpoint is local and has not been pushed by this implementation.

To abandon the implementation, continue from the original worktree at the
checkpoint. To inspect or selectively integrate it, use the isolated worktree.
Do not force-reset the original worktree. The Git checkpoint covers tracked
source only; ignored databases, evidence, and runtime artifacts are not a Git
recovery mechanism.

## Immutable compatibility baseline

The checkpoint records these protected Git objects:

| Protected scope | Checkpoint object | Implementation status |
|---|---|---|
| `workflows/` | `aff50ae50f36ce169875c46aed3d953571644761` | Unchanged |
| `config/business-relationships.json` | `9cc9787f18f6466e05a97ae4d9950b3f19e48612` | Unchanged |

The Agent tree contains 32 current manifests; 30 are exposed by the active
static catalog, including the platform assistant. All 32 manifests, rule sources,
versions, publication records, and validation records are unchanged. Three
generated Agent README blocks were refreshed solely to describe the two inactive
SD Agents accurately. All existing manifests omit `execution.relationshipPolicy`
and therefore retain the documented `legacy_enforced` default. The workflow tree
contains seven fixed Agent-version references. No existing Agent execution or
validation digest was rewritten.

## Policy behavior

| Package/run kind | Effective policy | Runtime behavior |
|---|---|---|
| Existing package with no policy | `legacy_enforced` | Existing relationship rejection behavior and error code are preserved. |
| New, cloned, imported, or new-version draft | `advisory` | The lifecycle service writes the policy into the candidate revision. |
| Existing unpublished draft on its next edit or explicit adoption | `advisory` | A new revision is created and prior trial/acceptance eligibility is invalidated. |
| Published advisory candidate | `advisory` | Saved deterministic steps execute without Codex; only catalog-coverage findings become guidance. |
| Free query and authoring investigation | advisory knowledge | Codex may use live schema and evidence for an unlisted or reverse direction. |

Structural dependencies, schema and type errors, invalid query syntax, unsafe
transport, SAP writes, secret-boundary violations, and incomplete composite-key
propagation remain blocking. An advisory never constitutes SAP evidence and does
not by itself change business completeness or business status.

## Verification record

Tests must import the isolated worktree explicitly because the shared virtual
environment is an editable installation of the original worktree.

- Full Python regression: 1,081 passed and 1 skipped with no failures. The only
  warning is the existing Starlette/httpx deprecation notice.
- Frontend tests: 77 passed with no failures. The production build generated 76
  pages, and Astro check reported zero errors, warnings, and hints.
- Documentation generation/check covered 53 documents with no stale generated
  blocks or broken local links.
- The repository has two inactive fixed Agents: `billing-dispute-classification`
  and `billing-output-monitor`. Both remain visible in Agent management and are
  excluded from the runnable catalog, role matching, static detail pages, and new
  runs. Neither was executed or revalidated.
- Inactive Agents whose latest version is not executable now expose
  `can_activate=false` and `agent_validation_pass_required` rather than an
  actionable activation control.
- New bilingual advisory rendering, policy validation, policy-to-digest binding,
  controlled draft adoption, deterministic no-Codex execution, and hard
  structural relationship checks passed.

Existing accepted Agents were not revalidated because their packages and
behavioral inputs did not change. A candidate that adopts `advisory` receives a
different execution digest and must follow its normal acceptance flow before a
future publication.

## Live validation boundary

Small read-only validation, when run, must use an isolated data root and a copied
draft package. It may create new test runs and evidence only in that isolated
root. It must not update the original draft, publish or activate a candidate, or
run a full three-stage acceptance campaign.

The small validation completed on 2026-09-13 with the following safe summary:

- An isolated free query for purchase orders by delivery date completed in 237
  seconds using `gpt-5.6-sol` with the recorded `max` reasoning effort. It made
  six tool calls, retained one run-scoped evidence reference, and reported both
  source and business completeness without a relationship admission error.
- An isolated copy of draft `mm-po-gr-status-check` revision 3 adopted
  `advisory`; the source draft was opened read-only and was not updated.
- Automatic sample discovery selected one ISO-normalized date parameter from
  100 bounded candidates in 75.6 seconds using one query.
- The deterministic trial completed all five SAP read plans plus its local rule,
  returned `PASS`, and recorded `relationship_binding_unapproved` as advisory
  guidance instead of `agent_relationship_rejected`.
- The five SAP steps produced record counts of 567, 336, 504, 384, and 654 with
  source and evidence completeness preserved. The purchase-order-item,
  material-document-item, and material-document-header propagations each retain
  their complete two-field, same-source composite keys.
- No existing Agent was revalidated, and neither live check published or
  activated a candidate.
