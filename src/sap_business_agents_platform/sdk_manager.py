from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from packaging.version import InvalidVersion, Version


_SDK_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_PYTHON_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_PYTHON_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_NPM_PACKAGE = re.compile(r"^(?:@[a-z0-9._-]+/)?[a-z0-9._-]+$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_AVAILABILITY = {"active", "planned", "reserved", "reserved_blocked"}
_INTEGRATION_RESOURCE_KINDS = {"app", "mcp_server"}
_INTEGRATION_FEATURES = {
    "catalog",
    "configuredDiscovery",
    "status",
    "authentication",
    "configuration",
    "directToolCall",
}
_INTEGRATION_READINESS = {"production", "preview", "reserved", "unavailable"}


class SDKManagerError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "sdk_manager_error",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


@dataclass(frozen=True, slots=True)
class SDKDefinition:
    provider_id: str
    sdk_id: str
    name: dict[str, str]
    description: dict[str, str]
    ecosystem: str
    package_name: str
    module_name: str | None
    availability: str
    declared_selectable: bool
    platforms: tuple[str, ...]
    capabilities: tuple[str, ...]
    authentication: str
    provider_implemented: bool
    live_validated: bool
    blockers: tuple[str, ...]
    project_root: str | None
    update_enabled: bool
    restart_required: bool
    integration_runtime: dict[str, Any] = field(default_factory=dict)


class SDKAdapter(Protocol):
    def installed_version(self, definition: SDKDefinition) -> str | None: ...

    async def latest_version(self, definition: SDKDefinition) -> str: ...

    async def update(self, definition: SDKDefinition, target_version: str) -> None: ...


class RuntimeProbe(Protocol):
    async def check_authentication(self, definition: SDKDefinition) -> dict[str, Any]: ...

    async def list_models(self, definition: SDKDefinition) -> dict[str, Any]: ...

    async def check_model(
        self, definition: SDKDefinition, model_id: str, workspace: Path
    ) -> dict[str, Any]: ...

    def client_version(self, definition: SDKDefinition) -> str | None: ...


class PythonPackageAdapter:
    def __init__(self, *, timeout_seconds: float = 30, update_timeout_seconds: int = 300) -> None:
        self.timeout_seconds = timeout_seconds
        self.update_timeout_seconds = update_timeout_seconds

    def installed_version(self, definition: SDKDefinition) -> str | None:
        _validate_python_package(definition.package_name)
        try:
            return metadata.version(definition.package_name)
        except metadata.PackageNotFoundError:
            return None

    async def latest_version(self, definition: SDKDefinition) -> str:
        package_name = _validate_python_package(definition.package_name)
        url = f"https://pypi.org/pypi/{quote(package_name, safe='')}/json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SDKManagerError(
                "Could not read the latest Python package version.",
                code="sdk_registry_unavailable",
            ) from exc
        latest = str((payload.get("info") or {}).get("version") or "").strip()
        _parse_version(latest)
        return latest

    async def update(self, definition: SDKDefinition, target_version: str) -> None:
        package_name = _validate_python_package(definition.package_name)
        _parse_version(target_version)
        await asyncio.to_thread(
            _run_package_manager,
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--upgrade",
                f"{package_name}=={target_version}",
            ],
            None,
            self.update_timeout_seconds,
        )


