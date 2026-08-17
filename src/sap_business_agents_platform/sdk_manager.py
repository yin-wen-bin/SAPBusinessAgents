from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from packaging.version import InvalidVersion, Version


_SDK_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_PYTHON_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_NPM_PACKAGE = re.compile(r"^(?:@[a-z0-9._-]+/)?[a-z0-9._-]+$")


class SDKManagerError(RuntimeError):
    def __init__(self, message: str, *, code: str = "sdk_manager_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SDKDefinition:
    sdk_id: str
    name: dict[str, str]
    description: dict[str, str]
    ecosystem: str
    package_name: str
    project_root: str | None
    update_enabled: bool
    restart_required: bool


class SDKAdapter(Protocol):
    def installed_version(self, definition: SDKDefinition) -> str | None: ...

    async def latest_version(self, definition: SDKDefinition) -> str: ...

    async def update(self, definition: SDKDefinition, target_version: str) -> None: ...


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
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--upgrade",
            f"{package_name}=={target_version}",
        ]
        await asyncio.to_thread(
            _run_package_manager,
            command,
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
    ) -> None:
        self.definitions_path = definitions_path.resolve()
        self.repository_root = repository_root.resolve()
        self.definitions = _load_definitions(self.definitions_path)
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
        self._state: dict[str, dict[str, Any]] = {
            item.sdk_id: {"latest_version": None, "checked_at": None, "error": None}
            for item in self.definitions
        }
        self._locks = {item.sdk_id: asyncio.Lock() for item in self.definitions}

    def list(self) -> list[dict[str, Any]]:
        return [self._snapshot(item) for item in self.definitions]

    async def check_all(self) -> list[dict[str, Any]]:
        await asyncio.gather(*(self.check(item.sdk_id) for item in self.definitions))
        return self.list()

    async def check(self, sdk_id: str) -> dict[str, Any]:
        definition = self._get(sdk_id)
        async with self._locks[sdk_id]:
            await self._check_unlocked(definition)
            return self._snapshot(definition)

    async def update(self, sdk_id: str) -> dict[str, Any]:
        definition = self._get(sdk_id)
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
            self._state[sdk_id] = {
                "latest_version": latest,
                "checked_at": _timestamp(),
                "error": None,
            }
            snapshot = self._snapshot(definition)
            snapshot["updated_from"] = before
            snapshot["updated_to"] = after
            return snapshot

    async def _check_unlocked(self, definition: SDKDefinition) -> None:
        try:
            latest = await self.adapters[definition.ecosystem].latest_version(definition)
        except SDKManagerError as exc:
            self._state[definition.sdk_id] = {
                "latest_version": None,
                "checked_at": _timestamp(),
                "error": {"code": exc.code, "message": str(exc)},
            }
            return
        self._state[definition.sdk_id] = {
            "latest_version": latest,
            "checked_at": _timestamp(),
            "error": None,
        }

    def _snapshot(self, definition: SDKDefinition) -> dict[str, Any]:
        adapter = self.adapters[definition.ecosystem]
        installed = adapter.installed_version(definition)
        state = self._state[definition.sdk_id]
        latest = state["latest_version"]
        update_available = bool(
            installed
            and latest
            and _parse_version(latest) > _parse_version(installed)
        )
        return {
            "sdk_id": definition.sdk_id,
            "name": definition.name,
            "description": definition.description,
            "ecosystem": definition.ecosystem,
            "package_name": definition.package_name,
            "installed": installed is not None,
            "current_version": installed,
            "latest_version": latest,
            "update_available": update_available,
            "update_enabled": definition.update_enabled,
            "restart_required": definition.restart_required,
            "checked_at": state["checked_at"],
            "check_status": "failed" if state["error"] else ("checked" if state["checked_at"] else "not_checked"),
            "error": state["error"],
        }

    def _get(self, sdk_id: str) -> SDKDefinition:
        for definition in self.definitions:
            if definition.sdk_id == sdk_id:
                return definition
        raise SDKManagerError("Unknown SDK.", code="sdk_not_found")


def _load_definitions(path: Path) -> list[SDKDefinition]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SDKManagerError("Cannot load the SDK registry.", code="sdk_definition_invalid") from exc
    if payload.get("schema_version") != 1 or not isinstance(payload.get("sdks"), list):
        raise SDKManagerError("The SDK registry schema is invalid.", code="sdk_definition_invalid")
    definitions: list[SDKDefinition] = []
    seen: set[str] = set()
    for raw in payload["sdks"]:
        if not isinstance(raw, dict):
            raise SDKManagerError("An SDK definition is invalid.", code="sdk_definition_invalid")
        sdk_id = str(raw.get("sdk_id") or "")
        ecosystem = str(raw.get("ecosystem") or "")
        package_name = str(raw.get("package_name") or "")
        if not _SDK_ID.fullmatch(sdk_id) or sdk_id in seen:
            raise SDKManagerError("An SDK id is invalid or duplicated.", code="sdk_definition_invalid")
        if ecosystem not in {"python", "npm"}:
            raise SDKManagerError("An SDK ecosystem is invalid.", code="sdk_definition_invalid")
        if ecosystem == "python":
            _validate_python_package(package_name)
        else:
            _validate_npm_package(package_name)
        name = raw.get("name")
        description = raw.get("description")
        if not _localized(name) or not _localized(description):
            raise SDKManagerError("SDK text must be bilingual.", code="sdk_definition_invalid")
        seen.add(sdk_id)
        definitions.append(
            SDKDefinition(
                sdk_id=sdk_id,
                name=dict(name),
                description=dict(description),
                ecosystem=ecosystem,
                package_name=package_name,
                project_root=str(raw.get("project_root")) if raw.get("project_root") else None,
                update_enabled=raw.get("update_enabled") is True,
                restart_required=raw.get("restart_required") is True,
            )
        )
    return definitions


def _localized(value: Any) -> bool:
    return isinstance(value, dict) and all(str(value.get(locale) or "").strip() for locale in ("zh", "en"))


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
        raise SDKManagerError("The package manager could not start.", code="sdk_package_manager_failed") from exc
    if completed.returncode != 0:
        raise SDKManagerError(
            f"The package manager exited with code {completed.returncode}.",
            code="sdk_package_manager_failed",
        )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()
