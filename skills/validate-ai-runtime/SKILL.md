---
name: validate-ai-runtime
description: Plan, audit and incrementally validate AI Runtime SDK or model capabilities in SAPBusinessAgents. Use for validation history, blocked qualifications, model changes, SDK environment updates, authorized case execution or auditable reuse. Never infer permission for model calls, SAP reads or qualification application from a request for status.
---

# AI Runtime validation

Read `<repository>/docs/runtime-validation.md` (resolve the repository, not the host installation directory). This document is authoritative; do not duplicate qualification or dependency algorithms in prompts.

1. Establish scope: status/audit, offline implementation, live cases, reuse application or protocol activation. These are separate authorizations. Default to read-only inspection.
2. Inspect current bindings, existing task/cleanup state and historical evidence. Do not use display counts as proof of loaded code or qualification.
   For WorkBuddy, distinguish SDK integration, model compatibility and advisory business evaluation. Never require 15/15 business PASS for Runtime enablement or a model switch. A new model needs only an authorized missing compatibility check, not automatic login, environment replacement or business reruns. Do not promote legacy checks into the new probe contract. SDK certification needs independently audited transport/security/cleanup evidence; infrastructure evidence does not require an old model-probe origin.
3. Run `scripts/validate-ai-runtime.py plan` for selected cases. Review every prerequisite, mode, target, parameter and expiry. Dependencies do not extend authorization.
4. Freeze an approved batch before live execution. Use the shared `run` command only when explicitly authorized and the controlled protocol migration is complete. Never dispatch the same case twice after a lost response; reconcile its owned job first.
5. Let authorized independent branches finish; pause dependents of failures. Stop affected dispatch when cleanup, identity or binding is unknown. Preserve budgets and existing Broker/Skill permissions.
6. Produce one issue matrix and a focused repair/retest plan. Separate historical PASS, current direct qualification, strict reuse, audited carry, this attempt's failures and unexecuted cases.
7. `audit-reuse` is advice only. Do not apply a receipt, activate a protocol, enable a Provider, change defaults or grant PASS without separate authorization. Never rewrite old PASS or reconstruct historical evidence from today's code.

Use the repository Python environment for the management CLI. Keep SDK execution in its existing isolated environment. Codex configuration, budgets, admission and historical storage must remain unchanged.

No model/SAP probes during offline work. No automatic retries, invented fields, relaxed checks, SDK upgrades or model switching to obtain PASS. Business quality PASS for the two acceptance capabilities still requires a complete isolated Campaign with fresh evidence, MATCH comparisons and PASS source anchors; a model does not need historical Campaign PASS to start a new formal acceptance. Keep actual operation/mode authorization, model identity and all runtime safety gates intact.

Report unknowns and safe error codes; omit credentials, authentication caches, raw reasoning and restricted SAP rows. This source package is not automatically installed in any host, and trusted local/full_access execution is not an OS sandbox.
