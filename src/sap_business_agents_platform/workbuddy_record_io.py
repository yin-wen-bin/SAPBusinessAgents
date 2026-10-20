"""Short cooperative read/write locks for mutable WorkBuddy host journals.

Windows may deny atomic replacement while a reader holds the target. Never
retry ACCESS_DENIED or weaken ACLs; keep our readers out of that window.
"""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import time


@contextmanager
def record_lock(path: Path, *, deadline: float | None = None):
    path = Path(path)
    end = min(deadline if deadline is not None else float('inf'), time.monotonic() + .5)
    with path.with_name(path.name + '.read-write.lock').open('a+b') as stream:
        acquired = False
        while not acquired:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() + .005 >= end:
                    raise OSError(errno.EBUSY, 'WorkBuddy journal is busy') from None
                time.sleep(.005)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def open_record_snapshot(path: Path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.name == 'environment.json':
        with path.open('rb') as stream:
            yield stream
        return
    with record_lock(path):
        with path.open('rb') as stream:
            yield stream


def read_record(path: Path):
    from .workbuddy_inspection import record_scope
    import hashlib
    scope = record_scope(path)
    def read():
        with open_record_snapshot(path) as stream:
            content = stream.read()
        if scope:
            scope.watch_bytes(path, hashlib.sha256(content).hexdigest())
        return json.loads(content.decode('utf-8'))
    return scope.memo(('job_record', str(Path(path).resolve())), read) if scope else read()
