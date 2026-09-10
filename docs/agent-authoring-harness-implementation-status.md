# Agent authoring Harness implementation checkpoint

Updated 2026-09-10. This is a partial implementation, not SAP acceptance or proof
that production tool execution is operational.

## Latest full-access implementation checkpoint (supersedes the configuration below)

The new authoring policy is version 2, `mode=full_access`. Existing version-1
operations retain their named Windows profile. Free-query start/resume/turn
calls explicitly use full access; evidence-only reinterpretation is unchanged.
No global Codex settings, SDK installation, active Agent version or Git history
were changed. The running API was **not restarted**.

Implemented in the working tree:

- Shared full-access SDK configuration, model/effort/version/tool-availability
  snapshots and a bounded fixed-command preflight. No Windows sandbox setup is
  requested by the new path. Full access is not an OS security boundary.
- Working-copy snapshots include allowed uncommitted source as the base. Their
  existing edits are not attributed to the model. Build caches are not patches.
- Authoring connects the five-round repair primitive to **static candidate
  checks only**. This is not fixture, browser, SAP or independent acceptance.
- Task-related platform edits are saved as digest-bound pending changesets.
  Retrieval is implemented. `confirm-and-apply` deliberately returns a blocking
  verification error: independent integration, application, restart and recovery
  are **not implemented**. Dependent Agent publication remains blocked.
- Feedback displays the real full-access policy, safe preflight errors and
  pending changeset ID. It does not claim that platform edits were applied.

Actual SDK verification:

- `scripts/check_runtime_execution.py`: current installed SDK successfully
  initialized and executed the fixed Python command in full-access mode.
  Controller result: passed; zero model calls and zero SAP calls.
- A separate `gpt-5.6-sol / max` file-edit/command smoke test was **rejected by
  the host execution approval reviewer before launch**. This is not an SDK or
  model failure. No alternate command or indirect execution was used to bypass
  the rejection.
- After explicit user authorization and the host execution environment changed
  to full access, the same smoke command was rerun on 2026-09-10 and **passed**:
  `python scripts/check_runtime_execution.py --model gpt-5.6-sol --effort max`.
  The controller verified the generated result file and an actual successful
  SDK command-execution item containing the expected marker; it did not rely
  only on the model's completion claim. One model turn, zero SAP calls.
  The task-owned SDK process was closed and the temporary workspace removed.
  This proves the bounded real-model file/command path, not browser, plugins,
  child Agents, automatic SAP testing or complete business E2E acceptance.

Still required before rollout:

1. Connect task-owned Playwright browser and bounded child-SDK-thread tools.
   Both are honestly reported as not connected; only existing Broker plugins
   are available, not personal Codex apps or inherited MCPs.
2. Connect automatic SAP sample discovery, candidate trial, fixture/affected
   regressions and independent acceptance to authoring repair checkpoints.
3. Complete verified changeset application, full Diff UI, supervised restart,
   health checks and recovery; currently no proposal is eligible for application.
4. Complete the two SDK/SAP E2E flows in the authorized execution environment,
   then decide on service rollout. The basic real-model smoke gate is passed.

Current verification of this checkpoint:

- Related Python regression: **215 passed, 1 skipped**, one existing
  Starlette/httpx deprecation warning.
- Frontend build: **76 pages**; catalog checks: **32 manifests / 30 visible
  records**; Astro check: **0 errors, 0 warnings**.
- Full frontend suite before the additional full-access presentation test:
  **69 passed, 2 failed**. The same two assertions expect the inactive
  `billing-output-monitor` page/count. Its inactive publication record was
  verified from HEAD (deactivated 2026-09-08), not introduced by this patch.
- Agent-draft component suite including the new bilingual full-access test:
  **28 passed**. The subsequent publication/dependency regression:
  **90 passed, 1 skipped**.
- `git diff --check` passes. No commits, pushes or production restarts.

The sections below retain the earlier sandbox diagnostic record. They do not
describe the execution policy of newly submitted version-2 jobs.

## Current result

The user explicitly accepted localhost/loopback reachability as a residual
limitation. This does not waive filesystem protection, SAP read-only rules,
Diff review or publication approval. A reachable loopback probe is recorded as
`passed_with_limitations`, with `network_denied=false`, never as network isolation.

The tool-enabled feedback path is implemented but **not verified operational on
this host**. Real SDK smoke attempts stalled in `command/exec` sandbox startup,
before model inference. A fixed Python print probe did not finish in 55 seconds;
an earlier full-smoke attempt timed out after 240 seconds at the same preflight.
The requested model was GPT-5.6 Sol with the currently saved `max` effort.
No successful model/tool execution is claimed.

