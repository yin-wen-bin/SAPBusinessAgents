from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
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
_AVAILABILITY = {"active", "planned", "reserved", "reserved_blocked"}


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


class SDKAdapter(Protocol):
    def installed_version(self, definition: SDKDefinition) -> str | None: ...

    async def latest_version(self, definition: SDKDefinition) -> str: ...

    async def update(self, definition: SDKDefinition, target_version: str) -> None: ...


class RuntimeProbe(Protocol):
    async def check_authentication(self, definition: SDKDefinition) -> dict[str, Any]: ...


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
        self._default_provider_id = self._load_selected_provider()

    @property
    def default_provider_id(self) -> str:
        return self._default_provider_id

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
        self._write_selected_provider(provider_id)
        self._default_provider_id = provider_id
        return self._snapshot(definition)

    def runtime_snapshot(self, provider_id: str | None = None) -> dict[str, Any]:
        definition = self._get_provider(provider_id or self._default_provider_id)
        snapshot = self._snapshot(definition)
        return {
            "provider_id": definition.provider_id,
            "sdk_id": definition.sdk_id,
            "version": snapshot["current_version"],
            "configuration_digest": _definition_digest(definition),
            "capabilities": list(definition.capabilities),
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
            state.update(
                authenticated=None,
                authentication_status="not_configured",
                authentication_error=None,
            )
            return
        probe = self.runtime_probes.get(definition.provider_id)
        if probe is None:
            state.update(
                authenticated=False,
                authentication_status="failed",
                authentication_error={
                    "code": "runtime_probe_unavailable",
                    "message": "No runtime authentication probe is registered.",
                },
            )
            return
        try:
            result = await probe.check_authentication(definition)
        except Exception as exc:
            state.update(
                authenticated=False,
                authentication_status="failed",
                authentication_error={
                    "code": str(getattr(exc, "code", "runtime_authentication_check_failed")),
                    "message": str(exc) or type(exc).__name__,
                },
            )
            return
        authenticated = result.get("authenticated")
        state.update(
            authenticated=authenticated if isinstance(authenticated, bool) else None,
            authentication_status=str(result.get("status") or "checked"),
            authentication_error=result.get("error"),
        )

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
        blockers = list(definition.blockers)
        if installed is None:
            blockers.append("sdk_not_installed")
        if not platform_supported:
            blockers.append("platform_not_supported")
        if not definition.provider_implemented:
            blockers.append("provider_not_implemented")
        if not definition.live_validated:
            blockers.append("free_query_live_acceptance_required")
        if state["authenticated"] is False:
            blockers.append("authentication_unavailable")
        elif state["authenticated"] is None and definition.declared_selectable:
            blockers.append("authentication_not_checked")
        blockers = list(dict.fromkeys(blockers))
        selectable = bool(
            definition.declared_selectable
            and installed
            and platform_supported
            and definition.provider_implemented
            and definition.live_validated
            and state["authenticated"] is True
            and not blockers
        )
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
            "availability": definition.availability,
            "selectable": selectable,
            "selected": definition.provider_id == self._default_provider_id,
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

    def _load_selected_provider(self) -> str:
        selected = self.registry_default_provider_id
        if self.selection_path.is_file():
            try:
                payload = json.loads(self.selection_path.read_text(encoding="utf-8"))
                candidate = str(payload.get("provider_id") or "")
                self._get_provider(candidate)
                selected = candidate
            except (OSError, json.JSONDecodeError, SDKManagerError):
                selected = self.registry_default_provider_id
        return selected

    def _write_selected_provider(self, provider_id: str) -> None:
        self.selection_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "provider_id": provider_id,
            "updated_at": _timestamp(),
        }
        temporary = self.selection_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.selection_path)


def _load_definitions(path: Path) -> tuple[str, list[SDKDefinition]]:
    if not path.exists():
        return "codex", []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SDKManagerError("Cannot load the SDK registry.", code="sdk_definition_invalid") from exc
    if payload.get("schema_version") == 1:
        return _load_legacy_definitions(payload)
    if payload.get("schemaVersion") != 2 or not isinstance(payload.get("providers"), list):
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
            )
        )
    return definitions[0].provider_id, definitions


def _localized(value: Any) -> bool:
    return isinstance(value, dict) and all(
        str(value.get(locale) or "").strip() for locale in ("zh", "en")
    )


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


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()
