"""Local startup coordination. Metadata is never Runtime execution authority."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import threading
import uuid
from contextvars import ContextVar
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


# Local cooperation, not an authorization boundary. Unknown processes retain the
# launcher's original writer checks. No credentials or tool inputs are recorded.
MCP_PROTOCOL = 'sapba.mcp-maintenance/1'
MCP_COMPONENTS = {
    'sap_business_agents_test_agent.mcp': 'src/sap_business_agents_test_agent/mcp.py',
    'sap_business_agents_platform.mcp_server': 'src/sap_business_agents_platform/mcp_server.py',
}
_mcp_owner = ContextVar('sapba_mcp_maintenance_owner', default=None)


def process_stamp(pid):
    """Native creation identity; None means dead, access errors fail closed."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes as w
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.GetProcessTimes.argtypes = [w.HANDLE, *([ctypes.POINTER(w.FILETIME)] * 4)]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() in {87, 1168}: return None
            raise StartupBlocked('startup_mcp_process_identity_unknown')
        try:
            times = [w.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
                raise StartupBlocked('startup_mcp_process_identity_unknown')
            return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    try:
        # comm may contain spaces/parentheses. starttime is field 22.
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except FileNotFoundError:
        return None


def _mcp_directory(root):
    root = Path(root).resolve()
    directory = root / '.local-data/startup/mcp'
    for path in (root / '.local-data', directory.parent, directory):
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise StartupBlocked('startup_mcp_coordination_link_rejected')
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _mcp_file(directory, name):
    path = directory / name
    if (path.is_symlink() or getattr(path, 'is_junction', lambda: False)()
            or path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
        raise StartupBlocked('startup_mcp_coordination_link_rejected')
    return path


def _mcp_write(path, value):
    # Multiple worker threads share the coordinator; never share a temp name.
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class MCPParticipant:
    """Keep stdio alive while tool calls cooperate with a one-shot launcher."""
    def __init__(self, root, component, source):
        if component not in MCP_COMPONENTS:
            raise StartupBlocked('startup_mcp_component_unknown')
        self.root = Path(root).resolve()
        self.directory = _mcp_directory(self.root)
        self.session = uuid.uuid4().hex
        self.record = {'protocol': MCP_PROTOCOL, 'root': str(self.root),
            'component': component, 'pid': os.getpid(), 'birth': process_stamp(os.getpid()),
            'session': self.session, 'proof': {
                'src/sap_business_agents_platform/startup_control.py': sha(__file__),
                MCP_COMPONENTS[component]: sha(source)}}
        self.path = _mcp_file(self.directory, f'{os.getpid()}.json')
        self.stop = threading.Event()
        self.thread = None

    def __enter__(self):
        with file_lock(_mcp_file(self.directory, 'control.lock'), wait=5):
            _mcp_write(self.path, self.record)
        self.thread = threading.Thread(target=self._acknowledge, name='sapba-mcp-maintenance', daemon=True)
        self.thread.start()
        return self

    def _acknowledge(self):
        while not self.stop.wait(.05):
            try:
                marker = _mcp_file(self.directory, 'maintenance.json')
                if not marker.exists(): continue
                value = json.loads(marker.read_bytes())
                if value.get('protocol') != MCP_PROTOCOL or value.get('root') != str(self.root): continue
                # The launcher separately owns the exclusive operation lock.
                # This live nonce response proves a participant, not idleness.
                ack = {**self.record, 'challenge': value['challenge']}
                _mcp_write(_mcp_file(self.directory, self.session + '.ack.json'), ack)
            except (OSError, ValueError, KeyError, StartupBlocked):
                # Missing acknowledgement blocks the launcher, never exempts us.
                continue

    @contextmanager
    def operation(self, *, cancel=False):
        with file_lock(_mcp_file(self.directory, 'control.lock'), wait=1):
            if _mcp_file(self.directory, 'maintenance.json').exists() and not cancel:
                raise StartupBlocked('startup_mcp_maintenance_pending')
            operation = file_lock(_mcp_file(self.directory, 'operations.lock'), shared=True)
            operation.__enter__()
        try:
            yield
        finally:
            operation.__exit__(None, None, None)

    def __exit__(self, *error):
        self.stop.set()
        if self.thread is not None: self.thread.join(timeout=2)
        with file_lock(_mcp_file(self.directory, 'control.lock'), wait=5):
            if self.path.exists() and json.loads(self.path.read_bytes()).get('session') == self.session:
                self.path.unlink()
            _mcp_file(self.directory, self.session + '.ack.json').unlink(missing_ok=True)


@contextmanager
def mcp_maintenance(root, *, activity_root=None, timeout=90, recovery=False):
    """Close MCP admissions and drain tools BEFORE taking deployment locks.

    Normal Ctrl+C releases a pre-switch pause. A crashed owner can be recovered
    only if the original service transaction has no unresolved journal/barrier.
    """
    root = Path(root).resolve()
    activity_root = Path(activity_root or root / '.local-data')
    directory = _mcp_directory(root)
    marker = _mcp_file(directory, 'maintenance.json')
    unresolved = lambda: (activity_root / 'startup-maintenance.json').exists() or (root / '.local-data/startup/pending-switch.json').exists()
    with file_lock(_mcp_file(directory, 'owner.lock')):
        with file_lock(_mcp_file(directory, 'control.lock'), wait=1):
            if marker.exists():
                old = json.loads(marker.read_bytes())
                if (old.get('protocol') != MCP_PROTOCOL or old.get('root') != str(root)
                        or not isinstance(old.get('pid'), int) or not old.get('birth')):
                    raise StartupBlocked('startup_mcp_owner_unknown')
                if process_stamp(old['pid']) == old['birth'] and not (recovery and old['pid'] == os.getpid()):
                    raise StartupBlocked('startup_mcp_owner_alive')
                if unresolved() and not recovery:
                    raise StartupBlocked('startup_switch_recovery_required')
            value = {'protocol': MCP_PROTOCOL, 'root': str(root), 'pid': os.getpid(),
                'birth': process_stamp(os.getpid()), 'challenge': uuid.uuid4().hex}
            _mcp_write(marker, value)
        operation = None
        token = None
        try:
            deadline = time.monotonic() + timeout
            announced = False
            while True:
                try:
                    operation = file_lock(_mcp_file(directory, 'operations.lock'))
                    operation.__enter__()
                    break
                except StartupBlocked as error:
                    operation = None
                    if str(error) != 'startup_coordination_busy': raise
                    if time.monotonic() >= deadline:
                        raise StartupBlocked('startup_mcp_drain_timeout')
                    if not announced:
                        print('[SAPBusinessAgents] Waiting for accepted MCP calls; connection stays open, new calls are paused.', flush=True)
                        announced = True
                    time.sleep(.1)
            token = _mcp_owner.set(value)
            yield value
        finally:
            if token is not None: _mcp_owner.reset(token)
            if operation is not None: operation.__exit__(None, None, None)
            with file_lock(_mcp_file(directory, 'control.lock'), wait=5):
                if marker.exists() and json.loads(marker.read_bytes()) == value and not unresolved():
                    marker.unlink()


def mcp_quiescent_pids(root, rows, trusted_source):
    """Only identity-bound, live, reviewed participants may pass writer checks."""
    owner = _mcp_owner.get()
    if owner is None: return set()
    root = Path(root).resolve()
    directory = _mcp_directory(root)
    if json.loads(_mcp_file(directory, 'maintenance.json').read_bytes()) != owner:
        raise StartupBlocked('startup_mcp_barrier_changed')
    proofs = []
    source = Path(trusted_source)
    for component, name in MCP_COMPONENTS.items():
        paths = ('src/sap_business_agents_platform/startup_control.py', name)
        if all((source / p).is_file() for p in paths):
            proofs.append((component, {p: sha(source / p) for p in paths}))
    current = root / '.local-data/startup-api-current.json'
    if current.exists():
        record = json.loads(current.read_bytes())
        key = record['source_digest']
        if not isinstance(key, str) or len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise StartupBlocked('startup_mcp_active_binding_unknown')
        archive = root / '.local-data/startup-releases' / key
        if str(archive) != record['archive'] or archive.resolve() != archive:
            raise StartupBlocked('startup_mcp_active_binding_unknown')
        manifest = json.loads((archive / 'manifest.json').read_bytes())
        if manifest.get('digest') != key or digest({k:v for k,v in manifest.items() if k != 'digest'}) != key:
            raise StartupBlocked('startup_mcp_active_binding_unknown')
        for component, name in MCP_COMPONENTS.items():
            paths = ('src/sap_business_agents_platform/startup_control.py', name)
            if all(manifest['files'].get(p) == sha(archive / 'source' / p) for p in paths if p in manifest['files']) and all(p in manifest['files'] for p in paths):
                proofs.append((component, {p:manifest['files'][p] for p in paths}))
    accepted = set()
    for row in rows:
        pid = row['ProcessId']
        path = _mcp_file(directory, f'{pid}.json')
        if not path.exists(): continue
        try:
            record = json.loads(path.read_bytes())
            component = record['component']
            command = (row.get('CommandLine') or '').replace('\\', '/')
            import re
            if (record.get('protocol') != MCP_PROTOCOL or record.get('root') != str(root)
                    or record.get('pid') != pid or record.get('birth') != process_stamp(pid)
                    or (component, record.get('proof')) not in proofs
                    or row.get('Name', '').lower() not in {'python.exe', 'pythonw.exe', 'python', 'python3'}
                    or not re.search(r'(?:^|\s)-m\s+' + re.escape(component) + r'(?:\s|$)', command)
                    or not re.fullmatch('[a-f0-9]{32}', record['session'])):
                continue
            ack = _mcp_file(directory, record['session'] + '.ack.json')
            deadline = time.monotonic() + 2
            while True:
                try: observed = json.loads(ack.read_bytes())
                except (OSError, ValueError): observed = None
                if observed == {**record, 'challenge': owner['challenge']}:
                    # Recheck after handshake; PID reuse cannot inherit a lease.
                    if process_stamp(pid) == record['birth']: accepted.add(pid)
                    break
                if time.monotonic() >= deadline: break
                time.sleep(.05)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return accepted