class NpmPackageAdapter:
    def __init__(
        self,
        repository_root: Path,
        *,
        timeout_seconds: float = 30,
        update_timeout_seconds: int = 300,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.timeout_seconds = timeout_seconds
        self.update_timeout_seconds = update_timeout_seconds

    def installed_version(self, definition: SDKDefinition) -> str | None:
        package_name = _validate_npm_package(definition.package_name)
        project_root = self._project_root(definition)
        package_path = project_root / "node_modules" / Path(*package_name.split("/")) / "package.json"
        if not package_path.is_file():
            return None
        try:
            payload = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SDKManagerError(
                "The installed npm package metadata is unreadable.",
                code="sdk_metadata_invalid",
            ) from exc
        installed = str(payload.get("version") or "").strip()
        _parse_version(installed)
        return installed

    async def latest_version(self, definition: SDKDefinition) -> str:
        package_name = _validate_npm_package(definition.package_name)
        url = f"https://registry.npmjs.org/{quote(package_name, safe='')}/latest"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SDKManagerError(
                "Could not read the latest npm package version.",
                code="sdk_registry_unavailable",
            ) from exc
        latest = str(payload.get("version") or "").strip()
        _parse_version(latest)
        return latest

    async def update(self, definition: SDKDefinition, target_version: str) -> None:
        package_name = _validate_npm_package(definition.package_name)
        _parse_version(target_version)
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm:
            raise SDKManagerError("npm is not installed.", code="sdk_package_manager_missing")
        await asyncio.to_thread(
            _run_package_manager,
            [npm, "install", "--save-exact", f"{package_name}@{target_version}"],
            self._project_root(definition),
            self.update_timeout_seconds,
        )

    def _project_root(self, definition: SDKDefinition) -> Path:
        if not definition.project_root:
            raise SDKManagerError(
                "An npm SDK must declare its project root.",
                code="sdk_definition_invalid",
            )
        candidate = (self.repository_root / definition.project_root).resolve()
        if candidate != self.repository_root and self.repository_root not in candidate.parents:
            raise SDKManagerError(
                "The npm project root is outside the repository.",
                code="sdk_definition_invalid",
            )
        if not (candidate / "package.json").is_file():
            raise SDKManagerError(
                "The npm project root has no package.json.",
                code="sdk_definition_invalid",
            )
        return candidate


class SDKManager:
    def __init__(
        self,
        definitions_path: Path,
        repository_root: Path,
        *,
        adapters: dict[str, SDKAdapter] | None = None,
        runtime_probes: dict[str, RuntimeProbe] | None = None,
        selection_path: Path | None = None,
        legacy_model: str | None = None,
    ) -> None:
        self.definitions_path = definitions_path.resolve()
        self.repository_root = repository_root.resolve()
        self.registry_default_provider_id, self.definitions = _load_definitions(
            self.definitions_path
        )
        self.adapters: dict[str, SDKAdapter] = adapters or {
            "python": PythonPackageAdapter(),
            "npm": NpmPackageAdapter(self.repository_root),
        }
        unsupported = sorted({item.ecosystem for item in self.definitions}.difference(self.adapters))
        if unsupported:
            raise SDKManagerError(
                "Unsupported SDK ecosystem(s): " + ", ".join(unsupported),
                code="sdk_definition_invalid",
            )
        self.runtime_probes = dict(runtime_probes or {})
        self.legacy_model = str(legacy_model or "").strip() or None
        self.selection_path = (
            selection_path.resolve()
            if selection_path is not None
            else (self.repository_root / ".local-data" / "sdk-runtimes" / "default.json").resolve()
        )
        self._state: dict[str, dict[str, Any]] = {}
        for item in self.definitions:
            installed = self.adapters[item.ecosystem].installed_version(item)
            trusted_active_login = bool(
                installed
                and item.provider_id == "codex"
                and item.authentication == "existing_login"
                and item.availability == "active"
                and item.live_validated
            )
            self._state[item.sdk_id] = {
                "latest_version": None,
                "checked_at": None,
                "error": None,
                "authenticated": True if trusted_active_login else None,
                "authentication_status": (
                    "existing_login" if trusted_active_login else "not_checked"
                ),
                "authentication_error": None,
            }
        self._locks = {item.sdk_id: asyncio.Lock() for item in self.definitions}
        self._config_lock = threading.RLock()
        self._runtime_config = self._load_runtime_config()

    @property
    def default_provider_id(self) -> str | None:
        value = self._runtime_config.get("default_provider_id")
        return str(value) if value else None

    @property
    def configuration_revision(self) -> int:
        return int(self._runtime_config.get("revision") or 0)

    def list(self) -> list[dict[str, Any]]:
        return [self._snapshot(item) for item in self.definitions]

    async def check_all(self) -> list[dict[str, Any]]:
        await asyncio.gather(*(self.check(item.sdk_id) for item in self.definitions))
        return self.list()

    async def check(self, sdk_id: str) -> dict[str, Any]:
        definition = self._get_sdk(sdk_id)
        async with self._locks[sdk_id]:
            await self._check_unlocked(definition)
            return self._snapshot(definition)

    async def check_provider(self, provider_id: str) -> dict[str, Any]:
        definition = self._get_provider(provider_id)
        return await self.check(definition.sdk_id)

    def models(self, provider_id: str) -> dict[str, Any]:
        definition = self._get_provider(provider_id)
        catalog = self._catalog(definition)
        models = [self._decorate_model(definition, item) for item in catalog.get("models", [])]
        selected_model = self._provider_runtime_state(definition)["default_model_id"]
        if selected_model and not any(item["model_id"] == selected_model for item in models):
            models.append(
                {
                    "model_id": selected_model,
                    "display_name": selected_model,
                    "is_sdk_default": False,
                    "input_modalities": [],
                    "default_reasoning_effort": None,
                    "supported_reasoning_efforts": [],
                    "service_tiers": [],
                    "upgrade_target": None,
                    "upgrade_message": None,
                    "catalog_sdk_version": catalog.get("sdk_version"),
                    "catalog_cli_version": catalog.get("cli_version"),
                    "retired": True,
                    "selected": True,
                    "check_status": "retired",
                    "selectable": False,
                    "check": None,
                }
            )
        return {
            "provider_id": provider_id,
            "sdk_version": catalog.get("sdk_version"),
            "cli_version": catalog.get("cli_version"),
            "catalog_digest": catalog.get("catalog_digest"),
            "model_catalog_complete": bool(catalog.get("model_catalog_complete")),
            "catalog_status": self._catalog_status(definition),
            "captured_at": catalog.get("captured_at"),
            "error": catalog.get("error"),
            "default_model_id": selected_model,
            "items": models,
        }

    async def refresh_models(self, provider_id: str) -> dict[str, Any]:
        definition = self._get_provider(provider_id)
        async with self._locks[definition.sdk_id]:
            probe = self.runtime_probes.get(provider_id)
            list_models = getattr(probe, "list_models", None)
            if not callable(list_models):
                raise SDKManagerError(
                    "This Agent Runtime cannot discover models.",
                    code="model_catalog_unavailable",
                )
            installed = self.adapters[definition.ecosystem].installed_version(definition)
            if not installed:
                raise SDKManagerError("The SDK is not installed.", code="sdk_not_installed")
            try:
                result = await list_models(definition)
                raw_models = result.get("models") if isinstance(result, dict) else None
                if not isinstance(raw_models, list):
                    raise ValueError("The Runtime model catalog is invalid.")
                normalized: list[dict[str, Any]] = []
                seen: set[str] = set()
                for raw in raw_models:
                    item = _normalize_model(raw)
                    if item is None:
                        continue
                    model_id = item["model_id"]
                    if model_id in seen:
                        raise ValueError("The Runtime model catalog contains duplicate ids.")
                    seen.add(model_id)
                    normalized.append(item)
                cli_version = self._client_version(definition)
                complete = not bool(result.get("next_cursor"))
                digest = _stable_digest(
                    {
                        "provider_id": provider_id,
                        "sdk_version": installed,
                        "cli_version": cli_version,
                        "models": normalized,
                        "complete": complete,
                    }
                )
                catalog = {
                    "provider_id": provider_id,
                    "sdk_version": installed,
                    "cli_version": cli_version,
                    "catalog_digest": digest,
                    "model_catalog_complete": complete,
                    "captured_at": _timestamp(),
                    "models": normalized,
                    "error": None,
                }
            except Exception as exc:
                catalog = {
                    **self._catalog(definition),
                    "captured_at": _timestamp(),
                    "error": {
                        "code": str(getattr(exc, "code", "model_catalog_unavailable")),
                        "message": str(exc) or type(exc).__name__,
                    },
                }
                self._runtime_config.setdefault("model_catalogs", {})[provider_id] = catalog
                self._persist_runtime_config()
                raise SDKManagerError(
                    "Could not read the Runtime model catalog.",
                    code="model_catalog_unavailable",
                ) from exc
            self._runtime_config.setdefault("model_catalogs", {})[provider_id] = catalog
            runtime_state = self._provider_runtime_state(definition)
            model_ids = {item["model_id"] for item in normalized}
            requested = self.legacy_model if provider_id == self.registry_default_provider_id else None
            if runtime_state.get("model_source") == "environment_migration" and requested:
                if requested in model_ids:
                    runtime_state["legacy_model_error"] = None
                else:
                    runtime_state["legacy_model_error"] = "legacy_model_unsupported"
            elif not runtime_state.get("default_model_id"):
                if requested:
                    if requested in model_ids:
                        runtime_state["default_model_id"] = requested
                        runtime_state["model_source"] = "environment_migration"
                        runtime_state["legacy_model_error"] = None
                    else:
                        runtime_state["legacy_model_error"] = "legacy_model_unsupported"
                else:
                    defaults = [item for item in normalized if item.get("is_sdk_default")]
                    if len(defaults) == 1:
                        runtime_state["default_model_id"] = defaults[0]["model_id"]
                        runtime_state["model_source"] = "sdk_default"
            self._persist_runtime_config()
            return self.models(provider_id)

    async def check_model(self, provider_id: str, model_id: str) -> dict[str, Any]:
        definition = self._get_provider(provider_id)
        model_id = _validate_model_id(model_id)
        async with self._locks[definition.sdk_id]:
            model = self._catalog_model(definition, model_id)
            probe = self.runtime_probes.get(provider_id)
            check_model = getattr(probe, "check_model", None)
            if not callable(check_model):
                raise SDKManagerError(
                    "This Agent Runtime cannot check model compatibility.",
                    code="runtime_model_check_unavailable",
                )
            workspace = (
                self.selection_path.parent
                / "probes"
                / provider_id
                / f"{_stable_digest(model_id)[:12]}-{uuid.uuid4().hex[:12]}"
            )
            workspace.mkdir(parents=True, exist_ok=True)
            installed = self.adapters[definition.ecosystem].installed_version(definition)
            catalog = self._catalog(definition)
            try:
                result = await check_model(definition, model_id, workspace)
            except Exception as exc:
                result = {
                    "compatible": False,
                    "status": str(getattr(exc, "code", "runtime_model_probe_failed")),
                    "error": {
                        "code": str(getattr(exc, "code", "runtime_model_probe_failed")),
                        "message": str(exc) or type(exc).__name__,
                    },
                }
            finally:
                shutil.rmtree(workspace, ignore_errors=True)
            if result.get("compatible") is True:
                self._update_authentication_state(
                    definition,
                    authenticated=True,
                    status="existing_login",
                    error=None,
                )
            elif result.get("status") == "runtime_model_authentication_failed":
                self._update_authentication_state(
                    definition,
                    authenticated=False,
                    status="login_required",
                    error=result.get("error"),
                )
            runtime_state = self._provider_runtime_state(definition)
            record = {
                "provider_id": provider_id,
                "model_id": model_id,
                "status": str(result.get("status") or "runtime_model_probe_failed"),
                "compatible": result.get("compatible") is True,
                "sdk_version": installed,
                "cli_version": self._client_version(definition),
                "platform": _platform_key(),
                "provider_configuration_digest": _definition_digest(definition),
                "catalog_digest": catalog.get("catalog_digest"),
                "authentication_status": (
                    self._state[definition.sdk_id].get("authentication_status")
                ),
                "authentication_revision": int(
                    runtime_state.get("authentication_revision") or 0
                ),
                "checked_at": _timestamp(),
                "error": result.get("error"),
            }
            record["check_digest"] = _stable_digest(
                {key: value for key, value in record.items() if key != "checked_at"}
            )
            self._runtime_config.setdefault("model_checks", {})[
                self._model_check_key(provider_id, model_id)
            ] = record
            self._persist_runtime_config()
            decorated = self._decorate_model(definition, model)
            return {"provider_id": provider_id, "item": decorated, "check": record}

    async def set_default_model(self, provider_id: str, model_id: str) -> dict[str, Any]:
        definition = self._get_provider(provider_id)
        model_id = _validate_model_id(model_id)
        self._catalog_model(definition, model_id)
        check = self._current_model_check(definition, model_id)
        if not check or check.get("compatible") is not True:
            result = await self.check_model(provider_id, model_id)
            check = result["check"]
        if check.get("compatible") is not True:
            error = check.get("error") or {}
            raise SDKManagerError(
                str(error.get("message") or "The selected model is not compatible."),
                code=str(error.get("code") or check.get("status") or "runtime_model_incompatible"),
                detail={"provider_id": provider_id, "model_id": model_id},
            )
        with self._config_lock:
            runtime_state = self._provider_runtime_state(definition)
            runtime_state["default_model_id"] = model_id
            runtime_state["model_source"] = "system_settings"
            runtime_state["legacy_model_error"] = None
            runtime_state["updated_at"] = _timestamp()
            self._persist_runtime_config()
        return self._snapshot(definition)

    def set_enabled(self, provider_id: str, enabled: bool) -> dict[str, Any]:
        definition = self._get_provider(provider_id)
        with self._config_lock:
            if enabled:
                snapshot = self._snapshot(definition)
                if not snapshot["can_enable"]:
                    raise SDKManagerError(
                        "The Agent Runtime has not passed all enablement gates.",
                        code="runtime_not_selectable",
                        detail={"provider_id": provider_id, "blockers": snapshot["blockers"]},
                    )
            runtime_state = self._provider_runtime_state(definition)
            runtime_state["enabled"] = bool(enabled)
            runtime_state["updated_at"] = _timestamp()
            if not enabled and self.default_provider_id == provider_id:
                self._runtime_config["default_provider_id"] = None
            self._persist_runtime_config()
        return self._snapshot(definition)

    async def update(self, sdk_id: str) -> dict[str, Any]:
        definition = self._get_sdk(sdk_id)
        if not definition.update_enabled:
            raise SDKManagerError("This SDK cannot be updated here.", code="sdk_update_disabled")
        async with self._locks[sdk_id]:
            await self._check_unlocked(definition)
            before = self.adapters[definition.ecosystem].installed_version(definition)
            latest = self._state[sdk_id]["latest_version"]
            if before is None:
                raise SDKManagerError("The SDK is not installed.", code="sdk_not_installed")
            if not latest or _parse_version(latest) <= _parse_version(before):
                raise SDKManagerError("The SDK is already up to date.", code="sdk_already_current")
            await self.adapters[definition.ecosystem].update(definition, latest)
            after = self.adapters[definition.ecosystem].installed_version(definition)
            if after is None or _parse_version(after) < _parse_version(latest):
                raise SDKManagerError(
                    "The package manager finished but the target version is not installed.",
                    code="sdk_update_not_applied",
                )
            self._state[sdk_id].update(
                latest_version=latest,
                checked_at=_timestamp(),
                error=None,
            )
            snapshot = self._snapshot(definition)
            snapshot["updated_from"] = before
            snapshot["updated_to"] = after
            return snapshot

    def set_default(self, provider_id: str) -> dict[str, Any]:
        definition = self._get_provider(provider_id)
        snapshot = self._snapshot(definition)
        if not snapshot["selectable"]:
            raise SDKManagerError(
                "The selected Agent Runtime has not passed all availability gates.",
                code="runtime_not_selectable",
                detail={"provider_id": provider_id, "blockers": snapshot["blockers"]},
            )
        with self._config_lock:
            self._runtime_config["default_provider_id"] = provider_id
            self._persist_runtime_config()
        return self._snapshot(definition)

    def runtime_snapshot(self, provider_id: str | None = None) -> dict[str, Any]:
        selected = provider_id or self.default_provider_id
        if not selected:
            raise SDKManagerError(
                "No default Agent Runtime is configured.",
                code="runtime_default_not_configured",
            )
        definition = self._get_provider(selected)
        snapshot = self._snapshot(definition)
        if not snapshot["selectable"]:
            raise SDKManagerError(
                "The selected Agent Runtime is not ready for new tasks.",
                code="runtime_not_selectable",
                detail={"provider_id": selected, "blockers": snapshot["blockers"]},
            )
        runtime_state = self._provider_runtime_state(definition)
        check = self._current_model_check(definition)
        return {
            "provider_id": definition.provider_id,
            "sdk_id": definition.sdk_id,
            "version": snapshot["current_version"],
            "cli_version": snapshot["cli_version"],
            "model": runtime_state.get("default_model_id"),
            "model_source": runtime_state.get("model_source"),
            "model_catalog_digest": snapshot.get("model_catalog_digest"),
            "model_check_digest": check.get("check_digest") if check else None,
            "runtime_configuration_revision": self.configuration_revision,
            "configuration_digest": _definition_digest(definition),
            "capabilities": list(definition.capabilities),
            "integration_runtime": _copy_json(definition.integration_runtime),
            "selected_at": _timestamp(),
        }

    async def _check_unlocked(self, definition: SDKDefinition) -> None:
        state = self._state[definition.sdk_id]
        try:
            latest = await self.adapters[definition.ecosystem].latest_version(definition)
        except SDKManagerError as exc:
            state.update(
                latest_version=None,
                checked_at=_timestamp(),
                error={"code": exc.code, "message": str(exc)},
            )
        else:
            state.update(latest_version=latest, checked_at=_timestamp(), error=None)

        if definition.authentication == "not_configured" or not definition.provider_implemented:
            self._update_authentication_state(
                definition,
                authenticated=None,
                status="not_configured",
                error=None,
            )
            return
        probe = self.runtime_probes.get(definition.provider_id)
        if probe is None:
            self._update_authentication_state(
                definition,
                authenticated=False,
                status="failed",
                error={
                    "code": "runtime_probe_unavailable",
                    "message": "No runtime authentication probe is registered.",
                },
            )
            return
        try:
            result = await probe.check_authentication(definition)
        except Exception as exc:
            self._update_authentication_state(
                definition,
                authenticated=False,
                status="failed",
                error={
                    "code": str(getattr(exc, "code", "runtime_authentication_check_failed")),
                    "message": str(exc) or type(exc).__name__,
                },
            )
            return
        authenticated = result.get("authenticated")
        self._update_authentication_state(
            definition,
            authenticated=authenticated if isinstance(authenticated, bool) else None,
            status=str(result.get("status") or "checked"),
            error=result.get("error"),
        )

    def _update_authentication_state(
        self,
        definition: SDKDefinition,
        *,
        authenticated: bool | None,
        status: str,
        error: Any,
    ) -> None:
        state = self._state[definition.sdk_id]
        previous = (state.get("authenticated"), state.get("authentication_status"))
        current = (authenticated, status)
        state.update(
            authenticated=authenticated,
            authentication_status=status,
            authentication_error=error,
        )
        if previous == current:
            return
        runtime_state = self._provider_runtime_state(definition)
        runtime_state["authentication_revision"] = (
            int(runtime_state.get("authentication_revision") or 0) + 1
        )
        prefix = f"{definition.provider_id}:"
        checks = self._runtime_config.setdefault("model_checks", {})
        for key in [value for value in checks if str(value).startswith(prefix)]:
            checks.pop(key, None)
        self._persist_runtime_config()

    def _snapshot(self, definition: SDKDefinition) -> dict[str, Any]:
        adapter = self.adapters[definition.ecosystem]
        installed = adapter.installed_version(definition)
        state = self._state[definition.sdk_id]
        latest = state["latest_version"]
        update_available = bool(
            installed and latest and _parse_version(latest) > _parse_version(installed)
        )
        platform_key = _platform_key()
        platform_supported = platform_key in definition.platforms
        base_blockers = list(definition.blockers)
        if installed is None:
            base_blockers.append("sdk_not_installed")
        if not platform_supported:
            base_blockers.append("platform_not_supported")
        if not definition.provider_implemented:
            base_blockers.append("provider_not_implemented")
        if not definition.live_validated:
            base_blockers.append("free_query_live_acceptance_required")
        if state["authenticated"] is False:
            base_blockers.append("authentication_unavailable")
        elif state["authenticated"] is None and definition.declared_selectable:
            base_blockers.append("authentication_not_checked")
        base_blockers = list(dict.fromkeys(base_blockers))
        runtime_state = self._provider_runtime_state(definition)
        enabled = bool(runtime_state.get("enabled"))
        model_id = runtime_state.get("default_model_id")
        catalog_status = self._catalog_status(definition)
        model_status = "not_configured"
        model_ready = False
        if model_id:
            if catalog_status == "not_loaded":
                model_status = "catalog_required"
            elif catalog_status == "stale":
                model_status = "catalog_stale"
            elif catalog_status == "incomplete":
                model_status = "catalog_incomplete"
            elif not self._has_catalog_model(definition, str(model_id)):
                model_status = "retired"
            else:
                check = self._current_model_check(definition)
                if check and check.get("compatible") is True:
                    model_status = "compatible"
                    model_ready = True
                elif check:
                    model_status = str(check.get("status") or "incompatible")
                else:
                    model_status = "check_required"
        blockers = list(base_blockers)
        if definition.declared_selectable:
            if not enabled:
                blockers.append("runtime_disabled")
            if catalog_status == "unavailable":
                blockers.append("runtime_model_catalog_unavailable")
            elif model_status == "not_configured":
                blockers.append("runtime_model_not_configured")
            elif model_status == "catalog_required":
                blockers.append("runtime_model_catalog_required")
            elif model_status == "catalog_stale":
                blockers.append("runtime_model_catalog_stale")
            elif model_status == "catalog_incomplete":
                blockers.append("runtime_model_catalog_incomplete")
            elif model_status == "retired":
                blockers.append("runtime_model_retired")
            elif not model_ready:
                blockers.append("runtime_model_check_required")
        blockers = list(dict.fromkeys(blockers))
        base_ready = not base_blockers
        can_enable = bool(definition.declared_selectable and base_ready and model_ready)
        selectable = bool(
            definition.declared_selectable
            and enabled
            and base_ready
            and model_ready
        )
        probe = self.runtime_probes.get(definition.provider_id)
        return {
            "provider_id": definition.provider_id,
            "sdk_id": definition.sdk_id,
            "name": definition.name,
            "description": definition.description,
            "ecosystem": definition.ecosystem,
            "package_name": definition.package_name,
            "module_name": definition.module_name,
            "installed": installed is not None,
            "current_version": installed,
            "cli_version": self._client_version(definition),
            "version": installed,
            "latest_version": latest,
            "update_available": update_available,
            "update_enabled": definition.update_enabled,
            "restart_required": definition.restart_required,
            "checked_at": state["checked_at"],
            "check_status": (
                "failed"
                if state["error"]
                else ("checked" if state["checked_at"] else "not_checked")
            ),
            "error": state["error"],
            "authenticated": state["authenticated"],
            "authentication_status": state["authentication_status"],
            "authentication_error": state["authentication_error"],
            "platform": platform_key,
            "platform_supported": platform_supported,
            "platforms": list(definition.platforms),
            "capabilities": list(definition.capabilities),
            "integration_runtime": _copy_json(definition.integration_runtime),
            "availability": definition.availability,
            "enabled": enabled,
            "can_enable": can_enable,
            "selectable": selectable,
            "selected": definition.provider_id == self.default_provider_id,
            "default_model_id": model_id,
            "model_source": runtime_state.get("model_source"),
            "model_status": model_status,
            "model_catalog_status": catalog_status,
            "model_catalog_complete": bool(self._catalog(definition).get("model_catalog_complete")),
            "model_catalog_digest": self._catalog(definition).get("catalog_digest"),
            "model_count": len(self._catalog(definition).get("models", [])),
            "model_discovery_supported": callable(getattr(probe, "list_models", None)),
            "model_check_supported": callable(getattr(probe, "check_model", None)),
            "runtime_configuration_revision": self.configuration_revision,
            "blockers": blockers,
            "provider_implemented": definition.provider_implemented,
            "live_validated": definition.live_validated,
            "configuration_digest": _definition_digest(definition),
        }

    def _get_sdk(self, sdk_id: str) -> SDKDefinition:
        for definition in self.definitions:
            if definition.sdk_id == sdk_id:
                return definition
        raise SDKManagerError("Unknown SDK.", code="sdk_not_found")

    def _get_provider(self, provider_id: str) -> SDKDefinition:
        for definition in self.definitions:
            if definition.provider_id == provider_id:
                return definition
        raise SDKManagerError("Unknown Agent Runtime.", code="runtime_not_found")

    def _load_runtime_config(self) -> dict[str, Any]:
        initial = self._initial_runtime_config()
        if not self.selection_path.is_file():
            return initial
        try:
            payload = json.loads(self.selection_path.read_text(encoding="utf-8"))
            if payload.get("schema_version") == 1:
                selected = str(payload.get("provider_id") or "")
                self._get_provider(selected)
                initial["default_provider_id"] = selected
                initial["providers"][selected]["enabled"] = True
                return initial
            if payload.get("schema_version") != 2:
                return initial
            selected = payload.get("default_provider_id")
            if selected is not None:
                self._get_provider(str(selected))
            providers = payload.get("providers")
            catalogs = payload.get("model_catalogs")
            checks = payload.get("model_checks")
            if not isinstance(providers, dict) or not isinstance(catalogs, dict) or not isinstance(checks, dict):
                return initial
            initial.update(
                revision=max(0, int(payload.get("revision") or 0)),
                default_provider_id=str(selected) if selected else None,
                model_catalogs=catalogs,
                model_checks=checks,
            )
            for definition in self.definitions:
                value = providers.get(definition.provider_id)
                if isinstance(value, dict):
                    initial["providers"][definition.provider_id].update(
                        enabled=value.get("enabled") is True,
                        default_model_id=(str(value.get("default_model_id")) if value.get("default_model_id") else None),
                        model_source=(str(value.get("model_source")) if value.get("model_source") else None),
                        legacy_model_error=(str(value.get("legacy_model_error")) if value.get("legacy_model_error") else None),
                        authentication_revision=max(
                            0, int(value.get("authentication_revision") or 0)
                        ),
                        updated_at=value.get("updated_at"),
                    )
            return initial
        except (OSError, ValueError, TypeError, json.JSONDecodeError, SDKManagerError):
            return initial

    def _initial_runtime_config(self) -> dict[str, Any]:
        providers: dict[str, dict[str, Any]] = {}
        for definition in self.definitions:
            is_default = definition.provider_id == self.registry_default_provider_id
            providers[definition.provider_id] = {
                "enabled": is_default,
                "default_model_id": self.legacy_model if is_default else None,
                "model_source": "environment_migration" if is_default and self.legacy_model else None,
                "legacy_model_error": None,
                "authentication_revision": 0,
                "updated_at": None,
            }
        return {
            "schema_version": 2,
            "revision": 0,
            "default_provider_id": self.registry_default_provider_id,
            "providers": providers,
            "model_catalogs": {},
            "model_checks": {},
            "updated_at": None,
        }

    def _persist_runtime_config(self) -> None:
        with self._config_lock:
            self._runtime_config["schema_version"] = 2
            self._runtime_config["revision"] = self.configuration_revision + 1
            self._runtime_config["updated_at"] = _timestamp()
            self.selection_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.selection_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self._runtime_config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.selection_path)

    def _provider_runtime_state(self, definition: SDKDefinition) -> dict[str, Any]:
        providers = self._runtime_config.setdefault("providers", {})
        return providers.setdefault(
            definition.provider_id,
            {
                "enabled": False,
                "default_model_id": None,
                "model_source": None,
                "legacy_model_error": None,
                "authentication_revision": 0,
                "updated_at": None,
            },
        )

    def _catalog(self, definition: SDKDefinition) -> dict[str, Any]:
        value = self._runtime_config.setdefault("model_catalogs", {}).get(definition.provider_id)
        return value if isinstance(value, dict) else {}

    def _catalog_status(self, definition: SDKDefinition) -> str:
        probe = self.runtime_probes.get(definition.provider_id)
        if not callable(getattr(probe, "list_models", None)):
            return "unavailable"
        catalog = self._catalog(definition)
        if not catalog:
            return "not_loaded"
        if catalog.get("error"):
            return "stale" if catalog.get("models") else "failed"
        installed = self.adapters[definition.ecosystem].installed_version(definition)
        if catalog.get("sdk_version") != installed or catalog.get("cli_version") != self._client_version(definition):
            return "stale"
        if not catalog.get("model_catalog_complete"):
            return "incomplete"
        return "ready"

    def _catalog_model(self, definition: SDKDefinition, model_id: str) -> dict[str, Any]:
        if self._catalog_status(definition) != "ready":
            raise SDKManagerError(
                "A complete current model catalog is required.",
                code="runtime_model_catalog_required",
            )
        for item in self._catalog(definition).get("models", []):
            if item.get("model_id") == model_id:
                return item
        raise SDKManagerError(
            "The selected model is not in the current Runtime catalog.",
            code="runtime_model_not_registered",
        )

    def _has_catalog_model(self, definition: SDKDefinition, model_id: str) -> bool:
        return any(
            item.get("model_id") == model_id
            for item in self._catalog(definition).get("models", [])
            if isinstance(item, dict)
        )

    def _model_check_key(self, provider_id: str, model_id: str) -> str:
        return f"{provider_id}:{model_id}"

    def _current_model_check(
        self, definition: SDKDefinition, model_id: str | None = None
    ) -> dict[str, Any] | None:
        selected = model_id or self._provider_runtime_state(definition).get("default_model_id")
        if not selected:
            return None
        value = self._runtime_config.setdefault("model_checks", {}).get(
            self._model_check_key(definition.provider_id, str(selected))
        )
        if not isinstance(value, dict):
            return None
        catalog = self._catalog(definition)
        expected = {
            "sdk_version": self.adapters[definition.ecosystem].installed_version(definition),
            "cli_version": self._client_version(definition),
            "platform": _platform_key(),
            "provider_configuration_digest": _definition_digest(definition),
            "catalog_digest": catalog.get("catalog_digest"),
            "authentication_status": self._state[definition.sdk_id].get(
                "authentication_status"
            ),
            "authentication_revision": int(
                self._provider_runtime_state(definition).get("authentication_revision")
                or 0
            ),
        }
        if any(value.get(key) != expected_value for key, expected_value in expected.items()):
            return None
        return value

    def _decorate_model(self, definition: SDKDefinition, item: dict[str, Any]) -> dict[str, Any]:
        model_id = str(item.get("model_id") or "")
        check = self._current_model_check(definition, model_id)
        check_status = (
            str(check.get("status") or "incompatible")
            if check
            else "check_required"
        )
        return {
            **item,
            "catalog_sdk_version": self._catalog(definition).get("sdk_version"),
            "catalog_cli_version": self._catalog(definition).get("cli_version"),
            "retired": False,
            "selected": self._provider_runtime_state(definition).get("default_model_id") == model_id,
            "check_status": check_status,
            "selectable": bool(
                self._catalog_status(definition) == "ready"
                and check
                and check.get("compatible") is True
            ),
            "check": check,
        }

    def _client_version(self, definition: SDKDefinition) -> str | None:
        probe = self.runtime_probes.get(definition.provider_id)
        method = getattr(probe, "client_version", None)
        if not callable(method):
            return None
        try:
            value = method(definition)
        except Exception:
            return None
        return str(value) if value else None


