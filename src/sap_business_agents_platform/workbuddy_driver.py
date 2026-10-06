"""SDK-free WorkBuddy transport, using only its owned isolated worker."""
from __future__ import annotations

import json
import uuid
import copy
from collections import OrderedDict
from typing import Any

from .runtime_contract import RuntimeRequest, RuntimeResult, RuntimeSession, RuntimeContractError, session_id, execute_frozen


class WorkBuddySDKDriver:
    provider_id = "workbuddy"

    def __init__(self, owner: Any):
        self.owner = owner
        self.sessions: dict[str, dict[str, Any]] = OrderedDict()

    def bind_session(self, key, state):
        self.sessions[key] = state
        self.sessions.move_to_end(key)
        while len(self.sessions) > 256:
            self.sessions.popitem(last=False)

    def client(self, **options: Any) -> Any:
        return WorkBuddyClient(self, options)

    def workflow_options(self, mode):
        if mode != "full_access":
            from .workbuddy_environment import WorkBuddyError
            raise WorkBuddyError("workbuddy_windows_restricted_unverified")
        return {"directory": "workbuddy-authoring", "snapshot_options": {"current_source": True}}

    async def workflow_turn(self, request, *, workspace, task_id, emit, cleanup_state):
        from .workbuddy_authoring import capabilities, _safe_progress
        from .workbuddy_environment import WorkBuddyError
        from .runtime_policy import operation_seconds
        owner = self.owner
        emit("research", {"provider": "workbuddy", "context_mode": "platform_history"})
        remaining = request.remaining()
        seconds = operation_seconds("workbuddy", request.operation, existing=3600)
        seconds = min(seconds, remaining) if remaining is not None else seconds
        try:
            response = await owner.supervisor.run(task_id=task_id, snapshot=request.binding,
                operation=request.operation, payload={"system_prompt": request.instructions, "cwd": request.cwd,
                    "output_schema": request.output_schema, "prompt": request.prompt},
                mode="trusted_local", seconds=seconds,
                emit=lambda kind, data: emit("tool_progress", {"kind": kind, "provider": "workbuddy",
                    **_safe_progress(kind, data)}))
        finally:
            cleanup_state["complete"] = not owner.supervisor.reconcile()
        if response.get("output_format") != "native_json_schema":
            emit("validation_failed", {"code": "workbuddy_native_schema_unavailable", "constraint": "native_json_schema"})
            raise WorkBuddyError("workbuddy_native_schema_unavailable")
        from .runtime_diagnostics import checked_output
        raw = checked_output(response.get("text"), request.output_schema,
            operation=request.operation, output_format="native_json_schema")
        return RuntimeResult(raw, RuntimeSession("workbuddy", response.get("session_id") or task_id or "platform-context"),
            actual_model=response.get("actual_model"), cleanup_complete=cleanup_state["complete"]), capabilities("full_access")

    async def execute(self, request: RuntimeRequest, *, emit=None) -> RuntimeResult:
        return await execute_frozen(self, request, emit=emit)

    async def _execute(self, request):
        async with self.client() as client:
            options = {"cwd": request.cwd, "model": request.binding.get("model"), **request.permissions}
            thread = await client.thread_resume(request.session, **options) if request.session else await client.thread_start(
                **options, developer_instructions=request.instructions)
            return await thread.run(request.prompt, output_schema=request.output_schema)


