"""SDK-free WorkBuddy release registry. No platform interpreter or SDK imports."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import stat
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from .sdk_manager import SDKManagerError
from .workbuddy_record_io import read_record, record_lock
from .workbuddy_inspection import current as inspection, inspect

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


def atomic_json(path: Path, value: Any, *, deadline: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.name == 'jobs':
        from .workbuddy_job_index import enabled, write
        if enabled(path.parent.parent):
            write(path, value, deadline=deadline)
            return
    # Release inventories are immutable installation artifacts, not live host
    # journals. Never create an unlisted coordination file inside a release.
    if path.name == "environment.json":
        _atomic_json_owned(path, value, deadline=deadline)
        return
    with record_lock(path, deadline=deadline):
        _atomic_json_owned(path, value, deadline=deadline)


def _atomic_json_owned(path: Path, value: Any, *, deadline: float | None = None) -> None:
    import uuid
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        # Windows readers can transiently deny replacement while inspecting a
        # job. Retry only explicit sharing/lock violations, never permissions,
        # missing paths, disk-full errors or installation integrity failures.
        # This retries the exact same bytes, not an SDK/model/SAP operation.
        for attempt in range(3):
            try:
                os.replace(temporary, path)
                return
            except OSError as error:
                delay = (0.01, 0.02)[min(attempt, 1)]
                if (getattr(error, "winerror", None) not in {32, 33} or attempt == 2
                        or deadline is not None and time.monotonic() + delay >= deadline):
                    raise
                time.sleep(delay)
    finally:
        # Only the temporary file owned by this invocation. Never sweep old
        # files/evidence, and a cleanup error cannot mask the primary write error.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class WorkBuddyEnvironment:
    def __init__(self, repository_root: Path):
        self.root = repository_root.resolve() / ".local-data/runtimes/workbuddy"
        self.state_path = self.root / "state.json"
        self.lock = threading.RLock()
        self._maintenance_held = False
        self._verified: dict[str, tuple[Any, ...]] = {}

    @contextmanager
    def display_inspection(self):
        # Only the SDK-list metadata thread uses this ephemeral scope. Actual
        # enablement, authentication actions and dispatch never enter it.
        with inspect(self):
            yield

    def state(self) -> dict:
        scope = inspection(self)
        if scope is not None:
            scope.watch(self.state_path)
            return scope.memo(('management_state',), self._state)
        return self._state()

    def _state(self) -> dict:
        if not self.state_path.exists():
            return {"revision": 0, "enabled": False, "models": {}, "capabilities": {}}
        try:
            value = read_record(self.state_path)
        except (ValueError, OSError) as exc:
            raise WorkBuddyError("workbuddy_state_invalid") from exc
        if not isinstance(value, dict):
            raise WorkBuddyError("workbuddy_state_invalid")
        if (not isinstance(value.get("revision", 0), int) or isinstance(value.get("revision", 0), bool)
                or value.get("revision", 0) < 0 or not isinstance(value.get("enabled", False), bool)
                or value.get("authenticated") is not None and not isinstance(value["authenticated"], bool)):
            raise WorkBuddyError("workbuddy_state_invalid")
        for name in ("models", "capabilities", "qualification_records", "qualification_legacy_history",
                     "qualification_environment_checks", "model_check_reuse"):
            entries = value.get(name, {})
            if not isinstance(entries, dict) or any(not isinstance(item, dict) for item in entries.values()):
                raise WorkBuddyError("workbuddy_state_invalid")
        for name in ("sdk_integration_records", "model_check_requests", "business_evaluation_projections"):
            entries = value.get(name, {})
            if not isinstance(entries, dict) or any(not isinstance(item, dict) for item in entries.values()):
                raise WorkBuddyError("workbuddy_state_invalid")
        for name in ("installation", "model_discovery"):
            if name in value and not isinstance(value[name], dict):
                raise WorkBuddyError("workbuddy_state_invalid")
        protocol = value.get("qualification_protocol")
        if protocol not in {None, "sapba.validation.qualification/2"}:
            raise WorkBuddyError("workbuddy_qualification_protocol_unsupported")
        allowed_kinds = {"live_pass", "failure", "reuse"}
        if protocol:
            allowed_kinds.add("protocol_fence")
            from .runtime_validation_qualification import valid
            if not isinstance(value.get("validation_ledger"), dict) or any(
                    key != record.get("id") or not valid(record) for key, record in value["validation_ledger"].items()):
                raise WorkBuddyError("workbuddy_state_invalid")
            if not any(r.get("kind") == "protocol_fence" for r in value.get("qualification_records", {}).values()):
                raise WorkBuddyError("workbuddy_qualification_protocol_fence_missing")
        for key, record in value.get("qualification_records", {}).items():
            if (record.get("id") != key or not isinstance(record.get("sequence"), int)
                    or isinstance(record.get("sequence"), bool) or record["sequence"] < 1
                    or record.get("kind") not in allowed_kinds
                    or not isinstance(record.get("binding"), dict)):
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
        if not isinstance(key, str) or len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise WorkBuddyError('workbuddy_environment_not_installed')
        scope = inspection(self)
        if scope is not None:
            return scope.memo(('installation', key), lambda: self._release(key, scope))
        return self._release(key)

    def _release(self, key, scope=None):
        if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise WorkBuddyError("workbuddy_environment_not_installed")
        directory = self.root / "releases" / key
        if scope:
            scope.watch(directory / 'environment.json')
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
            resolved_directory = directory.resolve()
            if scope:
                scope.watch(directory)
                scope.guard(('release_location', key), directory.resolve, resolved_directory)
            for name in files:
                path = (directory / name).resolve()
                info = path.stat()
                if resolved_directory not in path.parents or not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                    raise WorkBuddyError("workbuddy_environment_unavailable")
                observed.append((name, info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                                 info.st_size, info.st_mtime_ns, info.st_ctime_ns))
                if scope:
                    scope.watch(path, info=info)
                    if path != directory / name:
                        scope.watch(directory / name)
            fingerprint = tuple(observed)
            # Windows can keep an identical stat tuple for a rapid same-size
            # rewrite. Never trust that cache for the executable worker entry.
            if file_hash(directory / value["worker"]) != files[value["worker"]]:
                raise WorkBuddyError("workbuddy_environment_digest_mismatch")
            if self._verified.get(key) != fingerprint:
                if any(file_hash(directory / name) != expected for name, expected in files.items()):
                    raise WorkBuddyError("workbuddy_environment_digest_mismatch")
                self._verified[key] = fingerprint
            if scope:
                scope.watch_bytes(directory / value['worker'], files[value['worker']])
                # New/deleted files and redirected directory entries must not
                # disappear behind a request-local installation observation.
                def inventory():
                    return {p.relative_to(directory).as_posix() for p in directory.rglob('*')
                            if p.is_file() and p != directory / 'environment.json'}
                scope.guard(('installation_inventory', key), inventory, present)
        except PermissionError as exc:
            raise WorkBuddyError("workbuddy_environment_access_denied") from exc
        except OSError as exc:
            raise WorkBuddyError("workbuddy_environment_unavailable") from exc
        result = {**value, "directory": str(directory),
                "python_path": str(directory / value["python"]),
                "cli_path": str(directory / value["cli"])}
        return result

    def assert_verification_binding(self, snapshot: dict, *, formal: bool = False) -> dict:
        """Private validation skips selection gates, never identity or login."""
        from .workbuddy_identity import checked_identity
        release = self.release(snapshot.get("environment_digest"))
        if (snapshot.get("provider_id") != "workbuddy" or snapshot.get("reasoning_effort") is not None
                or not isinstance(snapshot.get("model"), str) or not snapshot["model"]):
            raise WorkBuddyError("workbuddy_binding_invalid")
        if (snapshot.get("version") != release["sdk_version"]
                or snapshot.get("cli_version") != release["cli_version"]
                or snapshot.get("sdk_fingerprint") != digest(release["files"])):
            raise WorkBuddyError("workbuddy_environment_digest_mismatch")
        state = self.state()
        from .workbuddy_site import assert_binding, matches, model_matches
        current_site = assert_binding(state, snapshot)
        if (state.get("authenticated") is not True
                or state.get("authentication_environment") != release["environment_digest"]
                or not matches(state.get("authentication_site") or {}, current_site)):
            raise WorkBuddyError("workbuddy_authentication_required")
        from .workbuddy_qualification import effective_model_check, accepts_check_binding
        check = effective_model_check(self, state, release, snapshot["model"])
        from .workbuddy_model_protocol import applicable
        if (check.get("compatible") is not True or check.get("environment_digest") != release["environment_digest"]
                or not applicable(self, check)
                or not check.get("check_digest")
                or not accepts_check_binding(self, state, release, snapshot, check)
                or snapshot.get("authentication_revision", 0) != state.get("authentication_revision", 0)
                or not model_matches(check, state, self)):
            raise WorkBuddyError("runtime_model_check_required")
        if (snapshot.get("actual_model") != check.get("actual_model")
                or snapshot.get("model_identity_known") is not checked_identity(check)
                or snapshot.get("model_identity_source") != check.get("model_identity_source")):
            raise WorkBuddyError("workbuddy_model_identity_changed")
        if formal and (not checked_identity(check) or not checked_identity(snapshot, flag="model_identity_known")):
            raise WorkBuddyError("workbuddy_model_identity_unknown")
        if formal and snapshot["model"] != check.get("actual_model"):
            raise WorkBuddyError("workbuddy_model_alias_not_allowed")
        return release

    def assert_operation(self, snapshot: dict, operation: str, *, mode: str = "bounded") -> dict:
        from .workbuddy_site import assert_binding, matches
        state = self.state()
        current_site = assert_binding(state, snapshot)
        release = self.release(snapshot.get("environment_digest"))
        if snapshot.get("provider_id") != "workbuddy" or snapshot.get("reasoning_effort") is not None:
            raise WorkBuddyError("workbuddy_binding_invalid")
        if mode == "restricted":
            raise WorkBuddyError("workbuddy_windows_restricted_unverified")
        # A saved snapshot is context, not authority. Business evaluations are
        # advisory; SDK operation/mode support and every safety gate remain hard.
        from .workbuddy_integration import require_operation
        require_operation(self, state, release, operation, mode)
        if not state.get('enabled'):
            raise WorkBuddyError('runtime_disabled')
        if snapshot.get("authentication_revision", 0) != state.get("authentication_revision", 0):
            raise WorkBuddyError("workbuddy_capability_binding_mismatch")
        self.assert_verification_binding(snapshot, formal=operation in {"acceptance_baseline", "acceptance_free_query"})
        return release