def _load_definitions(path: Path) -> tuple[str, list[SDKDefinition]]:
    if not path.exists():
        return "codex", []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SDKManagerError("Cannot load the SDK registry.", code="sdk_definition_invalid") from exc
    if payload.get("schema_version") == 1:
        return _load_legacy_definitions(payload)
    schema_version = payload.get("schemaVersion")
    if schema_version not in {2, 3} or not isinstance(payload.get("providers"), list):
        raise SDKManagerError("The SDK registry schema is invalid.", code="sdk_definition_invalid")
    default_provider_id = str(payload.get("defaultProviderId") or "")
    definitions: list[SDKDefinition] = []
    seen_sdks: set[str] = set()
    seen_providers: set[str] = set()
    for raw in payload["providers"]:
        if not isinstance(raw, dict):
            raise SDKManagerError("An SDK definition is invalid.", code="sdk_definition_invalid")
        provider_id = str(raw.get("providerId") or "")
        sdk_id = str(raw.get("sdkId") or "")
        ecosystem = str(raw.get("ecosystem") or "")
        package_name = str(raw.get("packageName") or "")
        module_name = str(raw.get("moduleName") or "") or None
        availability = str(raw.get("availability") or "")
        if (
            not _PROVIDER_ID.fullmatch(provider_id)
            or provider_id in seen_providers
            or not _SDK_ID.fullmatch(sdk_id)
            or sdk_id in seen_sdks
        ):
            raise SDKManagerError(
                "A provider or SDK id is invalid or duplicated.",
                code="sdk_definition_invalid",
            )
        if ecosystem not in {"python", "npm"}:
            raise SDKManagerError("An SDK ecosystem is invalid.", code="sdk_definition_invalid")
        if ecosystem == "python":
            _validate_python_package(package_name)
            if module_name and not _PYTHON_MODULE.fullmatch(module_name):
                raise SDKManagerError("Invalid Python module name.", code="sdk_definition_invalid")
        else:
            _validate_npm_package(package_name)
        if availability not in _AVAILABILITY:
            raise SDKManagerError("SDK availability is invalid.", code="sdk_definition_invalid")
        name = raw.get("name")
        description = raw.get("description")
        platforms = raw.get("platforms")
        capabilities = raw.get("capabilities")
        blockers = raw.get("blockers", [])
        if not _localized(name) or not _localized(description):
            raise SDKManagerError("SDK text must be bilingual.", code="sdk_definition_invalid")
        if not _string_list(platforms) or not _string_list(capabilities) or not _string_list(blockers, allow_empty=True):
            raise SDKManagerError("SDK lists are invalid.", code="sdk_definition_invalid")
        seen_sdks.add(sdk_id)
        seen_providers.add(provider_id)
        definitions.append(
            SDKDefinition(
                provider_id=provider_id,
                sdk_id=sdk_id,
                name=dict(name),
                description=dict(description),
                ecosystem=ecosystem,
                package_name=package_name,
                module_name=module_name,
                availability=availability,
                declared_selectable=raw.get("selectable") is True,
                platforms=tuple(str(item) for item in platforms),
                capabilities=tuple(str(item) for item in capabilities),
                authentication=str(raw.get("authentication") or "not_configured"),
                provider_implemented=raw.get("providerImplemented") is True,
                live_validated=raw.get("liveValidated") is True,
                blockers=tuple(str(item) for item in blockers),
                project_root=str(raw.get("projectRoot")) if raw.get("projectRoot") else None,
                update_enabled=raw.get("updateEnabled") is True,
                restart_required=raw.get("restartRequired") is True,
                integration_runtime=_load_integration_runtime(
                    raw.get("integrationRuntime"),
                    required=schema_version == 3,
                ),
            )
        )
    if default_provider_id not in seen_providers:
        raise SDKManagerError(
            "The default provider is not registered.",
            code="sdk_definition_invalid",
        )
    return default_provider_id, definitions


