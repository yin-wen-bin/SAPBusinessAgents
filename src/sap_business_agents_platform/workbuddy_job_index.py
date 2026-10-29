"""Versioned unfinished-job journal. No historical job is rewritten on migration.

Index -> job is the only lock order. A dirty two-file transaction blocks reads
until an explicit recovery with stopped-writer clearance; never guess success.
"""
from __future__ import annotations

import json
import hashlib
import os
import stat
import time
import uuid
from pathlib import Path

from .workbuddy_record_io import record_lock, read_record

PROTOCOL = 'sapba.workbuddy.unfinished-jobs/1'
ACTIVE = {'dispatching', 'running', 'cleanup_pending'}


def marker(root):
    return Path(root) / 'job-index-protocol.json'


def enabled(root):
    if _path(root).exists() and not marker(root).exists():
        _error()
    return marker(root).exists()


def _path(root):
    return Path(root) / 'unfinished-jobs.json'


def _error():
    from .workbuddy_environment import WorkBuddyError
    raise WorkBuddyError('workbuddy_job_index_recovery_required')


def _load(root):
    from .workbuddy_environment import digest
    try:
        policy = json.loads(marker(root).read_bytes())
        value = json.loads(_path(root).read_bytes())
        if (policy != {'protocol': PROTOCOL} or value.get('protocol') != PROTOCOL
                or value.get('sha256') != digest({k: v for k, v in value.items() if k != 'sha256'})
                or not isinstance(value.get('entries'), dict) or value.get('dirty') is not None):
            _error()
        for name, item in value['entries'].items():
            if (len(name) != 69 or not name.endswith('.json')
                    or any(c not in '0123456789abcdef' for c in name[:-5])
                    or not isinstance(item, dict) or item.get('status') not in ACTIVE):
                _error()
        return value
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        _error()


def _store(root, value, *, deadline=None):
    from .workbuddy_environment import _atomic_json_owned, digest
    value = {k: v for k, v in value.items() if k != 'sha256'}
    _atomic_json_owned(_path(root), {**value, 'sha256': digest(value)}, deadline=deadline)


def _entry(record):
    from .workbuddy_environment import digest
    return {key: record.get(key) for key in ('status', 'task_id', 'pid', 'process_identity')} | {'record_digest': digest(record)}


def paths(root, *, scope=None):
    """Read only unfinished records, but verify them against their original bytes."""
    from .workbuddy_environment import digest
    root = Path(root)
    with record_lock(_path(root)):
        value = _load(root)
        result = []
        if scope:
            scope.watch(_path(root))
            scope.watch(marker(root))
        for name, entry in value['entries'].items():
            path = root / 'jobs' / name
            try:
                record = read_record(path)
            except (OSError, ValueError):
                _error()
            if digest(record) != entry['record_digest'] or record.get('status') != entry['status']:
                _error()
            if scope:
                scope.watch(path)
            result.append((path, record))
        return result


def pending(root, *, scope=None, owned_dispatch=None):
    """Gate on cleanup/dispatch controls, not another job's progress bytes.

    Dispatch still reconciles all unfinished process identities. A running job
    may emit progress while a different request queues; that does not change
    cleanup eligibility. Dirty/inconsistent index transactions remain blocking.
    """
    def controls():
        with record_lock(_path(root)):
            value = _load(root)
            return {name: entry for name, entry in value['entries'].items()
                    if entry['status'] in {'cleanup_pending', 'dispatching'}
                    and not (entry['status'] == 'dispatching' and owned_dispatch
                             and owned_dispatch(read_record(Path(root) / 'jobs' / name)))}
    observed = controls()
    if scope:
        scope.watch(marker(root))
        scope.guard(('unfinished_cleanup_controls',), controls, observed)
    return list(observed.values())


def write(path, value, *, deadline=None, new=False):
    from .workbuddy_environment import _atomic_json_owned, digest, WorkBuddyError
    path = Path(path)
    root = path.parent.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    with record_lock(_path(root), deadline=deadline):
        index = _load(root)
        if path.name != digest(value.get('task_id')) + '.json':
            _error()
        with record_lock(path, deadline=deadline):
            if new and path.exists():
                raise WorkBuddyError('workbuddy_task_busy')
            if not new and not path.exists():
                _error()
            if path.exists():
                prior = json.loads(path.read_bytes())
                if prior.get('status') not in ACTIVE and prior.get('cleanup_complete') is True:
                    if value.get('status') in ACTIVE or value.get('cleanup_complete') is not True:
                        raise WorkBuddyError('workbuddy_job_already_closed')
            # Fail closed on a crash/write failure at either boundary.
            index.update(dirty=path.name, revision=index['revision'] + 1)
            _store(root, index, deadline=deadline)
            _atomic_json_owned(path, value, deadline=deadline)
            if value.get('status') in ACTIVE or value.get('cleanup_complete') is not True:
                if value.get('status') not in ACTIVE:
                    _error()
                index['entries'][path.name] = _entry(value)
            else:
                index['entries'].pop(path.name, None)
            index['dirty'] = None
            _store(root, index, deadline=deadline)


