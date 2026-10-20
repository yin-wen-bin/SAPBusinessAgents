"""Synchronous, owner-bound local inspections; never an authorization cache.

Only nested calls on the same environment, thread and asyncio task may share
observations. No scope survives a request, queue wait, or worker dispatch.
"""
from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
import logging
from pathlib import Path
import threading
import time

_current = ContextVar('workbuddy_local_inspection', default=None)


def owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


def stamp(path):
    try:
        info = Path(path).stat()
        return signature(info)
    except FileNotFoundError:
        return None


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def changed():
    from .workbuddy_environment import WorkBuddyError
    raise WorkBuddyError('workbuddy_inspection_changed')


class Inspection:
    def __init__(self, environment):
        self.environment, self.owner = environment, owner()
        self.values, self.files, self.hashes, self.guards = {}, {}, {}, {}
        self.statistics = {}
        self.closed, self.yielded = False, False
        self.started = time.perf_counter()
        self.watch(environment.state_path)

    def watch(self, path, *, info=None):
        path = Path(path)
        value = stamp(path) if info is None else signature(info)
        if path in self.files and self.files[path] != value:
            changed()
        self.files[path] = value

    def watch_bytes(self, path, sha256):
        self.watch(path)
        self.hashes[Path(path)] = sha256

    def guard(self, key, getter, observed=None):
        value = getter() if observed is None else observed
        if key in self.guards and self.guards[key][1] != value:
            changed()
        self.guards[key] = getter, value

    def verify(self):
        if self.closed or self.yielded:
            changed()
        for path, expected in self.files.items():
            if stamp(path) != expected:
                changed()
        for getter, expected in self.guards.values():
            if getter() != expected:
                changed()
        # Small critical/source/evidence files are rehashed at the scope fence;
        # this is not a new stat-only content cache. Release payload files keep
        # their existing full-content/stat-identity verification policy.
        for path, expected in self.hashes.items():
            try:
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    changed()
            except OSError:
                changed()

    def memo(self, key, loader):
        if self.closed or self.yielded:
            changed()
        if key not in self.values:
            started = time.perf_counter()
            value = loader()  # Never memoize failures.
            self.statistics[key[0]] = self.statistics.get(key[0], 0) + (time.perf_counter() - started)
            self.values[key] = copy.deepcopy(value)
        return copy.deepcopy(self.values[key])


def current(environment):
    scope = _current.get()
    return scope if (scope is not None and not scope.closed and scope.environment is environment and scope.owner == owner()) else None


def record_scope(path):
    """Only owned job journals share a read within one synchronous fence."""
    scope = _current.get()
    if scope is None or scope.closed or scope.yielded or scope.owner != owner():
        return None
    path = Path(path).resolve()
    return scope if path.parent == (scope.environment.root / 'jobs').resolve() else None


@contextmanager
def inspect(environment):
    from .workbuddy_environment import WorkBuddyEnvironment
    if not isinstance(environment, WorkBuddyEnvironment):
        # Injected adapters retain their previous checks. They do not receive
        # a reusable production inspection or a made-up filesystem binding.
        yield None
        return
    prior = current(environment)
    if prior is not None:
        yield prior
        return
    scope = Inspection(environment)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        yielded = None
    else:
        yielded = loop.call_soon(lambda: setattr(scope, 'yielded', True))
    token = _current.set(scope)
    try:
        yield scope
        scope.verify()
    finally:
        scope.closed = True
        if yielded is not None:
            yielded.cancel()
        _current.reset(token)
        logging.getLogger(__name__).debug('WorkBuddy local inspection', extra={
            'local_inspection_ms': round((time.perf_counter() - scope.started) * 1000, 2),
            'local_inspection_phases_ms': {k: round(v * 1000, 2) for k, v in scope.statistics.items()}})


def inspected(function):
    @wraps(function)
    def call(self, *args, **kwargs):
        environment = getattr(self, 'environment', self)
        with inspect(environment):
            return function(self, *args, **kwargs)
    return call