def _load_legacy_definitions(payload: dict[str, Any]) -> tuple[str, list[SDKDefinition]]:
    raw_sdks = payload.get("sdks")
    if not isinstance(raw_sdks, list) or not raw_sdks:
        raise SDKManagerError("The SDK registry schema is invalid.", code="sdk_definition_invalid")
    definitions: list[SDKDefinition] = []
    for index, raw in enumerate(raw_sdks):
        if not isinstance(raw, dict):
            raise SDKManagerError("An SDK definition is invalid.", code="sdk_definition_invalid")
        sdk_id = str(raw.get("sdk_id") or "")
        ecosystem = str(raw.get("ecosystem") or "")
        package_name = str(raw.get("package_name") or "")
        if not _SDK_ID.fullmatch(sdk_id) or ecosystem not in {"python", "npm"}:
            raise SDKManagerError("An SDK definition is invalid.", code="sdk_definition_invalid")
        provider_id = "codex" if index == 0 else sdk_id.removesuffix("-sdk")
        definitions.append(
            SDKDefinition(
                provider_id=provider_id,
                sdk_id=sdk_id,
                name=dict(raw.get("name") or {}),
                description=dict(raw.get("description") or {}),
                ecosystem=ecosystem,
                package_name=package_name,
                module_name="openai_codex" if sdk_id == "codex-python-sdk" else None,
                availability="active" if index == 0 else "planned",
                declared_selectable=index == 0,
                platforms=("windows",),
                capabilities=("planning", "resume", "structured_output"),
                authentication="existing_login",
                provider_implemented=index == 0,
                live_validated=index == 0,
                blockers=(),
                project_root=str(raw.get("project_root")) if raw.get("project_root") else None,
                update_enabled=raw.get("update_enabled") is True,
                restart_required=raw.get("restart_required") is True,
                integration_runtime=_disabled_integration_runtime(),
            )
        )
    return definitions[0].provider_id, definitions


