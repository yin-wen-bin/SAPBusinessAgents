"""Local startup coordination. Metadata is never Runtime execution authority."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class StartupBlocked(RuntimeError):
    pass


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def initial_install_state(root):
    """Allow a first deployment or its failed preparation, never repair old records.

    The receipt identifies files created by an interrupted first installation.
    It is coordination metadata, not SDK/model/SAP execution authority.
    """
    root = Path(root).resolve()
    data = root / '.local-data'
    for path in (data, data / 'startup', data / 'site-builds',
                 data / 'startup-releases', data / 'startup-source-archives'):
        if (os.path.lexists(path) and getattr(path.lstat(), 'st_file_attributes', 0) & 0x400
                or path.is_symlink()):
            raise StartupBlocked('startup_initialization_link_rejected')
    records = ('startup-api-current.json', 'startup-api-previous.json',
               'startup-api-legacy-rollback.json', 'site-builds/current.json',
               'site-builds/previous.json', 'startup-maintenance.json',
               'startup/pending-switch.json')
    if any(os.path.lexists(data / name) for name in records):
        raise StartupBlocked('startup_initialization_existing_state')
    receipt_path = data / 'startup/initialization.json'
    receipt = None
    if os.path.lexists(receipt_path):
        try:
            if receipt_path.is_symlink():
                raise ValueError('link')
            receipt = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
            if (receipt['version'] != 1 or receipt['root'] != str(root)
                    or receipt['status'] not in {'preparing', 'failed'}):
                raise ValueError('receipt')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise StartupBlocked('startup_initialization_recovery_required') from exc
    if receipt is None:
        if os.path.lexists(data / 'platform.sqlite3'):
            raise StartupBlocked('startup_initialization_existing_data')
        for name in ('startup-releases', 'startup-source-archives', 'site-builds'):
            directory = data / name
            if os.path.lexists(directory) and (not directory.is_dir()
                    or any(p.name not in {'archive.lock', 'release.lock', 'build.lock'} for p in directory.iterdir())):
                raise StartupBlocked('startup_initialization_existing_state')
        if os.path.lexists(data / 'startup/latest.json'):
            raise StartupBlocked('startup_initialization_existing_state')
    return receipt


@contextmanager
def file_lock(path, *, wait=0, shared=False):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    until = time.monotonic() + wait
    while True:
        try:
            opened = path.open('a+b'); break
        except OSError as exc:
            if time.monotonic() >= until: raise StartupBlocked('startup_coordination_busy') from exc
            time.sleep(.1)
    with opened as stream:
        # Lock beyond EOF; never initialize/read a byte before ownership.
        deadline = until
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes as w
            class Overlapped(ctypes.Structure):
                _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                            ("Offset", w.DWORD), ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.LockFileEx.argtypes = [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.POINTER(Overlapped)]
            kernel.LockFileEx.restype = w.BOOL
            kernel.UnlockFileEx.argtypes = [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, ctypes.POINTER(Overlapped)]
            kernel.UnlockFileEx.restype = w.BOOL
            handle, overlapped = msvcrt.get_osfhandle(stream.fileno()), Overlapped()
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    if not kernel.LockFileEx(handle, 1 | (0 if shared else 2), 0, 1, 0, ctypes.byref(overlapped)):
                        raise ctypes.WinError(ctypes.get_last_error())
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise StartupBlocked("startup_coordination_busy") from exc
                time.sleep(.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                if not kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped)):
                    raise ctypes.WinError(ctypes.get_last_error())
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def maintenance_pending(data_root):
    # An orphaned marker fails closed; recovery is an explicit owned operation.
    return (Path(data_root) / "startup-maintenance.json").exists()


@contextmanager
def admission(data_root):
    with file_lock(Path(data_root) / "startup-admission.lock", shared=True):
        if maintenance_pending(data_root):
            raise StartupBlocked("startup_maintenance_pending")
        yield


def activity(data_root):
    """Read-only inspection, never initialize/migrate/recover the live store."""
    data_root = Path(data_root)
    database = data_root / "platform.sqlite3"
    busy = {}
    if database.exists():
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            terminals = ("completed", "failed", "cancelled", "interrupted", "passed", "blocked", "superseded", "saved", "done", "applied", "answered", "expired", "inconclusive", "needs_input", "ready", "unavailable")
            for table in ("runs", "execution_jobs", "agent_draft_operations", "agent_acceptance_campaigns", "role_matching_jobs", "workflow_conversation_turns"):
                if table not in tables:
                    continue
                columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                if "status" not in columns:
                    raise StartupBlocked("startup_activity_schema_unknown")
                placeholders = ",".join("?" for _ in terminals)
                count = connection.execute(f'SELECT count(*) FROM "{table}" WHERE status NOT IN ({placeholders})', terminals).fetchone()[0]
                if count:
                    busy[table] = count
    authentication = data_root / "sdk-runtimes/authentication.json"
    if authentication.exists():
        value = json.loads(authentication.read_text(encoding="utf-8-sig"))
        count = sum(item.get("status") in {"queued", "running", "starting", "cancelling", "waiting_user", "waiting_browser"} for item in value.get("operations", {}).values())
        if count:
            busy["authentication"] = count
    return busy


def startup_projection():
    # Set only by the verified frozen-source bootstrap, not an environment claim.
    return dict(_binding) if _binding is not None else {"verified": False, "status": "version_unknown"}


@contextmanager
def startup_phase(name):
    began = time.monotonic()
    try: yield
    finally:
        if _binding is not None:
            _binding.setdefault("phases_ms", {})[name] = round((time.monotonic() - began) * 1000, 2)


_binding = None


def install_barrier(application, settings):
    """Idempotent for current APIs and source-verified rollback bootstraps."""
    if getattr(application.state, "startup_barrier_installed", False): return
    application.state.startup_barrier_installed = True
    from starlette.responses import JSONResponse
    @application.middleware("http")
    async def barrier(request, call_next):
        if request.method in {"GET", "HEAD", "OPTIONS"} or request.url.path.endswith(("/cancel", "/heartbeat", "/renew")):
            return await call_next(request)
        try:
            with admission(settings.data_root):
                return await call_next(request)
        except StartupBlocked:
            return JSONResponse(status_code=503, content={"detail": {"code": "startup_maintenance_pending", "accepted": False, "message": "Service update in progress. No task was accepted."}})
