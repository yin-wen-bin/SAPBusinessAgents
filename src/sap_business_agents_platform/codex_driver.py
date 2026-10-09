"""Codex transport driver. No SAP planning, matching or workflow business logic."""
from __future__ import annotations

import copy
import json
import re
import asyncio
from contextlib import asynccontextmanager
from typing import Any

from .runtime_contract import ApprovalMode, RuntimeRequest, RuntimeResult, RuntimeTurn, RuntimeSession, Sandbox, session_id, execute_frozen, require_deadline


def native_output_schema(schema):
    """Encode only the new role contract; business uniqueness stays authoritative.

    The concrete Codex final-stage trace rejects uniqueItems. Other Codex
    requests retain their original Schema object and every existing option.
    """
    from .runtime_role_consolidation import SCHEMA
    if schema != SCHEMA:
        return schema
    native = copy.deepcopy(schema)
    for name in ("workflow_suggestions", "agent_gaps"):
        native["properties"][name]["items"]["properties"]["operation_ids"].pop("uniqueItems")
    return native


def role_schema_error(error, schema):
    from .runtime_role_consolidation import SCHEMA
    if schema != SCHEMA or "Invalid schema" not in str(error):
        return None
    # Native messages are inspected only for known signatures, never persisted.
    from .runtime_contract import RuntimeContractError
    failure = RuntimeContractError("runtime_native_schema_rejected")
    match = re.search(r"\b(uniqueItems|minItems|minLength|type|required|additionalProperties)\b.{0,4} is not permitted", str(error))
    failure.detail = {"failure_source": "sdk_exception"}
    if match:
        failure.detail["validation_issues"] = [{"code": "runtime_report_schema_invalid", "path": "/", "constraint": match[1]}]
    return failure


def native_options(options: dict[str, Any]) -> dict[str, Any]:
    from openai_codex import ApprovalMode as NativeApproval, Sandbox as NativeSandbox
    result = dict(options)
    if isinstance(result.get("sandbox"), Sandbox):
        result["sandbox"] = getattr(NativeSandbox, result["sandbox"].name)
    if isinstance(result.get("approval_mode"), ApprovalMode):
        result["approval_mode"] = getattr(NativeApproval, result["approval_mode"].name)
    return result