def _localized(value: Any) -> bool:
    return isinstance(value, dict) and all(
        str(value.get(locale) or "").strip() for locale in ("zh", "en")
    )


def _load_integration_runtime(value: Any, *, required: bool) -> dict[str, Any]:
    if value is None and not required:
        return _disabled_integration_runtime()
    if not isinstance(value, dict):
        raise SDKManagerError(
            "Integration Runtime configuration is invalid.",
            code="sdk_definition_invalid",
        )
    adapter_id = value.get("adapterId")
    if adapter_id is not None and not _PROVIDER_ID.fullmatch(str(adapter_id)):
        raise SDKManagerError(
            "Integration Runtime adapter id is invalid.",
            code="sdk_definition_invalid",
        )
    resource_kinds = value.get("resourceKinds")
    features = value.get("features")
    blockers = value.get("blockers", [])
    readiness = str(value.get("readiness") or "")
    if (
        not _string_list(resource_kinds, allow_empty=True)
        or not set(resource_kinds).issubset(_INTEGRATION_RESOURCE_KINDS)
        or not isinstance(features, dict)
        or set(features) != _INTEGRATION_FEATURES
        or any(not isinstance(features[name], bool) for name in _INTEGRATION_FEATURES)
        or str(value.get("credentialOwner") or "") != "runtime"
        or readiness not in _INTEGRATION_READINESS
        or not _string_list(blockers, allow_empty=True)
    ):
        raise SDKManagerError(
            "Integration Runtime capability declaration is invalid.",
            code="sdk_definition_invalid",
        )
    return {
        "adapter_id": str(adapter_id) if adapter_id is not None else None,
        "resource_kinds": [str(item) for item in resource_kinds],
        "features": {
            "catalog": features["catalog"],
            "configured_discovery": features["configuredDiscovery"],
            "status": features["status"],
            "authentication": features["authentication"],
            "configuration": features["configuration"],
            "direct_tool_call": features["directToolCall"],
        },
        "credential_owner": "runtime",
        "readiness": readiness,
        "blockers": [str(item) for item in blockers],
    }