def _linked(path):
    try:
        value = path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISLNK(value.st_mode)
            or bool(getattr(value, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)))


def completed_job_path(root, task_id):
    """Construct a task path without following a substituted jobs directory."""
    from .workbuddy_environment import WorkBuddyError, digest
    if not isinstance(task_id, str) or not task_id:
        raise WorkBuddyError('workbuddy_job_import_invalid')
    root = Path(root).resolve()
    directory = root / 'jobs'
    path = directory / (digest(task_id) + '.json')
    for candidate in (directory, path, path.with_name(path.name + '.read-write.lock')):
        # Reject the reparse point itself. Comparing resolve() output while a
        # peer atomically publishes a file can spuriously fail on Windows.
        if _linked(candidate):
            raise WorkBuddyError('workbuddy_job_import_invalid')
    return path


def _atomic_bytes_owned(path, raw, *, deadline=None):
    """Publish the checked bytes; JSON serialization would change evidence SHA."""
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
        for attempt in range(3):
            try:
                os.replace(temporary, path)
                return
            except OSError as error:
                delay = (0.01, 0.02)[min(attempt, 1)]
                if (getattr(error, 'winerror', None) not in {32, 33} or attempt == 2
                        or deadline is not None and time.monotonic() + delay >= deadline):
                    raise
                time.sleep(delay)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _import_coordination_paths(root):
    from .workbuddy_environment import WorkBuddyError
    for path in (marker(root), _path(root), _path(root).with_name(_path(root).name + '.read-write.lock')):
        if _linked(path):
            raise WorkBuddyError('workbuddy_job_import_invalid')


def completed_job_bytes(root, task_id, *, deadline=None):
    """Read one source snapshot under index -> job locks, never across roots."""
    from .workbuddy_environment import WorkBuddyError
    root = Path(root).resolve()
    path = completed_job_path(root, task_id)
    _import_coordination_paths(root)
    with record_lock(_path(root), deadline=deadline):
        index = _load(root) if enabled(root) else None
        with record_lock(path, deadline=deadline):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('completed job snapshot deadline exceeded')
            completed_job_path(root, task_id)
            if index is not None and path.name in index['entries']:
                raise WorkBuddyError('workbuddy_job_import_conflict')
            return path.read_bytes()


