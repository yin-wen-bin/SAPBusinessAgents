"""SDK-free execution contracts; discovering capabilities never grants access."""
from __future__ import annotations

import copy
import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from contextvars import ContextVar
from contextlib import contextmanager, asynccontextmanager, nullcontext
from typing import Any, Callable, Protocol


_execution_deadline = ContextVar("runtime_execution_deadline", default=None)
_cleanup_deadline = ContextVar("runtime_cleanup_deadline", default=None)


def current_deadline():
    return _execution_deadline.get()


def require_deadline():
    value = current_deadline()
    if value is None:
        raise RuntimeContractError("runtime_budget_binding_missing")
    remaining_budget(None)
    return value


def cleanup_deadline():
    value = _cleanup_deadline.get()
    return value["deadline"] if value and value.get("deadline") is not None else time.monotonic() + 10


@contextmanager
def cleanup_scope(deadline=None):
    inherited = _cleanup_deadline.get()
    deadline = deadline if deadline is not None else time.monotonic() + 10
    effective = min(inherited["deadline"], deadline) if inherited and inherited.get("deadline") is not None else deadline
    token = _cleanup_deadline.set({"deadline": effective})
    try:
        yield effective
    finally:
        _cleanup_deadline.reset(token)


@asynccontextmanager
async def business_scope(owner, operation, *, seconds=None, provider_id=None):
    """Capacity precedes the first clock; inherited deadlines never restart."""
    from .runtime_policy import operation_seconds, require_operation
    require_operation(operation)
    reserve = getattr(owner, "workbuddy_reservation", None)
    provider_id = provider_id or getattr(getattr(owner, "_driver", None), "provider_id", None) or getattr(owner, "provider_id", None) or getattr(owner, "current_provider_id", None)
    async with reserve() if provider_id == "workbuddy" and callable(reserve) else nullcontext():
        # Role matching has bounded model stages, not a new job-wide timeout.
        if operation in {"analyze_role_matching", "review_role_matching_feedback"}:
            yield current_deadline()
            return
        seconds = operation_seconds(provider_id, operation, existing=seconds)
        with deadline_scope(time.monotonic() + seconds):
            yield require_deadline()


async def await_business(awaitable, *, deadline=None):
    """Bound execution without trusting cancellation to bound SDK shutdown."""
    with deadline_scope(deadline):
        limit = require_deadline()
        # A mutable, initially inactive binding is inherited by the child. At
        # cancellation both the supervisor and SDK close share one cutoff.
        inherited = _cleanup_deadline.get()
        cleanup = inherited if inherited is not None else {"deadline": None}
        token = _cleanup_deadline.set(cleanup)
        try:
            task = asyncio.ensure_future(awaitable)
            primary = None
            try:
                done, _ = await asyncio.wait({task}, timeout=max(0, limit - time.monotonic()))
                if done:
                    result = task.result()
                    remaining_budget(None)
                    return result
                primary = RuntimeContractError("runtime_deadline_exceeded")
            except asyncio.CancelledError as exc:
                primary = exc
            cutoff = time.monotonic() + 10
            if cleanup.get("deadline") is not None:
                cutoff = min(cutoff, cleanup["deadline"])
            cleanup["deadline"] = cutoff
            task.cancel()
            try:
                done, _ = await asyncio.wait({task}, timeout=max(0, cutoff - time.monotonic()))
            except asyncio.CancelledError:
                task.add_done_callback(_consume_task)
                primary.detail = dict(getattr(primary, "detail", {}) or {},
                                      cleanup_failure_code="runtime_cleanup_incomplete")
                raise primary
            if not done:
                task.add_done_callback(_consume_task)
                detail = dict(getattr(primary, "detail", {}) or {})
                detail["cleanup_failure_code"] = "runtime_cleanup_incomplete"
                primary.detail = detail
            else:
                try:
                    task.result()
                except BaseException as exc:
                    if (getattr(exc, "code", None) == "runtime_cleanup_incomplete"
                            or getattr(exc, "detail", {}).get("cleanup_failure_code")):
                        primary.detail = dict(getattr(primary, "detail", {}) or {},
                                              cleanup_failure_code="runtime_cleanup_incomplete")
            raise primary
        finally:
            _cleanup_deadline.reset(token)


def _consume_task(task):
    try:
        task.result()
    except BaseException:
        pass


@contextmanager
def deadline_scope(deadline):
    inherited = _execution_deadline.get()
    effective = min(inherited, deadline) if inherited is not None and deadline is not None else inherited if deadline is None else deadline
    token = _execution_deadline.set(effective)
    try:
        yield effective
    finally:
        _execution_deadline.reset(token)