class CodexSDKDriver:
    provider_id = "codex"

    def __init__(self, owner: Any):
        self.owner = owner

    def client(self, **options: Any) -> Any:
        from openai_codex import AsyncCodex
        options.pop("format_instructions", None)
        return CodexClient(self, AsyncCodex(**options))

    def feedback_options(self, policy, *, intent, image_inputs):
        from .runtime_execution import FULL_ACCESS_POLICY, LEGACY_TOOL_POLICY
        if policy is not None and policy not in (FULL_ACCESS_POLICY, LEGACY_TOOL_POLICY):
            from .authoring_harness import AuthoringHarnessError
            raise AuthoringHarnessError("agent_harness_policy_invalid")
        full = policy == FULL_ACCESS_POLICY
        return {"workspace": policy is not None, "full_access": full,
                "mode": "full_access" if full else "isolated_tools", "directory": "authoring-harness"}

    @asynccontextmanager
    async def feedback_session(self, *, workspace, cwd, operation_id, profile, tool_session):
        from .codex_planner import _tool_authoring_codex, _agent_authoring_codex, _with_authoring_mcp, _authoring_preflight_command
        from .runtime_contract import remaining_budget, cleanup_scope, cleanup_deadline, RuntimeContractError
        import time
        owner = self.owner
        client = _tool_authoring_codex(workspace, full_access=profile["full_access"]) if workspace else _agent_authoring_codex(cwd)
        if tool_session:
            client = _with_authoring_mcp(client, tool_session)
        key = operation_id or f"local-{id(client)}"
        start = asyncio.create_task(client.__aenter__())
        state = {"client": client, "start": start, "proc": None, "cleanup": None, "tool_mode": workspace is not None}
        owner._authoring_clients[key] = state
        primary = None
        try:
            native = await asyncio.wait_for(asyncio.shield(start), timeout=remaining_budget(30))
            state["proc"] = owner._authoring_process(client)
            preflight = None
            if workspace:
                if profile["full_access"]:
                    from .runtime_execution import command_preflight
                    preflight = await command_preflight(native, workspace.source)
                else:
                    from .authoring_workspace import sandbox_preflight
                    async def probe(ws, command, *, timeout=30):
                        return await _authoring_preflight_command(native, ws, command, timeout=remaining_budget(timeout))
                    preflight = await sandbox_preflight(workspace, accept_loopback_access=True, command_runner=probe)
            yield {"native": native, "preflight": preflight}
        except BaseException as exc:
            primary = exc
            raise
        finally:
            with cleanup_scope():
                state["cleanup_deadline"] = cleanup_deadline()
                close = asyncio.create_task(owner._close_authoring_client(key, state))
                state["cleanup"] = close
                done, _ = await asyncio.wait({close}, timeout=max(0, cleanup_deadline() - time.monotonic()))
                confirmed = bool(done and key in owner._closed_authoring_operations)
                if not confirmed:
                    close.add_done_callback(lambda task: None if task.cancelled() else task.exception())
                    if primary is None:
                        raise RuntimeContractError("runtime_cleanup_incomplete")
                    primary.detail = {**dict(getattr(primary, "detail", {}) or {}), "cleanup_failure_code": "runtime_cleanup_incomplete"}

    async def feedback_turn(self, session, prompt, *, cwd, workspace, thread_id, intent, image_inputs):
        return await self.owner._native_agent_feedback(session["native"], prompt, {}, thread_id, str(cwd),
            tool_workspace=workspace, explain_only=intent == "explain", image_inputs=image_inputs)

    def feedback_metadata(self, result, workspace, profile):
        if profile["full_access"]:
            from .runtime_execution import execution_snapshot
            result.update(execution_snapshot(model=self.owner.model, effort=self.owner.reasoning_effort),
                          base_digest=workspace.base_digest)

    def workflow_options(self, mode):
        return {"directory": "workflow-authoring", "snapshot_options": {}}

    async def workflow_turn(self, request, *, workspace, task_id, emit, cleanup_state):
        from .codex_planner import _tool_authoring_codex, _authoring_preflight_command
        from .runtime_execution import owned_client, command_preflight
        from .workflow_authoring_runtime import workflow_preflight, collect_turn, capabilities
        from openai_codex import ApprovalMode as NativeApproval, Sandbox as NativeSandbox
        mode = request.permissions["execution_mode"]
        client = _tool_authoring_codex(workspace, full_access=mode == "full_access")
        emit("preflight", {})
        async with owned_client(client, cleanup_timeout=10, cleanup_state=cleanup_state) as codex:
            if mode == "full_access":
                preflight = await command_preflight(codex, workspace.source)
            else:
                async def probe(ws, command, *, timeout=30):
                    return await _authoring_preflight_command(codex, ws, command, timeout=timeout)
                preflight = await workflow_preflight(workspace, probe)
            emit("research", {"preflight": preflight.get("status", "passed")})
            if mode == "full_access":
                thread = await codex.thread_start(cwd=request.cwd, sandbox=NativeSandbox.full_access,
                    approval_mode=NativeApproval.deny_all, model=self.owner.model,
                    service_name="sapba_workflow_authoring_v2", developer_instructions=request.instructions)
            else:
                from openai_codex.api import AsyncThread
                from openai_codex.generated.v2_all import ThreadStartResponse
                started = await codex._client.request("thread/start", {
                    "cwd": request.cwd, "permissions": "sapba-authoring", "approvalPolicy": "never",
                    "model": self.owner.model, "ephemeral": True, "developerInstructions": request.instructions,
                }, response_model=ThreadStartResponse)
                thread = AsyncThread(codex, started.thread.id)
            options = {"sandbox": NativeSandbox.full_access, "approval_mode": NativeApproval.deny_all,
                       "cwd": request.cwd} if mode == "full_access" else {}
            handle = await thread.turn(request.prompt, output_schema=request.output_schema,
                effort=self.owner.reasoning_effort, **options)
            result = await collect_turn(handle, emit)
        return RuntimeResult(json.loads(result.final_response), RuntimeSession("codex", thread.id),
            cleanup_complete=cleanup_state.get("complete") is True), capabilities(mode, preflight)

    async def execute(self, request: RuntimeRequest, *, emit=None) -> RuntimeResult:
        return await execute_frozen(self, request, emit=emit)

    async def _execute(self, request):
        async with self.client() as client:
            options = {"cwd": request.cwd, "model": request.binding.get("model"), **request.permissions}
            if request.session:
                thread = await client.thread_resume(request.session, **options)
            else:
                thread = await client.thread_start(**options, developer_instructions=request.instructions)
            result = await thread.run(request.prompt, output_schema=request.output_schema,
                                      effort=request.binding.get("reasoning_effort"))
        return result.result(cleanup_complete=client.cleanup_state.get("complete") is True)


