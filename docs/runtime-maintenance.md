# Runtime maintenance

## Launcher recovery and deferred workspace changes

The launcher checks target SDK integration evidence before preparing a frontend
build. A workspace candidate with missing integration evidence is not certified
or deployed implicitly. The launcher may restore only the recorded deployed
API archive and frontend pair: archive content, unchanged interpreter/dependency
binding, build identity, activity, writer, cleanup and admission checks remain
mandatory before a cold start. An invalid deployed target still blocks startup.

The console and startup result explicitly distinguish the loaded source digest
from the requested workspace digest (`update_deferred`, `current_changes_loaded`).
Pending changes must not be presented as loaded. The persisted deferral is only
a negative result tied to exact workspace content and SDK management-state
bytes; it never grants execution eligibility. Healthy exact-version reuse does
not rebuild or re-audit; cold starts still run the deployed target's full audit.

Full-content hashes are calculated with bounded I/O concurrency at the existing
consistency boundaries, not replaced by timestamps. Short-lived project writers
are waited out without holding the switch lock or stopping them. Windows archive
rename failures 5/32/33 receive bounded rename-only retries; an existing target
is never overwritten and persistent denial retains the staged archive.

The settings page separates versions and maintenance, diagnostics and validation,
and risk-sensitive actions. Opening these sections or switching SDK tabs only
reads display state; it never installs packages, checks authentication or probes
a model.

## Explicit version reads

`POST /api/system/sdks/{sdk_id}/check?scope=versions` returns the existing `item`
envelope with installed/available versions, update availability and a
`version_check` record (`status`, `checked_at`, safe `error`). It only reads the
package registry and installed package metadata. The default `scope=full`
preserves the original installation/authentication check. State GETs never
contact the package registry automatically.

A failed registry read means the available version is unknown, not that the SDK
is current. Its error code and phase remain available in collapsed diagnostics.
Version display data is not execution authorization and is not a new model or
business PASS.

## Existing updates

The Codex installation location and target-version comparison remain unchanged.
An update takes an exclusive authentication-service maintenance
reservation: existing business tasks, authentication operations/checks and
unconfirmed cleanup block it, while new calls cannot acquire a lease during the
mutation. No thread lock is held across an await. A successful response confirms
installation, not that the running service loaded the new dependency. When
required, the page explicitly says restart pending; it never restarts the API.

An update response that is lost or uncertain freezes the original action in the
page session. Reloading or rereading status must not submit another installation.
This is a UI safety guard, not a persistent installer-job protocol. A status read
alone cannot reconstruct the exact outcome of an interrupted legacy installer.

### Codex: verified download before local installation

The Python adapter stages an explicitly requested Codex update before mutating
the installed environment. It freezes the registry target, downloads binary
wheels and all dependencies from PyPI, and verifies each exact filename, size and
SHA-256 against PyPI release metadata. The SDK wheel's own exact CLI dependency
is authoritative; the CLI version is not guessed from the SDK version. Complete
verified bundles can be reused only after inventory, content and pinned-CLI
checks. Partial downloads are never used for installation. This is a local
derived package cache, not an SDK release manager or a model qualification.

Download/verification has a bounded 30-minute budget, with a 60-second
no-progress guard on the downloader and bounded network operations. No automatic
retry or second installation is submitted. The original 300-second installation
budget is retained and shared by offline resolution and installation; removing
the network download from that window addresses large native CLI wheels without
extending the environment-mutation budget.

Downloading holds neither the SDK mutation lock nor an authentication maintenance
reservation. Before local resolution, the original full preflight and exclusive
maintenance gate run again. A changed installed version or an unresolved gate
rejects installation, rather than silently changing the frozen target. Offline
pip resolution selects the transaction against the current environment; only
those verified, hash-locked local wheels are installed, without unnecessarily
upgrading already-satisfied dependencies.

