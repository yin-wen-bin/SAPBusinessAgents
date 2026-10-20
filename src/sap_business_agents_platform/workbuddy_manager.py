"""Independent management projection and checked bindings. No SDK imports."""
from __future__ import annotations

import asyncio
import json
import platform
import re
import tempfile
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .sdk_manager import SDKManagerError
from .workbuddy_environment import CONTRACT, OPERATIONS, WorkBuddyEnvironment, WorkBuddyError, digest, file_hash
from .workbuddy_supervisor import WorkBuddySupervisor
from .workbuddy_identity import checked_identity, is_concrete_model
from .workbuddy_inspection import inspected
from . import workbuddy_site as site

MODEL_CHECK_SECONDS = 90  # Independent management probe, not a business/SAP budget.
AUTHENTICATION_CHECK_SECONDS = 60  # Existing-login initialization only; no model or SAP calls.
from .workbuddy_model_protocol import SCHEMA as MODEL_CHECK_SCHEMA, CONTRACT as MODEL_CHECK_CONTRACT


def now():
    return datetime.now(timezone.utc).isoformat()


class WorkBuddyManager:
    def __init__(self, root: Path):
        self.environment = WorkBuddyEnvironment(root)
        self.supervisor = WorkBuddySupervisor(self.environment)

    def site_configuration(self, *, required=False):
        return site.configuration(self.environment.state(), required=required)

    def site_request_result(self, service_site, expected_revision, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id):
            raise WorkBuddyError("workbuddy_service_site_request_invalid")
        site.make_configuration(service_site, expected_revision)
        fingerprint = digest([service_site, expected_revision])
        previous = self.environment.state().get("service_site_requests", {}).get(request_id)
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise WorkBuddyError("workbuddy_service_site_request_conflict")
            return dict(previous["result"])
        return None

    def save_site(self, service_site, expected_revision, request_id, *, selected=False):
        with self.environment.maintenance():
            prior = self.site_request_result(service_site, expected_revision, request_id)
            if prior:
                return prior
            state = self.environment.state()
            current = site.configuration(state)
            if state.get("enabled") or selected:
                raise WorkBuddyError("workbuddy_service_site_runtime_active")
            if self.supervisor.active or self.supervisor.queued or self.supervisor.reconcile():
                raise WorkBuddyError("workbuddy_service_site_busy")
            if site.external_configuration():
                raise WorkBuddyError("runtime_authentication_external_configuration")
            if current["service_site_revision"] != expected_revision:
                raise WorkBuddyError("workbuddy_service_site_revision_conflict")
            changed = current.get("service_site_migration_required") or current["service_site"] != service_site
            result = site.make_configuration(service_site, expected_revision + 1) if changed else site.binding(current)
            def update(value):
                if changed:
                    # Retain old checks/evidence; their site binding stops qualifying them.
                    value.setdefault("authentication_history", []).append({k: value.get(k) for k in (
                        "authenticated", "authentication_status", "authentication_revision", "authentication_environment",
                        "authentication_site", "checked_at")})
                    value.update(result, authenticated=False, authentication_status="not_checked",
                                 authentication_site=None, checked_at=None, error=None)
                value.setdefault("service_site_requests", {})[request_id] = {
                    "fingerprint": digest([service_site, expected_revision]), "result": result, "changed_at": now()}
            self.environment.mutate(update)
            return {**result, "changed": bool(changed)}

    def snapshot(self, definition=None, *, selected=False, admission=False):
        try:
            return self._snapshot(definition, selected=selected, admission=admission)
        except (SDKManagerError, OSError, ValueError, TypeError):
            # WorkBuddy state damage must never prevent Codex/API discovery.
            return {"provider_id": "workbuddy", "sdk_id": "codebuddy-agent-sdk", "ecosystem": "python",
                "package_name": "codebuddy-agent-sdk", "module_name": "codebuddy_agent_sdk",
                "name": definition.name if definition else {"zh": "WorkBuddy 独立 Runtime", "en": "WorkBuddy isolated Runtime"},
                "description": definition.description if definition else {}, "enabled": False,
                "installed": False, "can_enable": False, "selectable": False, "selected": selected,
                "can_update": False, "update_available": False, "restart_required": False,
                "availability": "planned", "check_status": "failed", "capabilities": [],
                "operation_capabilities": {}, "model_check_supported": False, "default_model_id": None,
                "reasoning_effort": None, "reasoning_effort_source": "sdk_default",
                "blockers": ["workbuddy_state_or_cleanup_invalid"], "error": {"code": "workbuddy_state_or_cleanup_invalid"}}

    @inspected
    def _snapshot(self, definition=None, *, selected=False, admission=False):
        state = self.environment.state()
        current_site = site.configuration(state)
        release = None
        blockers = []
        if current_site.get("service_site_migration_required"):
            blockers.append("workbuddy_service_site_migration_required")
        try:
            release = self.environment.release()
        except WorkBuddyError as exc:
            blockers.append(exc.code)
        key = release["environment_digest"] if release else None
        if release and release.get("service_site_contract") != "1":
            blockers.append("workbuddy_service_site_worker_update_required")
        integration = self.integration(state, release)
        operations = self.operation_availability(integration)
        if integration["status"] != "certified":
            blockers.append(integration["reason"])
        model = state.get("default_model_id")
        check = self._model_check(state, release, model)
        model_ready = bool(check.get("compatible") and check.get("environment_digest") == key and site.model_matches(check, state, self.environment))
        if not model_ready:
            blockers.append("runtime_model_check_required")
        if (not state.get("authenticated") or state.get("authentication_environment") != key
                or not site.matches(state.get("authentication_site") or {}, current_site)):
            blockers.append("authentication_not_checked")
        if platform.system() != "Windows":
            blockers.append("platform_not_supported")
        if self.supervisor.pending():
            blockers.append("workbuddy_cleanup_pending")
        ready = not blockers
        can_update = bool(state.get("installation"))
        if not state.get("enabled"):
            blockers.append("runtime_disabled")
        catalog = {"catalog_digest": None, "items": []} if admission else self.models(state=state)
        quality = {"qualification": None, "advisory_only": True} if admission else self.business_evaluation(state, model)
        return {"provider_id": "workbuddy", "sdk_id": "codebuddy-agent-sdk", "ecosystem": "python",
            "name": definition.name if definition else {"zh": "WorkBuddy 独立 Runtime", "en": "WorkBuddy isolated Runtime"},
            "description": {"zh": "SDK 接入、模型兼容与业务质量分层管理；业务评测不作为统一启用门禁。",
                            "en": "Separate SDK integration, model compatibility and advisory business quality."},
            "package_name": "codebuddy-agent-sdk", "module_name": "codebuddy_agent_sdk",
            "current_version": release.get("sdk_version") if release else None,
            "cli_version": release.get("cli_version") if release else None,
            "latest_version": None, "validated_target_version": "0.3.247", "update_available": False, "can_update": can_update,
            "update_enabled": can_update, "restart_required": False, "installed": bool(release),
            "checked_at": state.get("checked_at"), "check_status": "checked" if state.get("checked_at") else "not_checked",
            "error": state.get("error"), "authenticated": state.get("authenticated"),
            "authentication_status": state.get("authentication_status", "not_checked"), "authentication_error": state.get("error"),
            "platform": "windows" if platform.system() == "Windows" else platform.system().lower(),
            "platform_supported": platform.system() == "Windows", "platforms": ["windows"],
            "capabilities": [], "operation_availability": operations,
            "operation_capabilities": (quality.get("qualification") or {}).get("operations", {}), "qualification": quality.get("qualification"),
            "sdk_integration": integration, "model_compatibility": check,
            "business_evaluation": quality,
            "integration_runtime": definition.integration_runtime if definition else {},
            "availability": "active" if ready else "planned", "enabled": bool(state.get("enabled")),
            "control_status": {"status": "ready", "revision": state.get("revision", 0), "checked_at": now()},
            "can_enable": ready, "selectable": ready and bool(state.get("enabled")), "selected": selected,
            "default_model_id": model, "model_source": "user_verified" if model else None,
            "model_status": "compatible" if model_ready else "not_checked", "model_catalog_status": "incomplete",
            "model_catalog_complete": False, "model_catalog_digest": catalog["catalog_digest"],
            "model_count": len(catalog["items"]), "model_discovery_supported": True,
            "model_check_supported": bool(release), "runtime_configuration_revision": state.get("configuration_revision", 0),
            "blockers": blockers, "provider_implemented": True,
            "live_validated": bool(quality.get("qualification") and not quality.get("projection_stale")
                and quality["qualification"].get("effective_pass_count") == quality["required_operation_count"]),
            "configuration_digest": self.configuration_digest(state, release, model, check),
            "model_check_requests": self.model_check_requests(state),
            "environment_digest": key, "reasoning_effort": None, "reasoning_effort_source": "sdk_default",
            "service_site_external_configuration": site.external_configuration(),
            "service_site_switch_allowed": not (selected or state.get("enabled") or site.external_configuration()
                or self.supervisor.active or self.supervisor.queued or self.supervisor.pending()), **current_site}

    def integration(self, state, release, *, operation=None, mode='bounded'):
        from .workbuddy_integration import projection
        if not release:
            return {"status": "not_certified", "reason": "workbuddy_environment_invalid", "operations": {}}
        return projection(self.environment, state, release, operation=operation, mode=mode)

    @staticmethod
    def operation_availability(integration):
        from .workbuddy_qualification import required_modes
        return {op: {"implemented": True, "permission_modes": integration.get("operations", {}).get(op, []),
            "required_permission_modes": list(required_modes(op)),
            "status": ("certified" if set(required_modes(op)).issubset(integration.get("operations", {}).get(op, []))
                else "partially_certified" if integration.get("operations", {}).get(op) else "not_certified"),
            "reason": None if integration.get("operations", {}).get(op) else "workbuddy_sdk_operation_not_certified"}
            for op in OPERATIONS}

    @staticmethod
    def business_evaluation(state, model):
        # Display-only saved projection. Never traverse Campaigns in admission.
        from .runtime_validation_registry import cases
        total = len({op for case in cases().values() if not case.diagnostic for op in case.operations})
        cached = state.get("business_evaluation_projections", {}).get(model)
        history = [record for name in ("validation_ledger", "qualification_records") for record in state.get(name, {}).values()
            if record.get("kind") in {"case_pass", "live_pass"}
            and (record.get("binding", {}).get("runtime", {}).get("model") == model
                or record.get("binding", {}).get("model", {}).get("requested_model") == model)]
        historical = len({op for record in history for op in record["binding"].get("operations", [record["binding"].get("operation")]) if op})
        return {"advisory_only": True, "model": model, "required_operation_count": total,
            "status": ("history_pending_review" if historical else "not_evaluated") if not cached else cached.get("status", "unknown"),
            "historical_pass_count": historical, "effective_pass_count": None,
            "qualification": cached.get("qualification") if cached else None,
            "projection_stale": True, "history_retained": True}

    @staticmethod
    def model_check_requests(state):
        return [{k: value.get(k) for k in ("request_id", "model_id", "job_id", "status", "created_at")}
                for value in state.get("model_check_requests", {}).values()]

    @staticmethod
    def configuration_digest(state, release, model, check):
        # Quality records and generic state revision are not execution config.
        return digest({"environment": release.get("environment_digest") if release else None,
            "model": model, "model_check": check.get("check_digest"),
            "authentication_revision": state.get("authentication_revision", 0),
            "enabled": state.get("enabled"), **site.binding(site.configuration(state))})

    def capabilities(self, key, *, state=None, model=None, release=None):
        from . import workbuddy_qualification as qualification
        state = self.environment.state() if state is None else state
        try:
            release = release or self.environment.release(key)
            return qualification.projection(self.environment, state, release, qualification.runtime_context(state, model))
        except WorkBuddyError as exc:
            return {op: {"status": "unverified", "implemented": True, "permission_modes": [],
                "required_permission_modes": list(qualification.required_modes(op)), "reason": exc.code,
                "contract_version": CONTRACT, "qualification_version": qualification.VERSION} for op in OPERATIONS}

    def qualification_plan(self, model=None, *, state=None, release=None):
        from . import workbuddy_qualification as qualification
        state = state if state is not None else self.environment.state()
        release = release or self.environment.release()
        result = qualification.validation_plan(self.environment, state, release, qualification.runtime_context(state, model))
        result["reuse_candidates"] = [{k: v for k, v in r.items() if k != "binding"} for r in result["reuse_candidates"]]
        return result

    def _model_check(self, state, release, model):
        from .workbuddy_qualification import effective_model_check
        from .workbuddy_model_protocol import applicable
        check = effective_model_check(self.environment, state, release, model) if release else state.get("models", {}).get(model, {})
        if check.get("compatible") is not True:
            return check
        return check if applicable(self.environment, check) else {**check, "compatible": False, "status": "runtime_model_protocol_changed"}

    def apply_model_check_reuse(self, model):
        from .workbuddy_qualification import execution_components, environment_check, model_check_origin, management_digest
        with self.environment.maintenance():
            if self._qualification_busy():
                raise WorkBuddyError("workbuddy_qualification_busy")
            state = self.environment.state()
            release = self.environment.release()
            check = state.get("models", {}).get(model, {})
            context = {**site.configuration(state), "authentication_revision": state.get("authentication_revision", 0)}
            receipt = environment_check(self.environment, state, release, context)
            source = self.environment.release(check.get("environment_digest"))
            if (not receipt or not checked_identity(check) or check.get("compatible") is not True
                    or not model_check_origin(self.environment, check, model)
                    or check.get("management_digest") != management_digest()
                    or not site.model_matches(check, state)
                    or execution_components(source)["digest"] != execution_components(release)["digest"]
                    or check.get("execution_digest") != execution_components(source)["digest"]
                    or state.get("authenticated") is not True or state.get("authentication_environment") != release["environment_digest"]):
                raise WorkBuddyError("workbuddy_qualification_reuse_not_proven")
            value = {"model": model, "source_check_digest": check["check_digest"],
                "source_environment_digest": source["environment_digest"], "target_environment_digest": release["environment_digest"],
                "execution_digest": execution_components(release)["digest"], "environment_check_id": digest(receipt),
                "management_digest": management_digest(),
                "rule": "sapba.workbuddy.model-check-reuse/1"}
            self.environment.mutate(lambda s: s.setdefault("model_check_reuse", {}).setdefault(digest(value), value))
            return digest(value)

    def migrate_qualification_history(self, snapshots=()):
        with self.environment.maintenance():
            if self.environment.state().get('qualification_protocol'):
                raise WorkBuddyError('validation_use_shared_runner')
            if self._qualification_busy():
                raise WorkBuddyError("workbuddy_qualification_busy")
            return self._migrate_qualification_history(snapshots)

    def _qualification_busy(self):
        # An inspector may not own the live API's supervisor: never reap it.
        if self.supervisor.active or self.supervisor.queued:
            return True
        for path in (self.environment.root / "jobs").glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if (value.get("status") in {"queued", "running", "cleanup_pending"}
                        or value.get("pid") and value.get("cleanup_complete") is not True):
                    return True
            except (OSError, ValueError, TypeError):
                return True
        return False

    def _migrate_qualification_history(self, snapshots=()):
        from .workbuddy_qualification import migrate_history, model_binding
        recovered = {}
        state = self.environment.state()
        for key, prior in state.get("capabilities", {}).items():
            try:
                job = json.loads((self.environment.root / "jobs" / (digest(prior["job_id"]) + ".json")).read_text(encoding="utf-8"))
            except (KeyError, OSError, ValueError):
                continue
            for snapshot in snapshots:
                if not isinstance(snapshot, dict):
                    raise WorkBuddyError("workbuddy_qualification_legacy_snapshot_invalid")
                if (snapshot.get("provider_id") == "workbuddy" and snapshot.get("model")
                        and digest(snapshot) == job.get("runtime_digest")
                        and snapshot.get("environment_digest") == job.get("environment_digest")
                        and snapshot.get("environment_digest") == prior.get("environment_digest")
                        and job.get("status") == "completed" and job.get("cleanup_complete") is True
                        and job.get("validation_job") is True and site.matches(prior, site.configuration(snapshot))
                        and prior.get("authentication_revision", 0) == snapshot.get("authentication_revision", 0)
                        and ":" + job.get("operation", "") + ":" in key):
                    self.environment.release(snapshot["environment_digest"])
                    recovered[key] = model_binding(snapshot)
        # Only explicit local migration writes metadata. Status GET remains read-only.
        self.environment.mutate(lambda s: migrate_history(s, recovered))
        return self.environment.state().get("qualification_legacy_history", {})

    def apply_qualification_reuse(self, model, source_record_id):
        from . import workbuddy_qualification as qualification
        with self.environment.maintenance():
            if self.environment.state().get('qualification_protocol'):
                raise WorkBuddyError('validation_use_shared_runner')
            if self._qualification_busy():
                raise WorkBuddyError("workbuddy_qualification_busy")
            snapshot = self.runtime_snapshot(model, require_enabled=False)
            state = self.environment.state()
            release = self.environment.release()
            candidates = qualification.reuse_candidates(self.environment, state, release, snapshot)
            candidate = next((r for r in candidates if r["source_record_id"] == source_record_id), None)
            if not candidate:
                # Repeated application returns the prior receipt only while still valid.
                prior = next((r for r in state.get("qualification_records", {}).values()
                    if r.get("kind") == "reuse" and r.get("source_record_id") == source_record_id
                    and r.get("environment_digest") == release["environment_digest"]
                    and r.get("binding", {}).get("model", {}).get("requested_model") == model), None)
                if (prior and prior["binding"] == qualification.binding(snapshot, release,
                        prior["binding"]["operation"], prior["binding"]["permission_mode"])
                        and qualification.select_record(self.environment, state, prior["binding"], release["environment_digest"])):
                    return prior["id"]
                raise WorkBuddyError("workbuddy_qualification_reuse_not_proven")
            value = {"kind": "reuse", "binding": candidate["binding"], "source_record_id": source_record_id,
                "environment_digest": release["environment_digest"], "reuse_rule": qualification.VERSION,
                "environment_check_id": candidate["environment_check_id"], "changes": ["release_placement"],
                "source_environment_digest": candidate["source_environment_digest"]}
            identifier = []
            self.environment.mutate(lambda s: identifier.append(qualification.add_record(s, value)))
            return identifier[0]

    async def check(self):
        current_site = self.site_configuration(required=True)
        progress = {}
        workspace = None
        result = None
        def event(kind, data):
            if kind == "workbuddy_authentication_started" and data.get("stage") in {"initializing", "checking_existing_login"}:
                progress["stage"] = data["stage"]
        try:
            if self.supervisor.reconcile():
                raise WorkBuddyError("workbuddy_cleanup_pending")
            release = self.environment.release()
            # The locked authentication worker does not set SDK cwd. Do not let
            # it inherit the API's potentially large/sensitive project checkout.
            # Same account/site/protocol; this management budget is separate
            # from model checks and all business/acceptance deadlines.
            workspace = tempfile.TemporaryDirectory(prefix='sapba-workbuddy-auth-check-')
            probe_id = "authentication_" + uuid.uuid4().hex
            result = await self.supervisor.run(task_id=probe_id, snapshot={"environment_digest": release["environment_digest"],
                "authentication_revision": self.environment.state().get("authentication_revision", 0), **current_site},
                operation="authentication", payload={}, seconds=AUTHENTICATION_CHECK_SECONDS, probe=True, emit=event,
                process_cwd=workspace.name)
        except Exception as exc:
            result = {"authenticated": False, "status": "failed", "error": {"code": getattr(exc, "code", "workbuddy_authentication_failed")}}
            if progress:
                result["diagnostics"] = progress
        finally:
            if workspace is not None:
                try:
                    owned = Path(workspace.name).resolve()
                    if owned.parent != Path(tempfile.gettempdir()).resolve() or not owned.name.startswith('sapba-workbuddy-auth-check-'):
                        raise OSError('Unexpected authentication workspace')
                    workspace.cleanup()
                except OSError:
                    # Owned-process cleanup remains the Supervisor's authority.
                    # Local directory cleanup cannot replace the primary error.
                    if isinstance(result, dict):
                        result.setdefault('warnings', []).append({'code': 'workbuddy_auth_workspace_cleanup_failed'})
        key = release["environment_digest"] if "release" in locals() else None
        site.assert_binding(self.environment.state(), current_site)
        self.environment.mutate(lambda state: state.update(authenticated=result.get("authenticated") is True,
            authentication_status=result.get("status"), authentication_environment=key,
            authentication_site=current_site, checked_at=now(), error=result.get("error")))
        if result.get("authenticated") is True:
            self._record_environment_check(release, current_site, probe_id)
        return result

    def _record_environment_check(self, release, current_site, probe_id):
        from .workbuddy_qualification import execution_components, management_digest
        # A probe receipt must refer to an owned completed job, never a UI claim.
        path = self.environment.root / "jobs" / (digest(probe_id) + ".json")
        if not path.exists():
            return
        with self.environment.maintenance():
            job = json.loads(path.read_text(encoding="utf-8"))
            if (job.get("operation") == "authentication" and job.get("environment_digest") == release["environment_digest"]
                    and job.get("status") == "completed" and job.get("cleanup_complete") is True
                    and job.get("authentication_revision", 0) == self.environment.state().get("authentication_revision", 0)
                    and job.get("management_digest") == management_digest()
                    and site.matches(job, current_site) and job.get("result_metadata", {}).get("authenticated") is True):
                value = {"job_id": job["task_id"], "job_digest": digest(job), "site": site.binding(current_site),
                    "environment_digest": release["environment_digest"], "execution_digest": execution_components(release)["digest"],
                    "management_digest": management_digest(),
                    "authentication_revision": self.environment.state().get("authentication_revision", 0)}
                self.environment.mutate(lambda s: s.setdefault("qualification_environment_checks", {}).setdefault(digest(value), value))
                return

    @inspected
    def models(self, *, state=None, _checked_model=None):
        state = self.environment.state() if state is None else state
        current_site = site.configuration(state)
        key = state.get("environment_digest")
        discovered = state.get("model_discovery") or {}
        if (discovered.get("environment_digest") != key or not site.matches(discovered, current_site)
                or discovered.get("source") != "locked_cli_help"):
            discovered = {}
        # The directory describes SDK discovery, not the history of arbitrary
        # model IDs that users have checked. Keep those records for audit and
        # saved runtime bindings without advertising them as SDK models.
        candidates = list(dict.fromkeys(discovered.get("models", [])))
        # Preserve the existing explicit check response's per-model details.
        # This private projection is never used by directory GET/refresh and
        # does not add the checked ID to SDK discovery or persistent config.
        if _checked_model is not None and _checked_model not in candidates:
            candidates.append(_checked_model)
        release = None
        try:
            release = self.environment.release(key)
        except WorkBuddyError:
            pass
        sdk_ready = bool(release and self.integration(state, release)["status"] == "certified")
        items = [{"model_id": model, "display_name": model, "input_modalities": ["text"],
            "default_reasoning_effort": None, "supported_reasoning_efforts": [], "service_tiers": [],
            # An expired check says nothing about whether that site offers the model.
            "selected": model == state.get("default_model_id"), "retired": False,
            "check_stale": bool(check) and not current,
            "selectable": bool(sdk_ready and check.get("compatible") and current),
            "check_status": check.get("status", "not_checked") if current else "not_checked", "check": check,
            "source": "user_verified" if check.get("compatible") and current else "locked_cli_help" if model in discovered.get("models", []) else "user_specified",
            "actual_model": check.get("actual_model") if current else None, "identity_known": checked_identity(check) if current else False}
            for model in candidates for check in [self._model_check(state, release, model)]
            for current in [check.get("environment_digest") == key and site.model_matches(check, state, self.environment)]]
        for item in items:
            item["business_evaluation"] = self.business_evaluation(state, item["model_id"])
            item["qualification"] = item["business_evaluation"].get("qualification")
            item["check_contract"] = item["check"].get("probe_contract", "legacy")
            item["model_compatibility"] = item["check"]
        protocol_items = [{k: v for k, v in item.items() if k not in {"qualification", "business_evaluation"}} for item in items]
        # Presentation is deliberately outside the existing catalog binding.
        from .model_compatibility_view import workbuddy_projection
        for item in items:
            item["compatibility_applicability"] = workbuddy_projection(state, item, ready=sdk_ready)
        return {"provider_id": "workbuddy", "items": items, "default_model_id": state.get("default_model_id"),
            "model_check_requests": self.model_check_requests(state),
            "model_catalog_complete": False, "catalog_status": "incomplete", "catalog_digest": digest(protocol_items),
            "source": discovered.get("source", "not_loaded"), "captured_at": discovered.get("captured_at"),
            "error": discovered.get("error"), "environment_digest": key, **current_site}

    async def refresh_models(self):
        current_site = self.site_configuration(required=True)
        key = self.environment.state().get("environment_digest")
        try:
            release = self.environment.release()
            key = release["environment_digest"]
            result = await self.supervisor.run(task_id=None, snapshot={"environment_digest": key, **current_site},
                operation="models", payload={}, seconds=15, probe=True)
            models = result.get("models") or []
            if not models or any(not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", model) for model in models):
                raise WorkBuddyError("workbuddy_model_discovery_failed")
            value = {"models": list(dict.fromkeys(models)), "source": "locked_cli_help",
                     "environment_digest": key, "captured_at": now(), "complete": False, **current_site}
        except Exception as exc:
            value = {"models": [], "environment_digest": key, "captured_at": now(), "complete": False, **current_site,
                     "error": {"code": getattr(exc, "code", "workbuddy_model_discovery_failed")}}
        with self.environment.maintenance():
            site.assert_binding(self.environment.state(), current_site)
            if self.environment.state().get("environment_digest") != key:
                raise WorkBuddyError("workbuddy_environment_changed")
            self.environment.mutate(lambda state: state.update(model_discovery=value))
        return self.models()

    async def update(self):
        """Rebuild from verified local inputs with the independent bootstrap only."""
        from .workbuddy_job import WindowsJob
        from .workbuddy_environment import atomic_json
        if platform.system() != "Windows":
            raise WorkBuddyError("platform_not_supported")
        inputs = self.environment.state().get("installation") or {}
        if not inputs:
            raise SDKManagerError("Run the independent installation script first.", code="workbuddy_independent_install_required")
        python, wheel = Path(inputs["python"]), Path(inputs["sdk_wheel"])
        repository = self.environment.root.parents[2]
        if python.resolve().is_relative_to(repository / ".venv"):
            raise SDKManagerError("Platform Python is forbidden.", code="workbuddy_platform_interpreter_forbidden")
        if file_hash(python) != inputs["python_sha256"] or file_hash(wheel) != inputs["wheel_sha256"]:
            raise SDKManagerError("Installation input changed.", code="workbuddy_installation_input_changed")
        async with self.supervisor.probe_lock:
            job = WindowsJob()
            process = await asyncio.create_subprocess_exec(str(python), "-I",
                str(repository / "scripts/install-workbuddy-runtime.py"), "--python", str(python),
                "--sdk-wheel", str(wheel), "--wheel-sha256", inputs["wheel_sha256"], "--supervised-start",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=0x08000000 if os.name == "nt" else 0)
            task = uuid.uuid4().hex
            from .workbuddy_job import process_identity
            record = {"task_id": task, "operation": "installation", "pid": process.pid,
                "process_identity": process_identity(process.pid), "environment_digest": self.environment.state().get("environment_digest"),
                "status": "running", "cleanup_complete": False}
            path = self.environment.root / "jobs" / (digest(task) + ".json")
            attached = False
            try:
                job.attach(process.pid)
                attached = True
                self.supervisor.active[task] = {"process": process, "job": job, "cancelled": False}
                atomic_json(path, record)
                process.stdin.write(b"1")  # Installer cannot spawn children until owned.
                await process.stdin.drain()
                process.stdin.close()
                await asyncio.wait_for(process.wait(), 700)
                if process.returncode:
                    raise WorkBuddyError("workbuddy_installation_failed")
                record["status"] = "completed"
            except BaseException:
                record["status"] = "interrupted"
                raise
            finally:
                complete = False
                try:
                    if attached:
                        complete = await asyncio.shield(self.supervisor.cancel(task))
                    else:
                        if process.returncode is None:
                            process.kill()
                        await asyncio.wait_for(process.wait(), 10)
                        complete = True
                finally:
                    self.supervisor.active.pop(task, None)
                    record["cleanup_complete"] = complete
                    if not complete:
                        record["status"] = "cleanup_pending"
                    atomic_json(path, record)
                    job.close()
            if not record["cleanup_complete"]:
                raise WorkBuddyError("runtime_cleanup_incomplete")
        return {"item": self.snapshot(), "restart_required": False}

    async def check_model(self, model_id: str, effort=None, request_id=None):
        from .workbuddy_model_probe import run
        return await run(self, model_id, effort, request_id)

    async def set_default_model(self, model):
        with self.environment.maintenance():
            snapshot = self.runtime_snapshot(model, require_enabled=False)
            self.environment.mutate(lambda state: state.update(default_model_id=model,
                configuration_revision=state.get("configuration_revision", 0) + 1))
        return snapshot

    def set_enabled(self, enabled):
        with self.environment.maintenance():
            if enabled and not self.snapshot(admission=True)["can_enable"]:
                raise SDKManagerError("Complete SDK integration and model compatibility checks first.", code="runtime_not_selectable",
                                      detail={"blockers": self.snapshot(admission=True)["blockers"]})
            self.environment.mutate(lambda state: state.update(enabled=bool(enabled),
                configuration_revision=state.get("configuration_revision", 0) + 1))

    @inspected
    def runtime_snapshot(self, model=None, *, require_enabled=True, formal=False, operation=None, mode='bounded'):
        state = self.environment.state()
        current_site = site.configuration(state, required=True)
        release = self.environment.release()
        model = model or state.get("default_model_id")
        check = self._model_check(state, release, model)
        if require_enabled and not state.get("enabled"):
            raise SDKManagerError("WorkBuddy is disabled.", code="runtime_disabled")
        integration = self.integration(state, release, operation=operation, mode=mode)
        if integration["status"] != "certified":
            raise WorkBuddyError(integration["reason"])
        if operation is not None and mode not in integration['operations'].get(operation, []):
            raise WorkBuddyError('workbuddy_sdk_operation_not_certified')
        if (state.get("authenticated") is not True
                or state.get("authentication_environment") != release["environment_digest"]
                or not site.matches(state.get("authentication_site") or {}, current_site)):
            raise WorkBuddyError("workbuddy_authentication_required")
        if self.supervisor.pending():
            raise WorkBuddyError("workbuddy_cleanup_pending")
        if (not check.get("compatible") or check.get("environment_digest") != release["environment_digest"]
                or not site.model_matches(check, state, self.environment)):
            raise SDKManagerError("Check the explicit model first.", code="runtime_model_check_required")
        if formal and not checked_identity(check):
            raise SDKManagerError("Formal acceptance requires a reported concrete model identity.", code="workbuddy_model_identity_unknown")
        if formal and model != check.get("actual_model"):
            raise SDKManagerError("Routing aliases cannot be used for formal acceptance.", code="workbuddy_model_alias_not_allowed")
        binding = {"provider_id": "workbuddy", "sdk_id": "codebuddy-agent-sdk", "version": release["sdk_version"],
            "cli_version": release["cli_version"], "model": model, "model_source": "user_verified",
            "actual_model": check.get("actual_model"), "model_identity_known": checked_identity(check),
            "model_identity_source": check.get("model_identity_source"),
            "reasoning_effort": None, "reasoning_effort_source": "sdk_default", "model_check_digest": check["check_digest"],
            "environment_digest": release["environment_digest"], "runtime_configuration_revision": state.get("configuration_revision", 0),
            "sdk_fingerprint": digest(release["files"]), "sdk_integration_id": integration["certificate_id"],
            "selected_at": now(), "capabilities": [], "authentication_revision": state.get("authentication_revision", 0), **current_site}
        binding["configuration_digest"] = digest({k: v for k, v in binding.items() if k != "selected_at"})
        return binding

    @inspected
    def prepare_admission(self, request):
        from .runtime_admission import PreparedRuntime
        request.check_deadline()
        if request.provider_id not in {None, 'workbuddy'}:
            raise WorkBuddyError('workbuddy_binding_invalid')
        if platform.system() != 'Windows':
            raise WorkBuddyError('platform_not_supported')
        if self.environment.release().get('service_site_contract') != '1':
            raise WorkBuddyError('workbuddy_service_site_worker_update_required')
        if request.snapshot is not None:
            result = self.bound_snapshot(request.snapshot)
            # A saved snapshot is not authority, and may never choose defaults.
            if request.operation is None:
                raise WorkBuddyError('workbuddy_binding_invalid')
            self.supervisor.check_operation(result, request.operation, request.permission_mode)
        else:
            result = self.runtime_snapshot(operation=request.operation, mode=request.permission_mode)
        request.check_deadline()
        return PreparedRuntime(result, request.operation, request.permission_mode)

    @inspected
    def bound_snapshot(self, snapshot, *, formal=False):
        # Never read current defaults to replace a saved environment/model.
        site.assert_binding(self.environment.state(), snapshot)
        release = self.environment.release(snapshot.get("environment_digest"))
        if snapshot.get("provider_id") != "workbuddy" or not snapshot.get("model"):
            raise SDKManagerError("Incomplete historical WorkBuddy binding.", code="workbuddy_binding_invalid")
        if snapshot.get("reasoning_effort") is not None:
            raise SDKManagerError("Invalid WorkBuddy reasoning binding.", code="workbuddy_binding_invalid")
        expected = digest(release["files"])
        if snapshot.get("sdk_fingerprint") and snapshot["sdk_fingerprint"] != expected:
            raise SDKManagerError("Bound SDK files changed.", code="workbuddy_environment_digest_mismatch")
        if snapshot.get("version") and snapshot["version"] != release["sdk_version"]:
            raise SDKManagerError("Bound SDK version changed.", code="workbuddy_environment_digest_mismatch")
        if snapshot.get("cli_version") and snapshot["cli_version"] != release["cli_version"]:
            raise SDKManagerError("Bound CLI version changed.", code="workbuddy_environment_digest_mismatch")
        if formal and not checked_identity(snapshot, flag="model_identity_known"):
            raise SDKManagerError("Unknown model identity.", code="workbuddy_model_identity_unknown")
        if formal and snapshot.get("model") != snapshot.get("actual_model"):
            raise SDKManagerError("Routing aliases cannot be used for formal acceptance.", code="workbuddy_model_alias_not_allowed")
        return dict(snapshot)

    def record_operation_validation(self, snapshot: dict, operation: str, *, job_id: str,
                                    permission_modes: list[str], evidence: dict) -> None:
        """Internal verifier only. A model/client cannot self-register capabilities."""
        if self.environment.state().get("qualification_protocol") == "sapba.validation.qualification/2":
            # Native success is only one stage; the shared runner must persist
            # the complete per-case checker report before registering a pass.
            raise SDKManagerError("Use complete case evidence.", code="validation_case_registration_required")
        from . import workbuddy_qualification as qualification
        current_site = site.assert_binding(self.environment.state(), snapshot)
        if operation not in OPERATIONS or any(mode not in {"bounded", "trusted_local"} for mode in permission_modes):
            raise SDKManagerError("Invalid validation operation.", code="workbuddy_capability_invalid")
        job_path = self.environment.root / "jobs" / (digest(job_id) + ".json")
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise SDKManagerError("Completed owned validation job required.", code="workbuddy_validation_job_missing") from None
        release = self.environment.assert_verification_binding(snapshot, formal=operation in {"acceptance_baseline", "acceptance_free_query"})
        target = qualification.binding(snapshot, release, operation, permission_modes[0]) if permission_modes else None
        if (len(permission_modes) != 1 or job.get("status") != "completed" or job.get("cleanup_complete") is not True
                or job.get("validation_job") is not True
                or set(permission_modes) != {job.get("permission_mode")}
                or job.get("operation") != operation or job.get("runtime_digest") != digest(snapshot)
                or job.get("environment_digest") != snapshot.get("environment_digest")
                or job.get("qualification_binding") != target
                or job.get("result_metadata", {}).get("structured_terminal") is not True
                or job.get("result_metadata", {}).get("actual_model") != snapshot.get("actual_model")
                or job.get("result_metadata", {}).get("identity_source") != snapshot.get("model_identity_source")
                or not evidence.get("platform_contract_passed") or evidence.get("kind") != "live_validation"):
            raise SDKManagerError("Validation did not pass its complete contract.", code="workbuddy_operation_validation_required")
        value = {"kind": "live_pass", "binding": target, "environment_digest": snapshot["environment_digest"],
            "job_id": job_id, "job_digest": digest(job), "evidence_digest": digest(evidence),
            "dependency_manifest": qualification.dependency_manifests()[operation],
            "execution_components": qualification.execution_components(release),
            "input_metadata": job.get("request_metadata", {}), "result_metadata": job["result_metadata"]}
        def save(state):
            site.assert_binding(state, snapshot)
            if snapshot.get("authentication_revision", 0) != state.get("authentication_revision", 0):
                raise WorkBuddyError("runtime_authentication_changed")
            # Recheck after acquiring the cross-process lock; no late registration.
            self.environment.assert_verification_binding(snapshot, formal=operation in {"acceptance_baseline", "acceptance_free_query"})
            if qualification.binding(snapshot, self.environment.release(snapshot["environment_digest"]), operation, permission_modes[0]) != target:
                raise WorkBuddyError("workbuddy_qualification_binding_changed")
            qualification.add_record(state, value)
        self.environment.mutate(save)