def _disabled_integration_runtime() -> dict[str, Any]:
    return {
        "adapter_id": None,
        "resource_kinds": [],
        "features": {
            "catalog": False,
            "configured_discovery": False,
            "status": False,
            "authentication": False,
            "configuration": False,
            "direct_tool_call": False,
        },
        "credential_owner": "runtime",
        "readiness": "unavailable",
        "blockers": ["integration_adapter_not_configured"],
    }


def _copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _string_list(value: Any, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(isinstance(item, str) and item.strip() for item in value)
        and len(value) == len(set(value))
    )


def _validate_python_package(value: str) -> str:
    if not _PYTHON_PACKAGE.fullmatch(value):
        raise SDKManagerError("Invalid Python package name.", code="sdk_definition_invalid")
    return value


def _validate_npm_package(value: str) -> str:
    if not _NPM_PACKAGE.fullmatch(value):
        raise SDKManagerError("Invalid npm package name.", code="sdk_definition_invalid")
    return value


def _parse_version(value: str) -> Version:
    try:
        return Version(value)
    except InvalidVersion as exc:
        raise SDKManagerError("Invalid SDK version.", code="sdk_version_invalid") from exc


def _run_package_manager(command: list[str], cwd: Path | None, timeout_seconds: int) -> None:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SDKManagerError("The SDK update timed out.", code="sdk_update_timeout") from exc
    except OSError as exc:
        raise SDKManagerError(
            "The package manager could not start.",
            code="sdk_package_manager_failed",
        ) from exc
    if completed.returncode != 0:
        raise SDKManagerError(
            f"The package manager exited with code {completed.returncode}.",
            code="sdk_package_manager_failed",
        )