class WorkBuddyClient:
    def __init__(self, driver, options=None):
        self.driver, self.options = driver, options or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def thread_start(self, **options):
        options = {**self.options, **options}
        key = "workbuddy:" + uuid.uuid4().hex
        state = {"options": options, "history": [], "index": 0,
                 "binding": copy.deepcopy(self.driver.owner.runtime_snapshot), "model": self.driver.owner.model}
        self.driver.bind_session(key, state)
        return WorkBuddyThread(self.driver, key, state)

    async def thread_resume(self, handle, **options):
        options = {**self.options, **options}
        key = session_id(handle, "workbuddy")
        # Legacy IDs are opaque and are never passed to a different SDK.
        # Platform-supplied operation context remains authoritative after reconnect.
        if isinstance(key, str) and key.startswith("thr_"):
            raise ValueError("runtime_session_provider_mismatch")
        state = self.driver.sessions.get(key)
        if state and (state["binding"] != self.driver.owner.runtime_snapshot or state["model"] != self.driver.owner.model):
            from .runtime_contract import RuntimeContractError
            raise RuntimeContractError("runtime_session_binding_changed")
        if state is None:
            # Reconnection carries platform context; never native cross-SDK resume.
            # Legacy native IDs are accepted only as compatibility input. They
            # cannot become platform keys: identical native IDs from unrelated
            # workers must never share history or imply an SDK resume capability.
            if not isinstance(key, str) or not key.startswith("workbuddy:"):
                key = "workbuddy:" + uuid.uuid4().hex
            state = {"options": options, "history": [], "index": 0,
                "binding": copy.deepcopy(self.driver.owner.runtime_snapshot), "model": self.driver.owner.model}
        self.driver.bind_session(key, state)
        state["options"].update(options)
        return WorkBuddyThread(self.driver, key, state)


class WorkBuddyThread:
    def __init__(self, driver, key, state):
        self.driver, self.id, self.state = driver, key, state

    async def run(self, prompt, *, output_schema, effort=None, **options):
        owner = self.driver.owner
        config = self.state["options"]
        self.state["index"] += 1
        request = RuntimeRequest("workbuddy", owner._operation.get(), f"turn-{self.state['index']}",
            owner.runtime_snapshot or {"provider_id": "workbuddy", "model": owner.model},
            prompt, output_schema, cwd=config.get("cwd"), instructions=config.get("developer_instructions"),
            permissions={key: value for key, value in config.items() if key in {"sandbox", "approval_mode"}},
            session=RuntimeSession("workbuddy", self.id))
        request.remaining()
        content = request.prompt
        if config.get("format_instructions"):
            content += "\n\n" + config["format_instructions"]
        if self.state["history"]:
            content += "\n\nPrevious turns of this platform-owned operation (context, not SAP evidence):\n" + json.dumps(
                self.state["history"], ensure_ascii=False)
        from .runtime_role_contract import native_output_schema, canonical_output
        wire_schema = native_output_schema(request.output_schema)
        if wire_schema != request.output_schema:
            content += (
                "\n\nNative transport encoding only: analysis_json is a JSON object matching the "
                "supplied schema, not an escaped JSON string. Preserve the current business phase "
                "and requirements; the platform serializes this object into its legacy envelope."
            )
        raw, returned_id = await owner._structured_turn(content, wire_schema, thread_id=self.id,
            system_prompt=request.instructions, native_schema=True, allow_repair=False)
        try:
            raw = canonical_output(raw, request.output_schema, operation=request.operation)
        except RuntimeContractError as exc:
            owner._emit("workbuddy_output_rejected", exc.detail)
            raise
        # Native IDs are metadata, not a platform conversation key. Two workers
        # may report identical IDs; they must never merge unrelated histories.
        self.state["native_session_id"] = returned_id
        self.state["history"].append({"prompt": prompt, "output": raw})
        if len(json.dumps(self.state["history"], ensure_ascii=False).encode("utf-8")) > 524288:
            from .runtime_contract import RuntimeContractError
            self.driver.sessions.pop(self.id, None)
            raise RuntimeContractError("runtime_context_too_large")
        self.driver.bind_session(self.id, self.state)
        return RuntimeResult(raw, RuntimeSession("workbuddy", self.id),
                             actual_model=(getattr(owner, "_last_result", {}) or {}).get("actual_model"))