def import_completed_job(root, raw_bytes, expected_task_id, expected_sha256, *, deadline=None):
    """Idempotently import one terminal model proof without weakening write().

    Take the index lock even for a legacy destination so an explicit index
    migration cannot interleave between selecting the mode and taking the job
    lock. Only the existing migration/maintenance path can create an index.
    """
    from .workbuddy_environment import WorkBuddyError
    if (not isinstance(raw_bytes, bytes) or not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(c not in '0123456789abcdef' for c in expected_sha256)
            or hashlib.sha256(raw_bytes).hexdigest() != expected_sha256):
        raise WorkBuddyError('workbuddy_job_import_invalid')
    try:
        value = json.loads(raw_bytes.decode('utf-8'))
    except (UnicodeError, ValueError):
        raise WorkBuddyError('workbuddy_job_import_invalid') from None
    if (not isinstance(value, dict) or value.get('task_id') != expected_task_id
            or value.get('operation') != 'model_check' or value.get('status') != 'completed'
            or value.get('cleanup_complete') is not True):
        raise WorkBuddyError('workbuddy_job_import_invalid')
    root = Path(root).resolve()
    path = completed_job_path(root, expected_task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _import_coordination_paths(root)
    with record_lock(_path(root), deadline=deadline):
        index = _load(root) if enabled(root) else None
        with record_lock(path, deadline=deadline):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('completed job import deadline exceeded')
            completed_job_path(root, expected_task_id)
            if index is not None and path.name in index['entries']:
                raise WorkBuddyError('workbuddy_job_import_conflict')
            if path.exists():
                if not path.is_file() or path.read_bytes() != raw_bytes:
                    raise WorkBuddyError('workbuddy_job_import_conflict')
                return 'already_present'
            if index is not None:
                index.update(dirty=path.name, revision=index['revision'] + 1)
                _store(root, index, deadline=deadline)
            _atomic_bytes_owned(path, raw_bytes, deadline=deadline)
            if index is not None:
                # Terminal evidence never becomes an unfinished business job.
                index['dirty'] = None
                _store(root, index, deadline=deadline)
            return 'created'


def claim(root, binding, *, deadline):
    """Durable claim precedes process creation. No model/SAP activity here."""
    from .workbuddy_environment import digest
    from .workbuddy_job import process_identity
    if not enabled(root):
        return
    record = {**binding, 'status': 'dispatching', 'cleanup_complete': False,
              'dispatch_phase': 'claimed',
              'dispatch_owner_pid': os.getpid(), 'dispatch_owner_identity': process_identity(os.getpid())}
    write(Path(root) / 'jobs' / (digest(binding['task_id']) + '.json'), record, deadline=deadline, new=True)


def spawning(root, binding, *, deadline):
    """Declare the ambiguous spawn window *before* calling CreateProcess."""
    from .workbuddy_environment import digest
    if not enabled(root):
        return
    path = Path(root) / 'jobs' / (digest(binding['task_id']) + '.json')
    record = read_record(path)
    record['dispatch_phase'] = 'spawning'
    write(path, record, deadline=deadline)


def owned(root, binding, pid, birth, *, deadline):
    from .workbuddy_environment import digest, WorkBuddyError
    if not enabled(root):
        return
    if not birth or birth == 'unknown':
        raise WorkBuddyError('workbuddy_process_identity_unknown')
    path = Path(root) / 'jobs' / (digest(binding['task_id']) + '.json')
    record = read_record(path)
    record.update(dispatch_phase='owned', pid=pid, process_identity=birth)
    write(path, record, deadline=deadline)


def rebuild(environment, *, clearance, recover=False):
    """Explicit maintenance only; callers must stop *all* old journal writers."""
    from .workbuddy_environment import atomic_json, _atomic_json_owned, WorkBuddyError, digest
    from .workbuddy_job import process_identity
    with environment.maintenance():
        proof = clearance()
        if (not proof.get('all_writers_checked') or proof.get('old_writers') != []
                or proof.get('active_jobs') != [] or proof.get('cleanup_pending') != []
                or not time.time() - 30 <= proof.get('checked_at', 0) <= time.time() + 1):
            raise WorkBuddyError('workbuddy_job_index_writer_clearance_required')
        with record_lock(_path(environment.root)):
            entries = {}
            recovered = []
            for path in (environment.root / 'jobs').glob('*.json'):
                record = read_record(path)
                if not isinstance(record, dict) or record.get('status') not in ACTIVE | {'completed', 'failed', 'cancelled', 'interrupted', 'timed_out'}:
                    raise WorkBuddyError('workbuddy_job_index_unresolved_job')
                if record.get('status') in ACTIVE or record.get('cleanup_complete') is not True:
                    if record.get('status') == 'dispatching' and not record.get('pid') and record.get('dispatch_phase') != 'claimed':
                        # Owner absence alone cannot prove that CreateProcess
                        # did not already create a child before PID persistence.
                        raise WorkBuddyError('workbuddy_job_index_unresolved_job')
                    pid = record.get('pid') or record.get('dispatch_owner_pid')
                    birth = record.get('process_identity') or record.get('dispatch_owner_identity')
                    identity = process_identity(int(pid or 0))
                    if (not recover or not pid or not birth or identity == 'unknown'
                            or identity is not None and identity == birth):
                        raise WorkBuddyError('workbuddy_job_index_unresolved_job')
                    # Only unfinished records may transition; never rewrite an
                    # old terminal PASS or infer that a live PID is ours to kill.
                    if record.get('status') not in ACTIVE:
                        raise WorkBuddyError('workbuddy_job_index_unresolved_job')
                    before = digest(record)
                    record = {**record, 'status': 'interrupted', 'cleanup_complete': True}
                    with record_lock(path):
                        _atomic_json_owned(path, record)
                    recovered.append({'path': path.name, 'before': before, 'after': digest(record), 'resolution': 'owned_process_absent'})
            _store(environment.root, {'protocol': PROTOCOL, 'revision': 1, 'entries': entries, 'dirty': None})
            atomic_json(marker(environment.root), {'protocol': PROTOCOL})
            if recovered:
                receipt = {'protocol': PROTOCOL, 'records': recovered, 'clearance_digest': digest(proof)}
                atomic_json(environment.root / 'job-index-recovery' / (digest(receipt) + '.json'), receipt)
        return {'protocol': PROTOCOL, 'unfinished': 0}
