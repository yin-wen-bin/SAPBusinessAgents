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

MODEL_CHECK_SECONDS = 90  # Independent management probe, not a business/SAP budget.


def now():
    return datetime.now(timezone.utc).isoformat()


class WorkBuddyManager:
    def __init__(self, root: Path):
        self.environment = WorkBuddyEnvironment(root)
        self.supervisor = WorkBuddySupervisor(self.environment)

    def snapshot(self, definition=None, *, selected=False):
        try:
            return self._snapshot(definition, selected=selected)
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

    def _snapshot(self, definition=None, *, selected=False):
        state = self.environment.state()
        release = None
        blockers = []
        try:
            release = self.environment.release()
        except WorkBuddyError as exc:
            blockers.append(exc.code)
        key = release["environment_digest"] if release else None
        operations = self.capabilities(key)
        def required_modes(name):
            return {"bounded", "trusted_local"} if name == "review_agent_feedback" else {"trusted_local"} if name == "workflow_authoring.v2" else {"bounded"}
        if any(operations[name]["status"] != "validated" or not required_modes(name).issubset(operations[name].get("permission_modes", []))
               for name in OPERATIONS):
            blockers.append("workbuddy_operation_validation_required")
        model = state.get("default_model_id")
        check = (state.get("models") or {}).get(model, {})
        model_ready = bool(check.get("compatible") and check.get("environment_digest") == key)
        if not model_ready:
            blockers.append("runtime_model_check_required")
        if not state.get("authenticated") or state.get("authentication_environment") != key:
            blockers.append("authentication_not_checked")
        if platform.system() != "Windows":
            blockers.append("platform_not_supported")
        if self.supervisor.pending():
            blockers.append("workbuddy_cleanup_pending")
        ready = not blockers
        can_update = bool(state.get("installation"))
        if not state.get("enabled"):
            blockers.append("runtime_disabled")
        catalog = self.models()
        return {"provider_id": "workbuddy", "sdk_id": "codebuddy-agent-sdk", "ecosystem": "python",
            "name": definition.name if definition else {"zh": "WorkBuddy 独立 Runtime", "en": "WorkBuddy isolated Runtime"},
            "description": {"zh": "独立 Runtime；认证、模型及全部首期操作均须验证，不影响 Codex 环境。",
                            "en": "Isolated Runtime; authentication, models and all initial operations require validation, without mutating Codex."},
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
            "capabilities": [], "operation_capabilities": operations,
            "integration_runtime": definition.integration_runtime if definition else {},
            "availability": "active" if ready else "planned", "enabled": bool(state.get("enabled")),
            "can_enable": ready, "selectable": ready and bool(state.get("enabled")), "selected": selected,
            "default_model_id": model, "model_source": "user_verified" if model else None,
            "model_status": "compatible" if model_ready else "not_checked", "model_catalog_status": "incomplete",
            "model_catalog_complete": False, "model_catalog_digest": catalog["catalog_digest"],
            "model_count": len(catalog["items"]), "model_discovery_supported": True,
            "model_check_supported": bool(release), "runtime_configuration_revision": state.get("revision", 0),
            "blockers": blockers, "provider_implemented": True, "live_validated": ready,
            "configuration_digest": digest({"environment": key, "models": state.get("models"), "operations": operations}),
            "environment_digest": key, "reasoning_effort": None, "reasoning_effort_source": "sdk_default"}

    def capabilities(self, key):
        from .runtime_policy import ORCHESTRATION_VERSION, orchestration_digest
        values = self.environment.state().get("capabilities") or {}
        result = {}
        for operation in OPERATIONS:
            value = dict(values.get(f"{key}:{operation}:{CONTRACT}", {
                "status": "unverified", "implemented": True, "environment_digest": key,
                "contract_version": CONTRACT, "permission_modes": [], "recovery": "platform_history"}))
            current = orchestration_digest(operation)
            if (value.get("status") == "validated" and
                    (value.get("orchestration_version") != ORCHESTRATION_VERSION
                     or value.get("orchestration_digest") != current)):
                value.update(status="unverified", reason="workbuddy_orchestration_validation_required")
            value["current_orchestration_digest"] = current
            result[operation] = value
        return result

    async def check(self):
        progress = {}
        def event(kind, data):
            if kind == "workbuddy_authentication_started" and data.get("stage") in {"initializing", "checking_existing_login"}:
                progress["stage"] = data["stage"]
        try:
            if self.supervisor.reconcile():
                raise WorkBuddyError("workbuddy_cleanup_pending")
            release = self.environment.release()
            result = await self.supervisor.run(task_id=None, snapshot={"environment_digest": release["environment_digest"]},
                operation="authentication", payload={}, seconds=15, probe=True, emit=event)
        except Exception as exc:
            result = {"authenticated": False, "status": "failed", "error": {"code": getattr(exc, "code", "workbuddy_authentication_failed")}}
            if progress:
                result["diagnostics"] = progress
        key = release["environment_digest"] if "release" in locals() else None
        self.environment.mutate(lambda state: state.update(authenticated=result.get("authenticated") is True,
            authentication_status=result.get("status"), authentication_environment=key,
            checked_at=now(), error=result.get("error")))
        return result

    def models(self):
        state = self.environment.state()
        key = state.get("environment_digest")
        discovered = state.get("model_discovery") or {}
        if discovered.get("environment_digest") != key:
            discovered = {}
        candidates = list(state.get("models") or {})
        if discovered.get("environment_digest") == key:
            candidates += [model for model in discovered.get("models", []) if model not in candidates]
        items = [{"model_id": model, "display_name": model, "input_modalities": ["text"],
            "default_reasoning_effort": None, "supported_reasoning_efforts": [], "service_tiers": [],
            "selected": model == state.get("default_model_id"), "retired": bool(check) and check.get("environment_digest") != key,
            "selectable": bool(check.get("compatible") and check.get("environment_digest") == key),
            "check_status": check.get("status", "not_checked"), "check": check,
            "source": "user_verified" if check.get("compatible") else "locked_cli_help" if model in discovered.get("models", []) else "user_specified",
            "actual_model": check.get("actual_model"), "identity_known": checked_identity(check)}
            for model in candidates for check in [(state.get("models") or {}).get(model, {})]]
        return {"provider_id": "workbuddy", "items": items, "default_model_id": state.get("default_model_id"),
            "model_catalog_complete": False, "catalog_status": "incomplete", "catalog_digest": digest(items),
            "source": discovered.get("source", "not_loaded"), "captured_at": discovered.get("captured_at"),
            "error": discovered.get("error"), "environment_digest": key}

    async def refresh_models(self):
        key = self.environment.state().get("environment_digest")
        try:
            release = self.environment.release()
            key = release["environment_digest"]
            result = await self.supervisor.run(task_id=None, snapshot={"environment_digest": key},
                operation="models", payload={}, seconds=15, probe=True)
            models = result.get("models") or []
            if not models or any(not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", model) for model in models):
                raise WorkBuddyError("workbuddy_model_discovery_failed")
            value = {"models": list(dict.fromkeys(models)), "source": "locked_cli_help",
                     "environment_digest": key, "captured_at": now(), "complete": False}
        except Exception as exc:
            value = {"models": [], "environment_digest": key, "captured_at": now(), "complete": False,
                     "error": {"code": getattr(exc, "code", "workbuddy_model_discovery_failed")}}
        with self.environment.maintenance():
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

    async def check_model(self, model_id: str, effort=None):
        if effort is not None:
            raise SDKManagerError("WorkBuddy reasoning is controlled by its SDK.", code="workbuddy_reasoning_effort_sdk_default")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", model_id):
            raise SDKManagerError("Invalid model ID.", code="runtime_model_id_invalid")
        release = self.environment.release()
        key = release["environment_digest"]
        state = self.environment.state()
        if state.get("authenticated") is not True or state.get("authentication_environment") != key:
            raise SDKManagerError("Check the locked CLI login before probing models.", code="workbuddy_authentication_required")
        workspace, cleanup_warning = None, None
        try:
            workspace = tempfile.TemporaryDirectory(prefix="sapba-workbuddy-model-")
            result = await self.supervisor.run(task_id=None, snapshot={"environment_digest": key, "model": model_id},
                operation="model_check", payload={"prompt": 'Return exactly {"status":"ok"}. No tools.',
                "system_prompt": "Compatibility probe only. No tools or external systems.", "cwd": workspace.name},
                seconds=MODEL_CHECK_SECONDS, probe=True)
            compatible = json.loads(result["text"]) == {"status": "ok"}
            actual = result.get("actual_model")
            identity_source = result.get("model_identity_source")
            identity = bool(is_concrete_model(actual) and identity_source == "assistant_message")
            code = "compatible" if compatible else "runtime_model_probe_invalid_response"
        except Exception as exc:
            compatible, actual, identity, identity_source = False, None, False, None
            code = str(getattr(exc, "code", "workbuddy_model_probe_failed"))
        finally:
            if workspace is not None:
                try:
                    workspace.cleanup()
                except OSError:
                    # Process cleanup is separately enforced by the supervisor.
                    # A locked directory must not hide the real probe outcome.
                    cleanup_warning = {"code": "workbuddy_model_workspace_cleanup_failed",
                                       "workspace_id": Path(workspace.name).name}
        record = {"provider_id": "workbuddy", "model_id": model_id, "requested_model": model_id,
            "actual_model": actual, "identity_known": identity, "status": code, "compatible": compatible,
            "model_identity_source": identity_source,
            "reasoning_effort": None, "reasoning_effort_source": "sdk_default", "environment_digest": key,
            "sdk_version": release["sdk_version"], "cli_version": release["cli_version"], "checked_at": now(),
            "error": None if compatible else {"code": code}, "source": "user_verified"}
        record["probe_timeout_seconds"] = MODEL_CHECK_SECONDS
        if cleanup_warning:
            record["warnings"] = [cleanup_warning]
        record["check_digest"] = digest(record)
        with self.environment.maintenance():
            if self.environment.state().get("environment_digest") != key:
                raise SDKManagerError("Environment changed during probe.", code="workbuddy_environment_changed")
            self.environment.mutate(lambda state: state.setdefault("models", {}).__setitem__(model_id, record))
        return {"provider_id": "workbuddy", "check": record,
                "item": next(item for item in self.models()["items"] if item["model_id"] == model_id)}

    async def set_default_model(self, model):
        with self.environment.maintenance():
            snapshot = self.runtime_snapshot(model, require_enabled=False)
            self.environment.mutate(lambda state: state.update(default_model_id=model))
        return snapshot

    def set_enabled(self, enabled):
        with self.environment.maintenance():
            if enabled and not self.snapshot()["can_enable"]:
                raise SDKManagerError("Complete operation validation first.", code="runtime_not_selectable",
                                      detail={"blockers": self.snapshot()["blockers"]})
            self.environment.mutate(lambda state: state.update(enabled=bool(enabled)))

    def runtime_snapshot(self, model=None, *, require_enabled=True, formal=False):
        state = self.environment.state()
        release = self.environment.release()
        model = model or state.get("default_model_id")
        check = (state.get("models") or {}).get(model, {})
        if require_enabled and not state.get("enabled"):
            raise SDKManagerError("WorkBuddy is disabled.", code="runtime_disabled")
        if not check.get("compatible") or check.get("environment_digest") != release["environment_digest"]:
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
            "environment_digest": release["environment_digest"], "runtime_configuration_revision": state.get("revision", 0),
            "sdk_fingerprint": digest(release["files"]), "operation_capabilities": self.capabilities(release["environment_digest"]),
            "selected_at": now(), "capabilities": []}
        binding["configuration_digest"] = digest({k: v for k, v in binding.items() if k != "selected_at"})
        return binding

    def bound_snapshot(self, snapshot, *, formal=False):
        # Never read current defaults to replace a saved environment/model.
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
        from .workbuddy_environment import atomic_json
        from .runtime_policy import ORCHESTRATION_VERSION, orchestration_digest
        if operation not in OPERATIONS or any(mode not in {"bounded", "trusted_local"} for mode in permission_modes):
            raise SDKManagerError("Invalid validation operation.", code="workbuddy_capability_invalid")
        job_path = self.environment.root / "jobs" / (digest(job_id) + ".json")
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise SDKManagerError("Completed owned validation job required.", code="workbuddy_validation_job_missing") from None
        if (job.get("status") != "completed" or job.get("cleanup_complete") is not True
                or job.get("validation_job") is not True
                or set(permission_modes) != {job.get("permission_mode")}
                or job.get("operation") != operation or job.get("runtime_digest") != digest(snapshot)
                or job.get("environment_digest") != snapshot.get("environment_digest")
                or job.get("orchestration_version") != ORCHESTRATION_VERSION
                or job.get("orchestration_digest") != orchestration_digest(operation)
                or not evidence.get("platform_contract_passed") or evidence.get("kind") != "live_validation"):
            raise SDKManagerError("Validation did not pass its complete contract.", code="workbuddy_operation_validation_required")
        previous = self.capabilities(snapshot["environment_digest"])[operation]
        modes = sorted(set(permission_modes + (previous.get("permission_modes", []) if previous.get("status") == "validated" else [])))
        value = {"status": "validated", "implemented": True, "environment_digest": snapshot["environment_digest"],
                 "orchestration_version": ORCHESTRATION_VERSION, "orchestration_digest": orchestration_digest(operation),
                 "contract_version": CONTRACT, "permission_modes": modes, "recovery": "platform_history",
                 "checked_at": now(), "evidence_digest": digest(evidence), "job_id": job_id}
        key = f"{snapshot['environment_digest']}:{operation}:{CONTRACT}"
        self.environment.mutate(lambda state: state.setdefault("capabilities", {}).__setitem__(key, value))