class WorkBuddySampleDriver:
    provider_id = "workbuddy"

    def __init__(self, manager):
        self.manager = manager

    async def interrupt_sample(self, service, run_id):
        await self.manager.supervisor.cancel(run_id)

    async def sample_turn(self, service, run_id, context, capability, binding):
        import time
        from .sample_discovery import sample_output_schema
        from .workbuddy_sample import sample_prompt
        from .mcp_server import _SAP_TOOLS
        from .workbuddy_environment import WorkBuddyError
        from .runtime_diagnostics import checked_output, output_diagnostic
        workspace = service.settings.data_root / "workbuddy-harness" / run_id / "sample"
        workspace.mkdir(parents=True, exist_ok=True)
        async def tool(name, arguments):
            return await service.broker.handle(run_id, capability, name, arguments)
        try:
            result = await self.manager.supervisor.run(task_id=run_id, snapshot=binding, operation="sample_discovery",
                payload={"system_prompt": sample_prompt(context, binding),
                    "prompt": "Find a verifiable real input sample within the declared scope.\n" + json.dumps({"output_schema": sample_output_schema()}),
                    "cwd": str(workspace), "tools": _SAP_TOOLS, "output_schema": sample_output_schema(),
                    "timeout_ms": int(context.remaining() * 1000)},
                seconds=context.remaining(), tool_handler=tool,
                emit=lambda kind, data: service.store.append_event(run_id, kind, data))
            service.store.append_event(run_id, "workbuddy_terminal_output", output_diagnostic(result.get("text"),
                operation="sample_discovery", output_format=result.get("output_format")))
            if result.get("output_format") != "native_json_schema":
                raise WorkBuddyError("workbuddy_structured_output_missing")
            checked_output(result.get("text"), sample_output_schema(), operation="sample_discovery",
                output_format="native_json_schema")
            return result["text"]
        except WorkBuddyError as error:
            if error.code == "workbuddy_deadline_exceeded":
                raise TimeoutError from error
            if error.code == "workbuddy_cancelled":
                import asyncio
                raise asyncio.CancelledError from error
            raise
        finally:
            remaining = max(0, self.manager.supervisor.cleanup_deadlines.get(run_id, time.monotonic() + 10) - time.monotonic())
            if not await service.broker.cancel_tools(run_id, timeout=remaining) or self.manager.supervisor.reconcile():
                raise WorkBuddyError("runtime_cleanup_incomplete")