The package-manager helper is attached to an owned Windows Job before its stdin
gate opens. Timeouts stop that owned process tree, with a 10-second active
cleanup window. If absence cannot be confirmed, display `cleanup_pending` and
retain the reservation while observing; do not report stopped or permit another
installation. Caller cancellation/disconnection cannot release maintenance
while the installation thread is still active. Unsupported platforms fail
explicitly rather than falling back to an unowned installer.

State GETs overlay the current update phase without invalidating Runtime or
authentication display fingerprints. The page shows download bytes and safe
wheel filenames, verification, local resolution, installation, cleanup and
version confirmation. Diagnostics under
`.local-data/sdk-update-diagnostics/<sdk_id>/` contain bounded phase/status
receipts, safe codes and observed versions, never raw installer output or
credentials. These receipts are audit artifacts, not a new persistent execution
or recovery protocol. After API replacement, an interrupted operation still
requires controlled review; GET must not infer success or dispatch a replacement.

After any uncertain installation, repeat installation is blocked in the running
manager. An explicit versions-only check can reconcile it only after dependency
consistency and actual SDK/CLI versions are checked. Confirmed installation
still reports **restart pending**, never that the running API has already loaded
the new SDK. Authentication, integration and model compatibility gates remain
independent and unchanged; successful package installation grants no PASS.

### WorkBuddy: complete online update in an independent environment

Version checks use the existing explicit `check?scope=versions` endpoint and do
not depend on installation, authentication or activation eligibility. They read
the official stable package version only. Opening or refreshing settings never
starts a check, installer or model probe.

After explicit confirmation, the WorkBuddy update POST freezes
`expectedTargetVersion` and returns HTTP 202 with an operation ID and live
`update_status`. The existing SDK status GET exposes progress without replaying
work. Durable operation receipts under `.local-data/sdk-update-jobs/workbuddy/`
retain the source binding, original deadline and model-dispatch marker. The
existing confirmation header remains required; Codex responses are unchanged.

The verified wheel cache, owned package helper and independent bootstrap build
a new immutable candidate. The platform then checks the candidate through the
official authentication adapter, runs frozen infrastructure checker cases,
and submits the current default model's memory-only compatibility check once.
The dispatch marker is saved before submitting. No SAP call or business PASS is
part of an SDK update. Other model records remain historical and do not qualify
the new environment automatically.

Target evidence includes the exact SDK/CLI/worker inventory, actual checker XML,
checker sources and owned native compatibility-job origin. Current model-check
v3 provenance is audited under its own protocol rather than the legacy host
management digest. Missing or failed evidence prevents activation.

Download keeps the existing 1800s/60s idle limits; independent installation keeps
its 700s owned envelope and individual 60s venv/300s pip limits. Infrastructure
checks have 300s; official account checks and the model probe retain their
existing limits. The operation has a persisted 3600s execution deadline. Cleanup
uses the existing 10s active window, then retains the reservation while awaiting
confirmed process absence. Interrupted operations become `needs_review`; GET
and restart never reinstall, redispatch a model or reset a deadline.

Activation takes the deployment/admission locks after idle and writer checks,
atomically switches the environment reference and publishes the freshly
verified account observation. Subsequent workers load the new immutable
release; the API does not need to import or upgrade its own SDK packages. Actual
SDK/CLI versions, target admission, authentication and cleanup must pass before
success is reported. A post-switch failure uses the same guarded rollback to the
old release, retaining appended audit/model/switch history. An unconfirmed
rollback or cleanup retains the maintenance fence and cannot be reported as
success. The legacy pinned installer is never an online-update fallback.

## Protection and rollout

SDK integration certification, model compatibility and historical business
quality are separate. Records are retained; retention does not establish their
applicability to a different SDK/CLI/environment. No maintenance display can grant
permissions or bypass an execution gate.

Changes to SDK management or authentication infrastructure require the normal
dependency-impact audit before deployment. Offline tests do not grant target
integration certification. Deliver the frontend build and backend source as an
offline candidate; do not upgrade, change worker, apply receipts or restart
services without a separately controlled operation.
