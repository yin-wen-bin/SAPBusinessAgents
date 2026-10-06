from __future__ import annotations

import json
import copy
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Iterator

from jsonschema import ValidationError, validate

from .workbuddy_prompts import (
    AUTHOR_OUTPUT_SCHEMA,
    AGENT_FEEDBACK_OUTPUT_SCHEMA,
    PLANNER_OUTPUT_SCHEMA,
    SUMMARY_OUTPUT_SCHEMA,
    WORKFLOW_COMPOSITION_OUTPUT_SCHEMA,
    WORKFLOW_REPAIR_OUTPUT_SCHEMA,
    WORKFLOW_REVIEW_OUTPUT_SCHEMA,
    _decode_plan_json,
    _grounding_prompt,
    _planner_prompt,
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
    """SDK-free adapter; owned workers enforce per-operation permission modes."""

    def __init__(
        self,
        repository_root: Path,
        model: str | None = None,
        *,
        request_timeout_ms: int = 120_000,
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
        self._operation: ContextVar[str] = ContextVar("workbuddy_operation", default="plan")
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

    async def review_agent_feedback(self, *, feedback: str, locale: str, package: dict[str, Any],
                                    history: list[dict[str, Any]] | None = None, thread_id: str | None = None,
                                    operation_id: str | None = None, intent: str = "revise",
                                    feedback_context: dict[str, Any] | None = None,
                                    tool_policy: Any = None, tool_session: Any = None,
                                    image_inputs: list[str] | None = None) -> dict[str, Any]:
        from .runtime_agent_authoring import feedback_prompt, decode_feedback
        # Native session resume/images are not verified for this driver. Context
        # remains platform-owned; an SDK handle never changes the authorization.
        del thread_id
        if not self.model:
            raise WorkBuddyRuntimeError("An explicit model is required.", code="agent_runtime_binding_missing")
        if image_inputs:
            raise WorkBuddyRuntimeError("Images are unsupported.", code="agent_feedback_image_unsupported")
        from .agent_feedback_context import safe_context
        feedback, feedback_context = safe_context(feedback), safe_context(feedback_context or {})
        try:
            feedback_prompt(self.repository_root, feedback=feedback, locale=locale, package=package,
                history=history, intent=intent, feedback_context=feedback_context)
        except ValueError as exc:
            raise WorkBuddyRuntimeError("Context exceeds the bound.", code=str(exc)) from None
        trusted = bool(intent == "revise" and tool_policy and tool_policy.get("mode") == "trusted_local")
        if tool_policy and not trusted and intent != "explain":
            raise WorkBuddyRuntimeError("Restricted mode is not verified.", code="workbuddy_windows_restricted_unverified")
        workspace = None
        if trusted:
            import uuid
            from .authoring_workspace import AuthoringWorkspace
            workspace = AuthoringWorkspace(self.repository_root,
                self.repository_root / ".local-data/workbuddy-authoring" / uuid.uuid4().hex)
            workspace.prepare(package, current_source=True)
            workspace.full_access = True
            workspace.read_only_source = True
            (workspace.source / ".authoring-tmp").mkdir(exist_ok=True)
        prompt = feedback_prompt(self.repository_root, feedback=feedback, locale=locale,
            package=package, history=history, intent=intent, feedback_context=feedback_context,
            tool_workspace=workspace, full_access=trusted, tool_session=tool_session, package_in_files=trusted)
        operation_token = self._feedback_operation.set(operation_id)
        with tempfile.TemporaryDirectory(prefix="sapba-workbuddy-feedback-") as isolated:
            cwd_token = self._feedback_cwd.set(workspace.source if workspace else Path(isolated))
            auth_token = self._authoring.set({"mode": "trusted_local", "session": tool_session} if trusted else None)
            try:
                async def turn(candidate_package, issues=(), remaining=None):
                    from .runtime_contract import deadline_scope
                    import time
                    with deadline_scope(time.monotonic() + remaining if remaining is not None else None):
                        raw, handle = await self._structured_turn(
                            prompt + ("\nController check failures: " + json.dumps(issues) if issues else ""),
                            AGENT_FEEDBACK_OUTPUT_SCHEMA, thread_id=None,
                            system_prompt=("Trusted local Agent authoring. No production writes; SAP via authorized platform tools only."
                                if trusted else "Draft-only feedback. Only the local StructuredOutput formatter is allowed. Never access SAP or the checkout."),
                            native_schema=True, allow_repair=False)
                    candidate = None
                    if workspace:
                        workspace._check_links_and_size()
                        if workspace.platform_changes():
                            raise WorkBuddyRuntimeError("Agent-only feedback changed platform source.", code="workbuddy_authoring_scope_invalid")
                        # Common decoder checks mixed file/JSON edits and identity.
                        candidate = workspace.read_package(exclude_test_artifacts=True)
                    return decode_feedback(raw, candidate_package, handle,
                        tool_workspace=workspace, file_package=candidate, explain_only=intent == "explain")
                if workspace:
                    from .runtime_agent_authoring import repair_feedback
                    checks = []
                    result = await repair_feedback(package, workspace, turn, checks=checks)
                    result["harness"] = {"mode": "trusted_local", "checks": checks,
                        "workspace_id": workspace.root.name, "preflight": "operation_validation_required",
                        "live_testing": "not_performed", "platform_apply": "not_performed"}
                    return result
                return await turn(package)
            except ValueError as exc:
                raise WorkBuddyRuntimeError("Draft response failed the shared contract.", code=str(exc)) from None
            finally:
                self._authoring.reset(auth_token)
                self._feedback_cwd.reset(cwd_token)
                self._feedback_operation.reset(operation_token)








    async def _structured_turn(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        thread_id: str | None,
        system_prompt: str | None = None,
        allow_repair: bool = True,
        native_schema: bool = False,
    ) -> tuple[dict[str, Any], str]:
        schema_prompt = (
            prompt
            + "\n\nReturn exactly one JSON object matching this JSON Schema:\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            + "\nDo not use Markdown fences or add explanatory text."
        )
        text, session_id = await self._query(
            schema_prompt, thread_id=thread_id, system_prompt=system_prompt,
            output_schema=schema if native_schema else None,
        )
        try:
            if native_schema:
                from .workbuddy_diagnostics import checked_output
                raw = checked_output(text, schema, operation=self._operation.get(), output_format="native_json_schema")
            else:
                raw = _parse_json_object(text)
                validate(instance=raw, schema=schema)
        except WorkBuddyError as exc:
            self._emit("workbuddy_output_rejected", exc.detail)
            raise WorkBuddyRuntimeError("WorkBuddy returned invalid structured output.",
                code="workbuddy_structured_output_invalid", detail=exc.detail) from exc
        except (ValueError, json.JSONDecodeError, ValidationError) as exc:
            # Never replay a native editing round just to repair terminal JSON.
            # Local edits are proposals until this one terminal passes validation.
            if not allow_repair or native_schema:
                raise WorkBuddyRuntimeError(
                    "WorkBuddy returned invalid structured output.",
                    code="workbuddy_structured_output_invalid",
                ) from exc
            return await self._structured_turn(
                (
                    "Your previous response failed JSON Schema validation. Re-emit the same "
                    "answer, changing only JSON syntax and required field shape.\n"
                    + schema_prompt + "\nPrevious response:\n" + text
                ),
                schema,
                thread_id=session_id,
                system_prompt=system_prompt,
                allow_repair=False,
            )
        return raw, session_id

    async def author_workflow_v2(self, **kwargs: Any) -> dict[str, Any]:
        from .workbuddy_authoring import run
        return await run(self, **kwargs)




    def _workflow_output(self, text, schema, path):
        from .workbuddy_workflow_contract import decode
        try:
            return decode(text, schema, operation=self._operation.get(), path=path)
        except WorkBuddyError as exc:
            self._emit("workbuddy_output_rejected", exc.detail)
            raise WorkBuddyRuntimeError("Invalid workflow JSON contract.",
                code="workbuddy_structured_output_invalid", detail=exc.detail) from exc

    def _embedded_output(self, text, kind, path):
        from .workbuddy_diagnostics import checked_output
        try:
            return checked_output(text, {"type": kind}, operation=self._operation.get(), output_format="embedded_json")
        except WorkBuddyError as exc:
            for issue in exc.detail["validation_issues"]:
                issue["path"] = path
            self._emit("workbuddy_output_rejected", exc.detail)
            raise WorkBuddyRuntimeError("Invalid embedded JSON.", code="workbuddy_structured_output_invalid", detail=exc.detail) from exc



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
        seconds = operation_seconds("workbuddy", self._operation.get(), existing=self.request_timeout_ms / 1000)
        from .runtime_contract import remaining_budget
        seconds = remaining_budget(seconds)
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
            try:
                result = await self.supervisor.run(task_id=self._feedback_operation.get(),
                    snapshot=snapshot, operation=self._operation.get(), payload=payload,
                    seconds=seconds,
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
            if name == "review_workflow_feedback":
                defaults = {"requirement": "", "feedback": "", "feedback_type_hint": None,
                            "locale": "zh", "workflow": {}, "previous_proposal": {},
                            "catalog": {}, "validation_report": None, "thread_id": None}
                kwargs = {**defaults, **kwargs}
            return await method(self, *args, **kwargs)
        except RuntimeContractError as exc:
            raise WorkBuddyRuntimeError("The shared output contract rejected the result.",
                code="workbuddy_structured_output_invalid", detail=exc.detail) from exc
        finally:
            self._operation.reset(token)
    return call


for _name in ("plan", "ground_plan", "summarize", "author_draft", "review_agent_feedback",
              "compose_workflow", "review_workflow", "repair_workflow", "review_free_query_feedback",
              "revise_free_query_presentation", "review_workflow_feedback", "analyze_role_matching",
              "review_role_matching_feedback"):
    setattr(WorkBuddyPlanner, _name, _bind_operation(_name, getattr(WorkBuddyPlanner, _name)))


def _parse_json_object(value: str) -> dict[str, Any]:
    text = value.strip()
    fence = chr(96) * 3
    if text.startswith(fence) and text.endswith(fence):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            raise
        payload, end = json.JSONDecoder().raw_decode(text[start:])
        if text[start + end :].strip():
            raise ValueError("Structured output contains trailing text.")
    if not isinstance(payload, dict):
        raise ValueError("Structured output is not an object.")
    return payload
