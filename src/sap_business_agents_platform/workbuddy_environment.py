"""SDK-free WorkBuddy release registry. No platform interpreter or SDK imports."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from .sdk_manager import SDKManagerError

PROTOCOL = "sapba.workbuddy.worker/1"
from .runtime_policy import OPERATIONS
CONTRACT = "1.0"


class WorkBuddyError(SDKManagerError):
    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code, code=code)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()


def file_hash(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    import uuid
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


class WorkBuddyEnvironment:
    def __init__(self, repository_root: Path):
        self.root = repository_root.resolve() / ".local-data/runtimes/workbuddy"
        self.state_path = self.root / "state.json"
        self.lock = threading.RLock()
        self._maintenance_held = False
        self._verified: dict[str, tuple[Any, ...]] = {}

    def state(self) -> dict:
        if not self.state_path.exists():
            return {"revision": 0, "enabled": False, "models": {}, "capabilities": {}}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise WorkBuddyError("workbuddy_state_invalid") from exc
        if not isinstance(value, dict):
            raise WorkBuddyError("workbuddy_state_invalid")
        if (not isinstance(value.get("revision", 0), int) or isinstance(value.get("revision", 0), bool)
                or value.get("revision", 0) < 0 or not isinstance(value.get("enabled", False), bool)
                or value.get("authenticated") is not None and not isinstance(value["authenticated"], bool)):
            raise WorkBuddyError("workbuddy_state_invalid")
        for name in ("models", "capabilities"):
            entries = value.get(name, {})
            if not isinstance(entries, dict) or any(not isinstance(item, dict) for item in entries.values()):
                raise WorkBuddyError("workbuddy_state_invalid")
        for name in ("installation", "model_discovery"):
            if name in value and not isinstance(value[name], dict):
                raise WorkBuddyError("workbuddy_state_invalid")
        candidates = value.get("model_discovery", {}).get("models", [])
        if not isinstance(candidates, list) or any(not isinstance(item, str) for item in candidates):
            raise WorkBuddyError("workbuddy_state_invalid")
        return value

    def mutate(self, change: Any) -> dict:
        with self.maintenance():
            value = self.state()
            change(value)
            value["revision"] = int(value.get("revision", 0)) + 1
            atomic_json(self.state_path, value)
            return value

    @contextmanager
    def maintenance(self):
        """Shared installer/API lock; busy is explicit, never freezes the API."""
        with self.lock:
            if self._maintenance_held:
                yield  # Same thread/object may mutate while validating a binding.
                return
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / "maintenance.lock").open("a+b") as stream:
                try:
                    stream.seek(0)
                    if not stream.read(1):
                        stream.write(b"0")
                        stream.flush()
                    stream.seek(0)
                except OSError as exc:
                    raise WorkBuddyError("workbuddy_maintenance_busy") from exc
                if os.name == "nt":
                    import msvcrt
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError as exc:
                        raise WorkBuddyError("workbuddy_maintenance_busy") from exc
                else:
                    import fcntl
                    try:
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as exc:
                        raise WorkBuddyError("workbuddy_maintenance_busy") from exc
                self._maintenance_held = True
                try:
                    yield
                finally:
                    self._maintenance_held = False
                    stream.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream, fcntl.LOCK_UN)

    def release(self, environment_digest: str | None = None) -> dict:
        key = environment_digest or self.state().get("environment_digest")
        if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise WorkBuddyError("workbuddy_environment_not_installed")
        directory = self.root / "releases" / key
        try:
            value = json.loads((directory / "environment.json").read_text(encoding="utf-8"))
        except PermissionError as exc:
            raise WorkBuddyError("workbuddy_environment_access_denied") from exc
        except (OSError, ValueError) as exc:
            raise WorkBuddyError("workbuddy_environment_unavailable") from exc
        if not isinstance(value, dict):
            raise WorkBuddyError("workbuddy_environment_invalid")
        files = value.get("files")
        if (not isinstance(files, dict) or not files
                or any(not isinstance(name, str) or not isinstance(expected, str)
                       or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected)
                       for name, expected in files.items())
                or any(not isinstance(value.get(name), str) or value[name] not in files
                       for name in ("python", "cli", "worker"))):
            raise WorkBuddyError("workbuddy_environment_invalid")
        if value.get("environment_digest") != key:
            raise WorkBuddyError("workbuddy_environment_digest_mismatch")
        if digest({k: v for k, v in value.items() if k != "environment_digest"}) != key:
            raise WorkBuddyError("workbuddy_environment_digest_mismatch")
        try:
            present = {path.relative_to(directory).as_posix() for path in directory.rglob("*")
                       if path.is_file() and path != directory / "environment.json"}
            if present != set(files):
                raise WorkBuddyError("workbuddy_environment_digest_mismatch")
            # Full file inventory is checked on first use and whenever stat identity changes.
            observed = []
            for name in files:
                path = (directory / name).resolve()
                if directory.resolve() not in path.parents or not path.is_file() or path.stat().st_nlink > 1:
                    raise WorkBuddyError("workbuddy_environment_unavailable")
                info = path.stat()
                observed.append((name, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            fingerprint = tuple(observed)
            # Windows can keep an identical stat tuple for a rapid same-size
            # rewrite. Never trust that cache for the executable worker entry.
            if file_hash(directory / value["worker"]) != files[value["worker"]]:
                raise WorkBuddyError("workbuddy_environment_digest_mismatch")
            if self._verified.get(key) != fingerprint:
                if any(file_hash(directory / name) != expected for name, expected in files.items()):
                    raise WorkBuddyError("workbuddy_environment_digest_mismatch")
                self._verified[key] = fingerprint
        except PermissionError as exc:
            raise WorkBuddyError("workbuddy_environment_access_denied") from exc
        except OSError as exc:
            raise WorkBuddyError("workbuddy_environment_unavailable") from exc
        return {**value, "directory": str(directory),
                "python_path": str(directory / value["python"]),
                "cli_path": str(directory / value["cli"])}

    def assert_operation(self, snapshot: dict, operation: str, *, mode: str = "bounded") -> dict:
        from .runtime_policy import ORCHESTRATION_VERSION, orchestration_digest
        release = self.release(snapshot.get("environment_digest"))
        if snapshot.get("provider_id") != "workbuddy" or snapshot.get("reasoning_effort") is not None:
            raise WorkBuddyError("workbuddy_binding_invalid")
        if mode == "restricted":
            raise WorkBuddyError("workbuddy_windows_restricted_unverified")
        capability = (snapshot.get("operation_capabilities") or {}).get(operation)
        if not isinstance(capability, dict) or capability.get("status") != "validated":
            raise WorkBuddyError("runtime_operation_unavailable", f"WorkBuddy operation not validated: {operation}")
        if (capability.get("environment_digest") != release["environment_digest"]
                or capability.get("contract_version") != CONTRACT
                or mode not in capability.get("permission_modes", [])):
            raise WorkBuddyError("workbuddy_capability_binding_mismatch")
        if (capability.get("orchestration_version") != ORCHESTRATION_VERSION
                or capability.get("orchestration_digest") != orchestration_digest(operation)):
            raise WorkBuddyError("workbuddy_orchestration_validation_required")
        return release
