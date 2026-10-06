"""SDK-free execution contracts; discovering capabilities never grants access."""
from __future__ import annotations

import copy
import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from contextvars import ContextVar
from contextlib import contextmanager
from typing import Any, Callable, Protocol


_execution_deadline = ContextVar("runtime_execution_deadline", default=None)


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
    cleanup_complete: bool = True

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
    if emit:
        emit(RuntimeEvent(request.provider_id, request.operation, request.stage, "stage_started"))
    try:
        with deadline_scope(request.deadline):
            async with asyncio.timeout(remaining):
                result = await driver._execute(request)
        request.remaining()
        result.final_response  # Reject incomplete cleanup / nonterminal output.
        result.session.require_provider(request.provider_id)
        if emit:
            emit(RuntimeEvent(request.provider_id, request.operation, request.stage, "completed"))
        return result
    except TimeoutError:
        raise RuntimeContractError("runtime_deadline_exceeded") from None
