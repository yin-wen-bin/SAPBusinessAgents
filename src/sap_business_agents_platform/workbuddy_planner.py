from __future__ import annotations

import json
import copy
import tempfile
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Iterator

from jsonschema import ValidationError, validate

from .workbuddy_prompts import (
    AUTHOR_OUTPUT_SCHEMA,
    AGENT_FEEDBACK_OUTPUT_SCHEMA,
    WORKFLOW_COMPOSITION_OUTPUT_SCHEMA,
    WORKFLOW_REPAIR_OUTPUT_SCHEMA,
    WORKFLOW_REVIEW_OUTPUT_SCHEMA,
    _safe_json,
    _workflow_composition_prompt,
)
from .models import PlannerDecision
from .shared_planner import SharedPlanner
from .workbuddy_driver import WorkBuddySDKDriver
from .workbuddy_environment import WorkBuddyEnvironment, WorkBuddyError
from .workbuddy_supervisor import WorkBuddySupervisor
from .runtime_contract import RuntimeContractError
from .runtime_policy import operation_seconds


class WorkBuddyRuntimeError(RuntimeError):
    def __init__(self, message: str, *, code: str = "workbuddy_runtime_error", detail=None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


class WorkBuddyPlanner(SharedPlanner):
    provider_id = "workbuddy"
    """SDK-free adapter; owned workers enforce per-operation permission modes."""

    def __init__(
        self,
        repository_root: Path,
        model: str | None = None,
        *,
        request_timeout_ms: int | None = None,
        supervisor: WorkBuddySupervisor | None = None,
        runtime_snapshot: dict[str, Any] | None = None,
        tool_broker: Any = None,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.model = model
        self.reasoning_effort = None
        self.data_root = self.repository_root / ".local-data"
        self._driver = WorkBuddySDKDriver(self)
        self.request_timeout_ms = request_timeout_ms
        self.supervisor = supervisor or WorkBuddySupervisor(WorkBuddyEnvironment(repository_root))
        self.runtime_snapshot = runtime_snapshot or {}
        self.tool_broker = tool_broker
        self._authoring: ContextVar[dict | None] = ContextVar("workbuddy_authoring_options", default=None)
        self._operation: ContextVar[str | None] = ContextVar("workbuddy_operation", default=None)
        self._terminal_result: ContextVar[dict | None] = ContextVar("workbuddy_terminal_result", default=None)
        self._feedback_operation: ContextVar[str | None] = ContextVar("workbuddy_feedback_operation", default=None)
        self._feedback_cwd: ContextVar[Path | None] = ContextVar("workbuddy_feedback_cwd", default=None)
        self._event_sink: ContextVar[
            Callable[[str, dict[str, Any]], None] | None
        ] = ContextVar("workbuddy_event_sink", default=None)

    @contextmanager
    def bind_events(
        self, sink: Callable[[str, dict[str, Any]], None]
    ) -> Iterator[None]:
        token = self._event_sink.set(sink)
        try:
            yield
        finally:
            self._event_sink.reset(token)

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        sink = self._event_sink.get()
        if sink is not None:
            sink(event_type, {"provider_id": "workbuddy", **data})

    @property
    def _last_result(self):
        return self._terminal_result.get()

    @_last_result.setter
    def _last_result(self, value):
        self._terminal_result.set(value)

    def feedback_capabilities(self) -> dict[str, bool]:
        # SDK events are bridged, but native resume/steer/images have not been
        # validated for draft feedback and must not be advertised as available.
        return {"stream_events": True, "resume": False, "steer": False, "image_input": False}

    def workbuddy_reservation(self):
        return self.supervisor.reserve()

    def workbuddy_reserved(self):
        return self.supervisor.reserved

    async def _structured_turn(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        thread_id: str | None,
        system_prompt: str | None = None,
        allow_repair: bool = False,
        native_schema: bool = True,
    ) -> tuple[dict[str, Any], str]:
        if not native_schema:
            raise RuntimeContractError("runtime_native_schema_required")
        schema_prompt = (
            prompt
            + "\n\nReturn exactly one JSON object matching this JSON Schema:\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            + "\nDo not use Markdown fences or add explanatory text."
        )
        text, session_id = await self._query(
            schema_prompt, thread_id=thread_id, system_prompt=system_prompt,
            output_schema=schema,
        )
        try:
            from .runtime_diagnostics import checked_output
            raw = checked_output(text, schema, operation=self._operation.get(), output_format="native_json_schema")
        except (WorkBuddyError, RuntimeContractError) as exc:
            self._emit("workbuddy_output_rejected", exc.detail)
            raise WorkBuddyRuntimeError("WorkBuddy returned invalid structured output.",
                code=exc.code, detail=exc.detail) from exc
        except (ValueError, json.JSONDecodeError, ValidationError) as exc:
            # Never replay a native editing round just to repair terminal JSON.
            # Local edits are proposals until this one terminal passes validation.
            raise WorkBuddyRuntimeError(
                    "WorkBuddy returned invalid structured output.",
                    code="workbuddy_structured_output_invalid",
                ) from exc
        return raw, session_id

    async def author_workflow_v2(self, **kwargs: Any) -> dict[str, Any]:
        from .workbuddy_authoring import run
        return await run(self, **kwargs)




    async def _query(
        self, prompt: str, *, thread_id: str | None, system_prompt: str | None,
        output_schema: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        # Native continuation is unverified: carry supplied platform history, never SDK IDs.
        del thread_id
        snapshot = self.runtime_snapshot
        if not snapshot:
            from .workbuddy_manager import WorkBuddyManager
            snapshot = WorkBuddyManager(self.repository_root).runtime_snapshot(self.model)
        workspace = tempfile.TemporaryDirectory(prefix="sapba-workbuddy-turn-")
        from .runtime_contract import remaining_budget, require_deadline
        deadline = require_deadline()
        seconds = remaining_budget(None)
        try:
            payload = {"prompt": prompt, "system_prompt": system_prompt,
                       "cwd": str(self._feedback_cwd.get() or Path(workspace.name)),
                       "timeout_ms": int(seconds * 1000), "max_turns": 4}
            if output_schema is not None:
                payload["output_schema"] = output_schema
            authoring = self._authoring.get() or {}
            session = authoring.get("session")
            tool_handler = None
            if session and self.tool_broker:
                from .mcp_server import _AUTHORING_TOOLS
                payload["tools"] = _AUTHORING_TOOLS
                async def tool_handler(name, arguments):
                    return await self.tool_broker.handle(session["operation_id"], session["capability"], name, arguments)
            if authoring:
                payload["max_turns"] = 50
            task_id = self._feedback_operation.get() or uuid.uuid4().hex
            scope = self._driver.turn_owner.get()
            if scope is not None:
                scope["jobs"].add(task_id)
            # Input fingerprints aid stage diagnosis without saving prompts,
            # private paths, native commands or business rows.
            if self._operation.get() in {"analyze_role_matching", "review_role_matching_feedback"}:
                import hashlib
                encoded_prompt = prompt.encode("utf-8")
                self._emit("workbuddy_request_prepared", {"operation": self._operation.get(),
                    "input_length": len(encoded_prompt), "input_sha256": hashlib.sha256(encoded_prompt).hexdigest(),
                    "budget_ms": int(seconds * 1000)})
            try:
                result = await self.supervisor.run(task_id=task_id,
                    snapshot=snapshot, operation=self._operation.get(), payload=payload,
                    seconds=seconds, deadline=deadline,
                    mode=authoring.get("mode", "bounded"), tool_handler=tool_handler,
                    emit=self._event_sink.get())
            except WorkBuddyError as exc:
                self._emit("workbuddy_execution_failed", {"operation": self._operation.get(),
                    "phase": "terminal_output" if exc.code == "workbuddy_structured_output_missing" else "execution",
                    "failure_code": exc.code, "output_format": "native_missing" if exc.code == "workbuddy_structured_output_missing" else None,
                    "output_length": None, "output_sha256": None, **getattr(exc, "detail", {})})
                raise WorkBuddyRuntimeError(str(exc), code=exc.code, detail=getattr(exc, "detail", {})) from exc
        finally:
            try:
                workspace.cleanup()
            except OSError:
                # The owned process supervisor decides cleanup qualification.
                # Filesystem cleanup must never replace a timeout/contract cause.
                self._emit("workbuddy_workspace_cleanup_failed", {"operation": self._operation.get(),
                    "failure_code": "workbuddy_workspace_cleanup_failed"})
        if output_schema is not None and result.get("output_format") != "native_json_schema":
            raise WorkBuddyRuntimeError("The worker did not return native structured output.",
                                        code="workbuddy_structured_output_missing")
        self._last_result = result
        return str(result["text"]), str(result.get("session_id") or "platform-context")

    async def abort_agent_feedback(self, operation_id: str) -> bool:
        return await self.supervisor.cancel(operation_id)

    async def cancel(self, thread_id: str | None = None) -> None:
        # A session ID is not process ownership. Caller must provide its operation ID.
        if thread_id:
            await self.supervisor.cancel(thread_id)


def _bind_operation(name: str, method: Any) -> Any:
    async def call(self: WorkBuddyPlanner, *args: Any, **kwargs: Any) -> Any:
        from .shared_planner import _operation_context
        token = self._operation.set(_operation_context.get() or name)
        try:
            return await method(self, *args, **kwargs)
        except RuntimeContractError as exc:
            raise WorkBuddyRuntimeError("The shared output contract rejected the result.",
                code=exc.code, detail=exc.detail) from exc
        except ValueError as exc:
            code = str(exc)
            if not code.replace("_", "").isalnum() or len(code) > 100:
                code = "runtime_report_validation_failed"
            raise WorkBuddyRuntimeError("The shared output contract rejected the result.", code=code) from exc
        finally:
            self._operation.reset(token)
    return call


for _name in ("author_draft", "review_agent_feedback",
              "compose_workflow", "review_workflow", "repair_workflow", "review_free_query_feedback",
              "revise_free_query_presentation", "review_workflow_feedback", "analyze_role_matching",
              "review_role_matching_feedback"):
    setattr(WorkBuddyPlanner, _name, _bind_operation(_name, getattr(WorkBuddyPlanner, _name)))