class CodexClient:
    def __init__(self, driver, native):
        self.driver, self.native = driver, native
        self.cleanup_state = {"complete": False}

    async def __aenter__(self):
        from .runtime_execution import owned_client
        self._owned = owned_client(self.native, cleanup_timeout=10, cleanup_state=self.cleanup_state)
        self.active = await self._owned.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self._owned.__aexit__(*args)

    async def thread_start(self, **options):
        thread = await self.active.thread_start(**native_options(options))
        return CodexThread(self.driver, thread, options)

    async def thread_resume(self, handle, **options):
        handle = session_id(handle, "codex")
        if isinstance(handle, str) and handle.startswith("workbuddy"):
            from .runtime_contract import RuntimeContractError
            raise RuntimeContractError("runtime_session_provider_mismatch")
        thread = await self.active.thread_resume(handle, **native_options(options))
        return CodexThread(self.driver, thread, options)


class CodexThread:
    def __init__(self, driver, native, options):
        self.driver, self.native, self.options = driver, native, options
        self.id = native.id
        self.index = 0

    async def run(self, prompt, *, output_schema, effort=None, **options):
        self.index += 1
        owner = self.driver.owner
        request = RuntimeRequest("codex", owner.current_operation,
            f"turn-{self.index}", {"provider_id": "codex", "model": owner.model, "reasoning_effort": effort},
            prompt, output_schema, cwd=self.options.get("cwd"),
            instructions=self.options.get("developer_instructions"),
            permissions={key: value for key, value in self.options.items() if key in {"sandbox", "approval_mode"}},
            session=RuntimeSession("codex", self.id))
        require_deadline()
        request.remaining()
        try:
            result = await self.native.run(prompt, output_schema=native_output_schema(output_schema), effort=effort, **native_options(options))
        except Exception as exc:
            failure = role_schema_error(exc, output_schema)
            if failure is not None:
                raise failure from None
            raise
        # Preserve the SDK's final output rather than repairing or inventing fields.
        request.remaining()
        from .runtime_diagnostics import checked_output
        return RuntimeTurn(checked_output(result.final_response, output_schema,
            operation=request.operation, output_format="native_json_schema"), RuntimeSession("codex", self.id),
            actual_model=getattr(result, "model", None))


class CodexSampleDriver:
    provider_id = "codex"

    async def sample_turn(self, service, run_id, context, capability, binding):
        return await run_codex_sample(service, run_id, context, capability)

    async def interrupt_sample(self, service, run_id):
        from .harness import _best_effort_interrupt
        active = service._turns.get(run_id)
        if active is not None:
            await _best_effort_interrupt(active)


