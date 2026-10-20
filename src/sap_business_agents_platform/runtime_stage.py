"""SDK-free role phase deadlines and safe, task-local progress metadata."""
from contextlib import contextmanager
from contextvars import ContextVar
import time

from .runtime_contract import deadline_scope
from .runtime_diagnostics import stage_failure

_sink = ContextVar("runtime_stage_sink", default=None)
_stage = ContextVar("runtime_stage", default=None)


@contextmanager
def bind_stage_events(sink):
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def stage_metadata():
    return {k: v for k, v in (_stage.get() or {}).items() if k not in {"started", "native_progress"}}


def record_stage_progress(value):
    """Keep safe driver milestones even if later platform decoding fails."""
    stage = _stage.get()
    if stage is not None:
        stage.setdefault("native_progress", {}).update(value)


def _emit(kind, data):
    sink = _sink.get()
    if sink:
        sink(kind, data)


def role_observation_seconds(page_count):
    from .runtime_policy import ROLE_MATCHING_RUNTIME_TURN_SECONDS
    return (max(0, int(page_count)) + 2) * (ROLE_MATCHING_RUNTIME_TURN_SECONDS + 10) + 60


@contextmanager
def role_stage(phase, *, schema, operation, prompt=None, page_index=None):
    from .runtime_policy import ROLE_MATCHING_RUNTIME_TURN_SECONDS
    started = time.monotonic()
    info = {"phase": phase, "operation": operation, "budget_seconds": ROLE_MATCHING_RUNTIME_TURN_SECONDS,
            **({"page_index": page_index} if page_index is not None else {})}
    token = _stage.set({**info, "started": started})
    _emit("role_stage_started", info)
    try:
        with deadline_scope(started + ROLE_MATCHING_RUNTIME_TURN_SECONDS):
            yield
    except BaseException as error:
        detail = getattr(error, "detail", {})
        error.detail = {**(_stage.get() or {}).get("native_progress", {}),
                        **(detail if isinstance(detail, dict) else {})}
        diagnostic = stage_failure(error, schema=schema, prompt=prompt, operation=operation, phase=phase,
            budget_seconds=ROLE_MATCHING_RUNTIME_TURN_SECONDS, elapsed_ms=int((time.monotonic() - started) * 1000))
        error.runtime_diagnostic = diagnostic
        _emit("role_stage_failed", diagnostic)
        raise
    else:
        _emit("role_stage_finished", {**info, "elapsed_ms": int((time.monotonic() - started) * 1000)})
    finally:
        _stage.reset(token)
