"""SDK-free admission scheduling; not a model or business-output contract."""
import asyncio
from dataclasses import dataclass
import time
from contextlib import contextmanager, asynccontextmanager
from contextvars import ContextVar
from functools import wraps
import logging
import threading
import uuid

# Diagnostics only. This context never contains a reusable permission decision.
_trace = ContextVar('runtime_admission_trace', default=None)
_phases = {'request', 'admission', 'selection', 'status', 'installation', 'model',
           'client_version', 'authentication', 'binding', 'lock_wait', 'consistency',
           'schema', 'tools', 'workspace', 'material', 'capacity_wait'}


class AdmissionTrace:
    def __init__(self, kind, store=None, run_id=None):
        self.kind, self.store, self.run_id = kind, store, run_id
        self.trace_id = uuid.uuid4().hex
        self.started = time.perf_counter()
        self.durations, self.counts = {}, {}
        self.closed = False
        self.finished_at = None
        self.lock = threading.Lock()

    def add(self, name, seconds=0):
        if name not in _phases:
            return
        with self.lock:
            if self.closed:
                return
            self.durations[name] = self.durations.get(name, 0) + seconds * 1000
            self.counts[name] = self.counts.get(name, 0) + 1

    def payload(self):
        total = max(0, ((self.finished_at or time.perf_counter()) - self.started) * 1000)
        excluded = sum(self.durations.get(k, 0) for k in ('workspace', 'schema', 'tools', 'capacity_wait'))
        return {'trace_id': self.trace_id, 'boundary': self.kind,
                'admission_ms': round(self.durations.get('admission', 0) if self.kind == 'submission' else max(0, total-excluded), 3),
                'timings_ms': {k: round(v, 3) for k, v in self.durations.items()},
                'counts': dict(self.counts)}

    def finish(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.finished_at = time.perf_counter()
            value = self.payload()
        if self.store is not None and self.run_id is not None:
            try:
                self.store.append_event(self.run_id, 'runtime_admission_profile', value)
            except Exception:
                # Observability cannot replace or mask the original gate error.
                logging.getLogger(__name__).exception('Could not persist admission timing')
        logging.getLogger(__name__).info('runtime_admission_profile %s', value)

    def header(self):
        return ', '.join(f'{k};dur={v:.3f}' for k, v in sorted(self.durations.items()))


def current_trace():
    value = _trace.get()
    return value if value is not None and not value.closed else None


@contextmanager
def tracing(kind, store=None, run_id=None):
    value = AdmissionTrace(kind, store, run_id)
    token = _trace.set(value)
    try:
        yield value
    finally:
        _trace.reset(token)
        value.finish()


@contextmanager
def measure(name):
    value = current_trace()
    started = time.perf_counter() if value is not None else 0
    try:
        yield
    finally:
        if value is not None:
            value.add(name, time.perf_counter() - started)


def metered(name):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with measure(name):
                return function(*args, **kwargs)
        return call
    return decorate


@asynccontextmanager
async def measured_authentication(service, provider, snapshot):
    value = current_trace()
    started, entered = time.perf_counter(), False
    try:
        async with service.use(provider, snapshot):
            entered = True
            if value is not None:
                value.add('authentication', time.perf_counter() - started)
            yield
    finally:
        if not entered and value is not None:
            value.add('authentication', time.perf_counter() - started)


@dataclass(frozen=True)
class RuntimeAdmissionRequest:
    provider_id: str | None = None
    operation: str | None = None
    permission_mode: str = 'bounded'
    snapshot: dict | None = None
    deadline: float | None = None

    def check_deadline(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            from .sdk_manager import SDKManagerError
            raise SDKManagerError('Admission deadline exceeded.', code='runtime_deadline_exceeded')


@dataclass(frozen=True)
class PreparedRuntime:
    snapshot: dict
    operation: str | None
    permission_mode: str


async def prepare(planner, request):
    callback = getattr(planner, 'prepare_admission', None)
    if callable(callback):
        with measure('admission'):
            result = await asyncio.to_thread(callback, request)
        request.check_deadline()
        return result.snapshot
    return await snapshot(planner.snapshot)


def selected_status(manager, selected):
    admission = getattr(manager, 'admission_status', None)
    if callable(admission):
        return admission(selected)
    # Compatibility for injected/legacy managers, not production SDKManager.
    return next((item for item in manager.list() if item.get('provider_id') == selected), None)


async def snapshot(loader):
    # ContextVars are copied by to_thread. All gates complete before persistence.
    return await asyncio.to_thread(loader)


async def driver_binding(driver, run_id, state, model, reasoning_effort):
    if driver.provider_id == 'workbuddy':
        return await asyncio.to_thread(driver.binding, run_id, state, model, reasoning_effort)
    return driver.binding(run_id, state, model, reasoning_effort)