def _platform_key() -> str:
    if sys.platform == "win32":
        return "windows"
    architecture = platform.machine().lower()
    normalized_arch = "arm64" if architecture in {"arm64", "aarch64"} else "x64"
    if sys.platform == "darwin":
        return f"darwin-{normalized_arch}"
    if sys.platform.startswith("linux"):
        return f"linux-{normalized_arch}"
    return f"{sys.platform}-{normalized_arch}"


def _definition_digest(definition: SDKDefinition) -> str:
    payload = json.dumps(asdict(definition), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_model_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not _MODEL_ID.fullmatch(normalized):
        raise SDKManagerError("Invalid Runtime model id.", code="runtime_model_id_invalid")
    return normalized


def _stable_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_model(raw: Any) -> dict[str, Any] | None:
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="json")
    if not isinstance(raw, dict) or raw.get("hidden") is True:
        return None
    model_id = _validate_model_id(str(raw.get("id") or raw.get("model") or ""))
    efforts: list[str] = []
    for value in raw.get("supported_reasoning_efforts") or []:
        if isinstance(value, dict):
            effort = value.get("reasoning_effort") or value.get("reasoningEffort")
        else:
            effort = value
        if effort and str(effort) not in efforts:
            efforts.append(str(effort))
    tiers: list[str] = []
    for value in raw.get("service_tiers") or []:
        tier = value.get("id") if isinstance(value, dict) else value
        if tier and str(tier) not in tiers:
            tiers.append(str(tier))
    upgrade_info = raw.get("upgrade_info") or raw.get("upgradeInfo")
    if not isinstance(upgrade_info, dict):
        upgrade_info = {}
    upgrade_target = raw.get("upgrade") or upgrade_info.get("model")
    upgrade_message = (
        upgrade_info.get("migration_markdown")
        or upgrade_info.get("migrationMarkdown")
        or upgrade_info.get("upgrade_copy")
        or upgrade_info.get("upgradeCopy")
    )
    return {
        "model_id": model_id,
        "display_name": str(raw.get("display_name") or raw.get("displayName") or model_id),
        "is_sdk_default": raw.get("is_default") is True or raw.get("isDefault") is True,
        "input_modalities": [str(value) for value in raw.get("input_modalities") or raw.get("inputModalities") or []],
        "default_reasoning_effort": (
            str(raw.get("default_reasoning_effort") or raw.get("defaultReasoningEffort"))
            if raw.get("default_reasoning_effort") or raw.get("defaultReasoningEffort")
            else None
        ),
        "supported_reasoning_efforts": efforts,
        "service_tiers": tiers,
        "upgrade_target": str(upgrade_target) if upgrade_target else None,
        "upgrade_message": str(upgrade_message).strip() if upgrade_message else None,
    }


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()