Production services have not been restarted for these changes. Smoke tests did
not change existing drafts, Agents, history, publication state or SAP data.

## Implemented scope

- New Codex feedback turns freeze the isolated-tools policy. Old persisted turns
  without it retain the JSON-only path.
- Source is copied from a pinned Git commit, excluding ignored local data,
  credentials, Git metadata and historical version packages.
- SDK shell and patch tools use a named permission profile on thread/start.
  Global plugins, apps, hooks, arbitrary MCP, web and browser remain disabled.
- Platform source is readable for investigation. Only the copied Agent package
  and scratch area are editable. Platform changes are rejected until changeset
  review/apply is connected.
- Tool children receive an explicit non-secret Windows environment allowlist.
- Preflight probes the same SDK client/profile that owns subsequent tools.
  Filesystem violations, malformed observations and runtime errors still block.
- A controller timeout also bounds sandbox setup, which SDK timeoutMs did not
  bound in live checks. Cleanup targets only the owned process tree and retains
  ownership when cleanup cannot be confirmed.
- File changes are read back by the controller. Mixed file/JSON changes are
  rejected; untouched binary attachments are preserved. Existing identity,
  Schema, managed-rule, revision and publication checks remain.
- Bilingual feedback explains the conditional tool policy, residual limitation,
  preflight failures and retry guidance. Local checks are not SAP acceptance.

## Other changes preserved in this worktree

- Candidate static checks detect unguarded optional input references without
  rewriting historical manifests.
- Explicit omitIfEmpty removes the entire filter for missing, null, blank or
  empty-array input. Mandatory references retain their failure behavior.
- Trial errors show a sanitized bilingual missing-input explanation.
- A repair-loop primitive enforces five rounds, 3600 seconds, repeated-failure
  termination and digest-bound checkpoints. It is not connected to production.

Example optional filter:

```json
{
  "field": "PurchaseOrder",
  "operator": "eq",
  "value": "{{input.purchase_order}}",
  "omitIfEmpty": "{{input.purchase_order?}}"
}
```

## Host diagnostics

Official administrator-authorized sandbox initialization completed successfully,
with readiness ready and no change to the personal Codex configuration hash.
Setup readiness alone does not certify isolation.

An earlier probe incorrectly counted Winsock error 10106 as network blocking.
Its child environment lacked required Windows variables. Both environment and
assertion were corrected: only actual access-denial codes qualify. Some earlier
directories then denied outside reads/writes but allowed loopback. These results
cannot be generalized to fresh workspaces.

Fresh full-workspace standalone probes still showed inconsistent outside-read
results. Production preflight was moved to the same App Server client/profile.
Current attempts stall during sandbox setup-refresh; the underlying cause is
not established. This is separate from the accepted loopback limitation.

A diagnostic simplification of explicit deny entries was rejected by execution
safety review and was not run. Complete restrictions were restored for the next
fixed-print probe. No manual ACL reset, firewall disablement or global sandbox
downgrade was performed.

## Remaining

1. Resolve SDK sandbox command startup; verify same-client filesystem probes,
   actual file editing/local testing and cancellation/descendant cleanup.
2. Safely reload services after checking active operations.
3. Connect the repair controller, approved SAP tools, bounded automatic sampling,
   candidate trials and independent acceptance. Direct shell SAP/HTTP is not a
   substitute for approved tools.
4. Add durable platform changesets, review/apply APIs and UI, confirmation-bound
   application, service health checks and recovery.
5. Complete the real SDK/SAP mm-po-gr-status cycle in an isolated copy.

## Verification

- Current Python authoring-tools/workspace/checks/loop/feedback/reasoning suites:
  **136 passed**, one existing Starlette/httpx deprecation warning.
- Current Agent-draft frontend tests: **27 passed**.
- Complete frontend suite: **69 passed, 2 failed**. Both failures still assume
  the inactive billing-output-monitor is in the static catalog/has a detail page;
  its inactive state was verified from HEAD, not introduced by this work.
- Astro: **0 errors, 0 warnings**. Manifest validation: **32 manifests**.
- Production build: **76 pages**. Git diff whitespace check passed.
- Real SDK tool smoke: **not passed**, sandbox startup timed out.
- No SAP calls, Agent publication/activation, service restart, commit or push.

Earlier broader regression baseline: 69 passed and 2 failed because assertions
expected the already-inactive billing-output-monitor to be active. Its recorded
deactivation predates these changes (2026-09-08). The assertions were not weakened.