async def run_codex_sample(service, run_id, context, capability):
    import asyncio
    from .sample_discovery import SampleDiscoveryError, SAMPLE_MODEL, sample_output_schema
    from .runtime_sample import native_sample_allowed
    from .harness import (_safe_codex, _approval_mode, _sandbox, _event_item,
        _completed_turn_error, _custom_tool_kind, _best_effort_interrupt)
    self = service
    workspace = self.settings.data_root / "harness" / run_id / "sample-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    final_response = ""
    codex = _safe_codex(self.settings, run_id, capability, workspace, allow_web=False)
    async with asyncio.timeout(context.remaining()):
        from .runtime_execution import owned_client
        async with owned_client(codex):
            thread = await codex.thread_start(approval_mode=_approval_mode(), developer_instructions=context.prompt(),
                                             cwd=str(workspace), model=SAMPLE_MODEL, sandbox=_sandbox())
            self.store.update_run(run_id, thread_id=thread.id)
            turn = await thread.turn("Find a verifiable real input sample within the declared scope.",
                                     approval_mode=_approval_mode(), model=SAMPLE_MODEL,
                                     effort=self.store.get_run(run_id).runtime.reasoning_effort,
                                     output_schema=sample_output_schema(), sandbox=_sandbox())
            self._turns[run_id] = turn
            async def consume():
                nonlocal final_response
                async for event in turn.stream():
                    kind, item = _event_item(event)
                    custom_kind, _ = _custom_tool_kind(item)
                    if not native_sample_allowed(kind, custom_kind):
                        raise SampleDiscoveryError("sample_capability_isolation_failed")
                    if event.method == "turn/completed" and _completed_turn_error(event):
                        raise SampleDiscoveryError("sample_runtime_unavailable")
                    if kind == "agentMessage" and event.method == "item/completed":
                        final_response = str(item.get("text") or "")
            stream_task = asyncio.create_task(consume())
            ready_task = asyncio.create_task(context.ready_event.wait())
            try:
                await asyncio.wait({stream_task, ready_task}, return_when=asyncio.FIRST_COMPLETED)
                if stream_task.done():
                    stream_task.result()  # Tool-boundary violations still fail closed.
                if context.ready_result:
                    context.closed = True
                    context.progress("finalizing")
                    await _best_effort_interrupt(turn)
                else:
                    await stream_task
            finally:
                if not stream_task.done():
                    # SDK 0.147 uses to_thread(queue.get). Cancelling the
                    # asyncio wrapper unregisters that queue, leaving the
                    # worker blocked forever. Wake this task-owned router
                    # BEFORE cancellation/unregistration (no global SDK state).
                    router = getattr(getattr(getattr(codex, "_client", None), "_sync", None), "_router", None)
                    if router is not None and callable(getattr(router, "fail_all", None)):
                        router.fail_all(RuntimeError("sample_stream_closed"))
                        await asyncio.wait({stream_task}, timeout=1)
                for task in (stream_task, ready_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(stream_task, ready_task, return_exceptions=True)
    return final_response


class CodexHarnessDriver:
    """Native Codex session/event codec; lifecycle and evidence decisions are shared."""
    provider_id = "codex"
    engineering_copy = True

    def __init__(self, owner):
        self.owner = owner

    def binding(self, run_id, state, model, reasoning_effort):
        from .runtime_execution import execution_snapshot
        if not model or not reasoning_effort:
            raise RuntimeError("runtime_execution_binding_missing")
        return execution_snapshot(model=model, effort=reasoning_effort)

    async def cleanup(self, run_id, context):
        self.owner._active_turns.pop(run_id, None)

    async def harness_turn(self, request, context):
        import asyncio
        from .runtime_execution import owned_client, command_preflight, public_tool_event
        from .runtime_harness import report_tool_completed
        from .harness import (_safe_codex, _approval_mode, _sandbox, _event_item,
            _completed_turn_error, _custom_tool_kind, _best_effort_interrupt,
            _public_https_citations, _stream_with_timeout, _last_agent_message)
        self = self.owner
        run_id, capability, workspace = context["run_id"], context["capability"], context["workspace"]
        model, reasoning_effort = request.binding["model"], request.binding["reasoning_effort"]
        full_access = request.permissions["full_access"]
        direct_baseline = context["state"].get("acceptance_direct_baseline") is True
        cleanup_state, deadline_monitor = context["cleanup_state"], None
        thread_id, turn_count = context["thread_id"], context["turn_count"]
        codex = _safe_codex(
            self.settings, run_id, capability, workspace,
            allow_web=not direct_baseline, full_access=full_access,
        )
        web_search_count = 0
        self.store.append_event(
            run_id,
            "harness_started",
            {
                "runtime": "codex_app_server",
                "protocol": "agent_runtime.v2",
                "web_search": not direct_baseline,
                "acceptance_direct_baseline": direct_baseline,
                "turn_count": turn_count,
            },
        )
        try:
            async with owned_client(codex, cleanup_timeout=10, cleanup_state=cleanup_state):
                if full_access:
                    preflight = await command_preflight(codex, workspace)
                    self.store.append_event(run_id, "runtime_command_preflight", preflight)
                if thread_id:
                    thread = await codex.thread_resume(
                        thread_id,
                        approval_mode=_approval_mode(),
                        developer_instructions=request.instructions,
                        cwd=str(workspace),
                        model=model,
                        sandbox=_sandbox(full_access=full_access),
                    )
                else:
                    thread = await codex.thread_start(
                        approval_mode=_approval_mode(),
                        developer_instructions=request.instructions,
                        cwd=str(workspace),
                        model=model,
                        sandbox=_sandbox(full_access=full_access),
                    )
                    thread_id = thread.id
                self.store.update_run(run_id, thread_id=thread_id)
                prompt = request.prompt
                turn = await thread.turn(
                    prompt,
                    effort=reasoning_effort,
                    approval_mode=_approval_mode(),
                    model=model,
                    output_schema=request.output_schema,
                    sandbox=_sandbox(full_access=full_access),
                )
                self._active_turns[run_id] = turn
                self.store.update_harness_state(
                    run_id,
                    {"thread_id": thread_id, "turn_count": turn_count, "active_turn_id": turn.id},
                )
                deadline_monitor = asyncio.create_task(
                    self._monitor_deadline(run_id, turn),
                    name=f"sapba-harness-deadline-{run_id}",
                )
                self.store.append_event(
                    run_id, "codex_turn_started", {"turn_id": turn.id, "turn_count": turn_count}
                )
                final_response = ""
                completed_from_validated_report = False
                context["native_complete"] = False
                async for event in _stream_with_timeout(
                    turn.stream(), max(0, self.broker.budget_snapshot(run_id)["hard_limit_seconds"] - self.broker._elapsed_seconds(run_id))
                ):
                    item_type, item = _event_item(event)
                    if event.method == "turn/completed":
                        context["native_complete"] = True
                        turn_error = _completed_turn_error(event)
                        if turn_error:
                            self.store.append_event(
                                run_id,
                                "codex_turn_failed",
                                {"turn_id": turn.id, "code": turn_error[0], "message": turn_error[1]},
                            )
                            raise RuntimeError(f"{turn_error[0]}:{turn_error[1]}")
                    custom_kind, custom_topic = _custom_tool_kind(item, full_access=full_access)
                    if custom_kind == "forbidden":
                        await _best_effort_interrupt(turn)
                        raise RuntimeError("capability_isolation_failed:custom_tool")
                    if custom_kind == "engineering":
                        self.store.append_event(run_id, "runtime_engineering_tool", public_tool_event(item))
                    elif custom_kind == "web_search":
                        if event.method == "item/started":
                            self.store.append_event(
                                run_id, "web_search_started", {"query": custom_topic}
                            )
                        elif event.method == "item/completed":
                            web_search_count += 1
                            self.store.append_event(
                                run_id,
                                "web_search_completed",
                                {
                                    "query": custom_topic,
                                    "citations": _public_https_citations(item),
                                },
                            )
                    elif item_type == "webSearch":
                        if event.method == "item/started":
                            self.store.append_event(
                                run_id, "web_search_started", {"query": item.get("query", "")}
                            )
                        elif event.method == "item/completed":
                            web_search_count += 1
                            self.store.append_event(
                                run_id,
                                "web_search_completed",
                                {
                                    "query": item.get("query", ""),
                                    "citations": _public_https_citations(item),
                                },
                            )
                    elif item_type == "mcpToolCall":
                        self.store.append_event(
                            run_id,
                            (
                                "agent_runtime_tool_started"
                                if event.method == "item/started"
                                else "agent_runtime_tool_completed"
                            ),
                            {
                                "tool": item.get("tool"),
                                "server": item.get("server"),
                                "status": item.get("status"),
                            },
                        )
                        if (
                            event.method == "item/completed"
                            and item.get("tool") == "sap_final_report_validate"
                        ):
                            recovered_payload = await report_tool_completed(self, run_id, turn)
                            if recovered_payload is not None:
                                final_response = json.dumps(recovered_payload, ensure_ascii=False)
                                completed_from_validated_report = True
                                context["native_complete"] = True
                                break
                    elif item_type == "agentMessage" and event.method == "item/completed":
                        final_response = str(item.get("text") or final_response)
                        self.store.append_event(
                            run_id, "assistant_message", {"message": final_response[:4000]}
                        )
                    elif item_type in {"commandExecution", "fileChange"}:
                        self.store.append_event(run_id, "runtime_engineering_tool", public_tool_event(item))
                    elif item_type in {
                        "commandExecution",
                        "fileChange",
                        "computerUse",
                        "collabAgentToolCall",
                        "dynamicToolCall",
                    }:
                        await _best_effort_interrupt(turn)
                        raise RuntimeError(f"capability_isolation_failed:{item_type}")
                self.store.append_event(
                    run_id,
                    (
                        "codex_turn_closed_after_validation"
                        if completed_from_validated_report
                        else "codex_turn_completed"
                    ),
                    {"turn_id": turn.id, "turn_count": turn_count},
                )
                if not final_response:
                    read = await thread.read(include_turns=True)
                    final_response = _last_agent_message(read.model_dump(mode="json", by_alias=True))
        finally:
            if deadline_monitor is not None:
                deadline_monitor.cancel()
                await asyncio.gather(deadline_monitor, return_exceptions=True)
            context.update(thread_id=thread_id, final_response=locals().get("final_response", ""))
        return RuntimeResult(json.loads(final_response), RuntimeSession("codex", thread_id),
            cleanup_complete=cleanup_state.get("complete") is True)


