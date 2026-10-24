# Runtime maintenance

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

WorkBuddy's current installer rebuilds pinned SDK 0.3.247 from verified local
inputs. It is not an online upgrade facility, and rebuilding can change the active
environment and authentication binding. The settings page therefore does not
offer that path as an upgrade and leaves same-version repair disabled with a
visible explanation. The existing controlled maintenance entry point retains a
flat SDK result and is explicitly identified as environment repair.

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