class WorkBuddyHarnessDriver:
    """Owned-worker codec over the common Harness lifecycle."""
    provider_id = "workbuddy"
    engineering_copy = False

    def __init__(self, owner):
        self.owner = owner

    def binding(self, run_id, state, model, reasoning_effort):
        snapshot = self.owner.manager.bound_snapshot(
            self.owner.store.get_run(run_id).runtime.model_dump(mode="json"),
            formal=bool(state.get("acceptance_spec")))
        operation = "acceptance_baseline" if state.get("acceptance_direct_baseline") is True else "acceptance_free_query" if state.get("acceptance_spec") else "free_query"
        self.owner.manager.supervisor.check_operation(snapshot, operation)
        return snapshot

    async def cleanup(self, run_id, context):
        import time
        from .workbuddy_environment import WorkBuddyError
        supervisor = self.owner.manager.supervisor
        remaining = max(0, supervisor.cleanup_deadlines.get(run_id, time.monotonic() + 10) - time.monotonic())
        context["cleanup_state"]["started"] = True
        if not await self.owner.broker.cancel_tools(run_id, timeout=remaining) or supervisor.reconcile():
            self.owner.store.update_harness_state(run_id, {"cleanup_incomplete": True})
            raise WorkBuddyError("runtime_cleanup_incomplete")
        context["cleanup_state"]["complete"] = True

    async def harness_turn(self, request, context):
        import copy
        from .runtime_contract import RuntimeContractError
        from .runtime_harness import tool_call
        from .runtime_query_contract import PLAN_SCHEMA
        from .runtime_diagnostics import output_diagnostic
        from .workbuddy_harness import parse_output
        from .workbuddy_environment import WorkBuddyError
        from .workbuddy_compat import translate
        from .harness import AcceptanceReportValidationError
        from .mcp_server import _SAP_TOOLS, _TOOL_TOOLS
        owner, run_id = self.owner, context["run_id"]
        tools = copy.deepcopy(_SAP_TOOLS if request.permissions["acceptance_direct_baseline"] else _SAP_TOOLS + _TOOL_TOOLS)
        for tool in tools:
            if tool["name"] in {"sap_query_validate", "sap_query_execute"}:
                tool["inputSchema"]["properties"]["plan"] = PLAN_SCHEMA
        async def handle(name, arguments):
            try:
                result = await tool_call(owner, context, name, arguments)
            except RuntimeContractError as error:
                failure = translate(error)
                owner.store.append_event(run_id, "workbuddy_tool_rejected", {"tool": name, "code": failure.code, **failure.detail})
                return {"ok": False, "code": failure.code, **failure.detail}
            if (owner.store.get_harness_state(run_id).get("acceptance_validation_failure") or {}).get("terminal") is True:
                result = {**result, "_workbuddy_stop_after_response": True}
            return result
        snapshot = {k: v for k, v in request.binding.items()
                    if k not in {"workspace_id", "candidate_access", "base_digest", "base_commit"}}
        # provider_id is omitted only when a historical snapshot did not carry it.
        original = owner.store.get_run(run_id).runtime.model_dump(mode="json")
        if "provider_id" not in original:
            snapshot.pop("provider_id", None)
        owner.store.append_event(run_id, "harness_started", {"runtime": "workbuddy_worker",
            "protocol": "agent_runtime.v2", "web_search": False,
            "acceptance_direct_baseline": request.permissions["acceptance_direct_baseline"],
            "turn_count": context["turn_count"]})
        try:
            response = await owner.manager.supervisor.run(task_id=run_id, snapshot=snapshot,
                operation=request.operation, payload={"prompt": request.prompt + ("\nPlatform-owned conversation context:\n" +
                    json.dumps(request.context, ensure_ascii=False) if request.context else ""),
                    "system_prompt": (request.instructions or "") + "\nSDK terminal encoding: use StructuredOutput with the entire frozen output object, not a fragment.",
                    "cwd": request.cwd, "tools": tools, "output_schema": request.output_schema,
                    "timeout_ms": int(request.remaining() * 1000)}, seconds=request.remaining(),
                tool_handler=handle, emit=lambda kind, data: owner.store.append_event(run_id, kind, data))
            owner.store.append_event(run_id, "workbuddy_terminal_output", output_diagnostic(response.get("text"),
                operation=request.operation, output_format=response.get("output_format")))
            if response.get("output_format") != "native_json_schema":
                raise WorkBuddyError("workbuddy_structured_output_missing")
            raw = parse_output(response.get("text"), request.output_schema, formal=bool(context["state"].get("acceptance_spec")))
            handle_id = "workbuddy:" + run_id
            context.update(thread_id=handle_id, final_response=json.dumps(raw, ensure_ascii=False))
            owner.store.update_run(run_id, thread_id=handle_id)
            owner.store.update_harness_state(run_id, {"thread_id": handle_id,
                "turn_count": context["turn_count"], "native_session_id": response.get("session_id")})
            return RuntimeResult(raw, RuntimeSession("workbuddy", handle_id),
                actual_model=response.get("actual_model"))
        except WorkBuddyError as error:
            owner.store.append_event(run_id, "workbuddy_execution_failed", {"operation": request.operation,
                "phase": "terminal_output" if error.code == "workbuddy_structured_output_missing" else "execution",
                "failure_code": error.code, **getattr(error, "detail", {})})
            if error.code == "acceptance_report_validation_failed":
                failure = owner.store.get_harness_state(run_id).get("acceptance_validation_failure") or {}
                raise AcceptanceReportValidationError(list(failure.get("validation_issues") or [])) from error
            if error.code == "workbuddy_deadline_exceeded":
                raise TimeoutError from error
            if error.code == "workbuddy_cancelled":
                raise RuntimeError("runtime_interrupted") from error
            raise