def remaining_budget(seconds):
    deadline = _execution_deadline.get()
    if deadline is None:
        return seconds
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeContractError("runtime_deadline_exceeded")
    return min(seconds, remaining) if seconds is not None else remaining


class Sandbox(str, Enum):
    read_only = "read_only"
    full_access = "full_access"
    workspace_write = "workspace_write"


class ApprovalMode(str, Enum):
    deny_all = "deny_all"


@dataclass(frozen=True)
class RuntimeSession:
    provider_id: str
    session_id: str

    def require_provider(self, provider_id: str) -> str:
        if self.provider_id != provider_id:
            raise RuntimeContractError("runtime_session_provider_mismatch")
        return self.session_id


class RuntimeContractError(ValueError):
    def __init__(self, code: str, issues: list[dict[str, Any]] | None = None):
        self.code = code
        self.detail = {"validation_issues": issues or []}
        super().__init__(code)


@dataclass(frozen=True)
class RuntimeRequest:
    provider_id: str
    operation: str
    stage: str
    binding: dict[str, Any]
    prompt: Any
    output_schema: dict[str, Any]
    cwd: str | None = None
    instructions: str | None = None
    permissions: dict[str, Any] = field(default_factory=dict)
    tools: tuple[dict[str, Any], ...] = ()
    session: RuntimeSession | None = None
    deadline: float | None = None
    tool_session: dict[str, Any] = field(default_factory=dict, repr=False)
    context: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        inherited = _execution_deadline.get()
        if inherited is not None:
            object.__setattr__(self, "deadline", min(inherited, self.deadline) if self.deadline is not None else inherited)
        if self.session is not None:
            self.session.require_provider(self.provider_id)
        if self.binding.get("provider_id", self.provider_id) != self.provider_id:
            raise RuntimeContractError("runtime_binding_provider_mismatch")
        # Own request data so model output and later defaults cannot mutate it.
        for name in ("binding", "prompt", "output_schema", "permissions", "tools", "tool_session", "context"):
            object.__setattr__(self, name, copy.deepcopy(getattr(self, name)))

    def remaining(self) -> float | None:
        if self.deadline is None:
            return None
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise RuntimeContractError("runtime_deadline_exceeded")
        return value


@dataclass(frozen=True)
class RuntimeResult:
    output: dict[str, Any]
    session: RuntimeSession
    status: str = "completed"
    actual_model: str | None = None
    diagnostics: tuple[dict[str, Any], ...] = ()
    cleanup_complete: bool = False

    def __post_init__(self):
        if not isinstance(self.output, dict):
            raise RuntimeContractError("runtime_structured_output_invalid")
        object.__setattr__(self, "output", copy.deepcopy(self.output))

    @property
    def final_response(self) -> str:
        if self.status != "completed" or not self.cleanup_complete:
            raise RuntimeContractError("runtime_result_not_complete")
        return json.dumps(self.output, ensure_ascii=False)


@dataclass(frozen=True)
class RuntimeEvent:
    provider_id: str
    operation: str
    stage: str
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeTurn:
    """Validated terminal data inside an owned client, before shutdown."""
    output: dict[str, Any]
    session: RuntimeSession
    actual_model: str | None = None

    @property
    def final_response(self):
        return json.dumps(self.output, ensure_ascii=False)

    def result(self, *, cleanup_complete):
        return RuntimeResult(self.output, self.session, actual_model=self.actual_model,
                             cleanup_complete=cleanup_complete)


class RuntimeDriver(Protocol):
    provider_id: str

    def client(self, **options: Any) -> Any: ...

    async def execute(self, request: RuntimeRequest, *, emit: Callable | None = None) -> RuntimeResult: ...


def session_id(value: str | RuntimeSession, provider_id: str) -> str:
    return value.require_provider(provider_id) if isinstance(value, RuntimeSession) else value


async def execute_frozen(driver, request: RuntimeRequest, *, emit=None):
    """Enforce one caller-issued deadline; drivers cannot replenish its budget."""
    if request.provider_id != driver.provider_id:
        raise RuntimeContractError("runtime_binding_provider_mismatch")
    remaining = request.remaining()
    if remaining is None:
        raise RuntimeContractError("runtime_budget_binding_missing")
    if emit:
        emit(RuntimeEvent(request.provider_id, request.operation, request.stage, "stage_started"))
    try:
        with deadline_scope(request.deadline):
            result = await await_business(driver._execute(request))
        request.remaining()
        result.final_response  # Reject incomplete cleanup / nonterminal output.
        result.session.require_provider(request.provider_id)
        if emit:
            emit(RuntimeEvent(request.provider_id, request.operation, request.stage, "completed"))
        return result
    except TimeoutError:
        raise RuntimeContractError("runtime_deadline_exceeded") from None
