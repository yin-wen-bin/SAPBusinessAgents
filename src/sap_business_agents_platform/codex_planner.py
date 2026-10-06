from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
import sys
import tempfile
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Protocol

from .models import PlannerDecision, RunPresentation
from .shared_planner import SharedPlanner
from .codex_driver import CodexSDKDriver
from .runtime_prompts import (
    PLANNER_OUTPUT_SCHEMA,
    SUMMARY_OUTPUT_SCHEMA,
    AUTHOR_OUTPUT_SCHEMA,
    WORKFLOW_REVIEW_OUTPUT_SCHEMA,
    AGENT_FEEDBACK_OUTPUT_SCHEMA,
    FREE_QUERY_FEEDBACK_REVIEW_SCHEMA,
    _strict_response_schema,
    _RUN_PRESENTATION_SCHEMA,
    FREE_QUERY_PRESENTATION_REVISION_SCHEMA,
    WORKFLOW_REPAIR_OUTPUT_SCHEMA,
    WORKFLOW_COMPOSITION_OUTPUT_SCHEMA,
    WORKFLOW_FEEDBACK_OUTPUT_SCHEMA,
    ROLE_MATCHING_OUTPUT_SCHEMA,
    ROLE_MATCHING_RUNTIME_TURN_SECONDS,
    _consume_background_task,
    _await_with_hard_timeout,
    _workflow_assistant_tool_catalog,
    _workflow_feedback_prompt,
    _workflow_composition_prompt,
    _planner_prompt,
    _run_plan_turn,
    _decode_plan_json,
    _grounding_prompt,
    _schema_snapshot,
    _RUNTIME_PLAN_CONTRACT,
    _SECRET_KEYS,
    _decode_role_matching_output,
    _role_matching_thread_can_restart,
    _exact_json,
    _compact_role_match_records,
    _safe_json,
)



def _agent_authoring_codex(workspace: Path) -> Any:
    """Start a tool-free client in an empty directory, without inherited MCP servers."""
    import re
    import tomllib
    from codex_cli_bin import bundled_codex_path
    from openai_codex import AsyncCodex
    from openai_codex.client import CodexConfig
    from .harness import _sanitized_codex_env, _deny_approval

    args = [str(bundled_codex_path()), "--config", 'web_search="disabled"']
    for feature in ("shell_tool", "apply_patch_streaming_events", "browser_use", "computer_use", "image_generation", "multi_agent", "plugins", "apps", "hooks"):
        args.extend(["--disable", feature])
    config_path = Path.home() / ".codex" / "config.toml"
    if config_path.is_file():
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        for name in config.get("mcp_servers") or {}:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", str(name)):
                raise ValueError("agent_authoring_isolation_failed")
            args.extend(["--config", f"mcp_servers.{name}.enabled=false"])
    args.extend(["app-server", "--listen", "stdio://"])
    codex = AsyncCodex(config=CodexConfig(launch_args_override=tuple(args), cwd=str(workspace), env=_sanitized_codex_env(), client_name="sapba_agent_authoring"))
    codex._client._sync._approval_handler = _deny_approval
    return codex


def _tool_authoring_codex(workspace: Any, *, full_access: bool = False) -> Any:
    """Tool-capable authoring client; scope is the copy, never the live checkout."""
    from dataclasses import replace
    from .authoring_workspace import isolated_environment, sandbox_overrides

    client = _agent_authoring_codex(workspace.source)
    if full_access:
        from .runtime_execution import configure_client
        return configure_client(client)
    config = client._client._sync.config
    args = list(config.launch_args_override)
    for feature in ("shell_tool", "apply_patch_streaming_events"):
        for index in range(len(args) - 1, 0, -1):
            if args[index] == feature and args[index - 1] == "--disable":
                del args[index - 1:index + 1]
    # Override the two disabled built-ins; unrelated app/plugin/MCP features stay off.
    overrides = sandbox_overrides(workspace)[2:] + [
        "--enable", "shell_tool", "--enable", "apply_patch_streaming_events",
        "-c", "default_permissions='sapba-authoring'",
        "-c", "shell_environment_policy.inherit='none'",
    ]
    environment = isolated_environment()
    environment_table = ",".join(json.dumps(key) + "=" + json.dumps(value) for key, value in environment.items())
    overrides += ["-c", "shell_environment_policy.set={" + environment_table + "}"]
    args[args.index("app-server"):args.index("app-server")] = overrides
    # SDK env is merged with the trusted host. Do not blank Codex's transport
    # identity variables (an empty originator breaks SDK initialization).
    # Tool children use inherit=none above, so they receive only the allowlist.
    cleared = {key: "" for key in os.environ if key.upper().startswith(("SAP_", "SAPBA_SAP", "SAP_ADT_"))
               or any(word in key.upper() for word in ("PASSWORD", "SECRET", "API_KEY", "TOKEN"))
               or key.upper() in {"PYTHONPATH", "NODE_OPTIONS", "SSH_AUTH_SOCK"}}
    client._client._sync.config = replace(config, launch_args_override=tuple(args),
        env={**cleared, **environment})
    return client


def _with_authoring_mcp(client: Any, session: dict[str, str]) -> Any:
    """Attach only the platform's revision-scoped authoring Broker."""
    from dataclasses import replace
    from .harness import _validate_internal_api_url
    _validate_internal_api_url(session["internal_api_url"])
    config = client._client._sync.config
    args = list(config.launch_args_override)
    name = "sap_authoring_catalog"
    values = [
        f"mcp_servers.{name}.command={json.dumps(sys.executable)}",
        f"mcp_servers.{name}.args={json.dumps(['-m', 'sap_business_agents_platform.mcp_server', '--mode', 'authoring'])}",
        f"mcp_servers.{name}.enabled=true",
        f"mcp_servers.{name}.env.SAPBA_INTERNAL_API_URL={json.dumps(session['internal_api_url'])}",
        f"mcp_servers.{name}.env.SAPBA_HARNESS_RUN_ID={json.dumps(session['operation_id'])}",
        f"mcp_servers.{name}.env.SAPBA_HARNESS_CAPABILITY={json.dumps(session['capability'])}",
        f"mcp_servers.{name}.env.PYTHONUTF8={json.dumps('1')}",
    ]
    for value in values:
        args[args.index("app-server"):args.index("app-server")] = ["--config", value]
    client._client._sync.config = replace(config, launch_args_override=tuple(args))
    return client


async def _authoring_preflight_command(codex: Any, workspace: Any, command: list[str], *, timeout: float = 30) -> tuple[int, bytes, bytes]:
    """Bound the SDK request too: timeoutMs alone does not bound sandbox setup."""
    from openai_codex.generated.v2_all import CommandExecResponse
    from .authoring_harness import AuthoringHarnessError
    try:
        response = await asyncio.wait_for(codex._client.request("command/exec", {
            "command": command, "cwd": str(workspace.source),
            "permissionProfile": "sapba-authoring", "timeoutMs": int(timeout * 1000),
        }, response_model=CommandExecResponse), timeout=timeout + 30)
    except TimeoutError:
        # The caller's finally owns and terminates the entire client/process tree.
        raise AuthoringHarnessError("agent_harness_sandbox_preflight_timeout") from None
    return response.exit_code, response.stdout.encode(), response.stderr.encode()






















class Planner(Protocol):
    async def plan(
        self,
        query: str,
        catalog: dict[str, Any],
        guidance: dict[str, Any],
        skills: list[dict[str, Any]],
        thread_id: str | None = None,
    ) -> PlannerDecision: ...

    async def ground_plan(
        self,
        *,
        query: str,
        decision: PlannerDecision,
        schemas: list[dict[str, Any]],
        relationships: dict[str, Any] | None = None,
        validation_failures: list[dict[str, Any]] | None = None,
        repair_attempt: int = 0,
    ) -> PlannerDecision: ...

    async def compose_workflow(
        self,
        *,
        requirement: str,
        catalog: dict[str, Any],
        locale: str,
        thread_id: str | None = None,
        clarification_input: str | None = None,
        previous: dict[str, Any] | None = None,
        integration_catalog: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def review_workflow_feedback(self, **kwargs: Any) -> dict[str, Any]: ...

    async def review_agent_feedback(self, **kwargs: Any) -> dict[str, Any]: ...

    async def analyze_role_matching(self, **kwargs: Any) -> dict[str, Any]: ...


class CodexPlanner(SharedPlanner):
    @staticmethod
    def feedback_capabilities() -> dict[str, bool]:
        # Authoring still creates an isolated turn for each revision; SDK
        # resume and live steering are not yet validated for this scope.
        return {"stream_events": False, "resume": False, "steer": False,
                "image_input": True}

    def __init__(self, repository_root: Path, model: str | None = None, reasoning_effort: str | None = None, *, data_root: Path | None = None) -> None:
        self.data_root = data_root or repository_root / ".local-data"
        self.repository_root = repository_root
        self.model = model
        self.reasoning_effort = reasoning_effort
        self._driver = CodexSDKDriver(self)
        self._authoring_clients: dict[str, dict[str, Any]] = {}
        self._closed_authoring_operations: deque[str] = deque(maxlen=1024)







    async def review_agent_feedback(
        self,
        *,
        feedback: str,
        locale: str,
        package: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
        thread_id: str | None = None,
        operation_id: str | None = None,
        tool_policy: dict[str, Any] | None = None,
        intent: str = "revise",
        feedback_context: dict[str, Any] | None = None,
        tool_session: dict[str, str] | None = None,
        image_inputs: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.model:
            raise ValueError("agent_runtime_binding_missing")
        tool_workspace = None
        preflight = None
        full_access = False
        if tool_policy is not None:
            from .authoring_workspace import AuthoringWorkspace, sandbox_preflight
            from .authoring_harness import AuthoringHarnessError
            from .runtime_execution import FULL_ACCESS_POLICY, LEGACY_TOOL_POLICY
            if tool_policy not in (FULL_ACCESS_POLICY, LEGACY_TOOL_POLICY):
                raise AuthoringHarnessError("agent_harness_policy_invalid")
            full_access = tool_policy == FULL_ACCESS_POLICY
            tool_workspace = AuthoringWorkspace(self.repository_root,
                self.data_root / "authoring-harness" / uuid.uuid4().hex)
            # Snapshot from a pinned commit. Ignored data and credentials are not copied.
            tool_workspace.prepare(package, current_source=full_access)
            tool_workspace.full_access = full_access
            (tool_workspace.source / ".authoring-tmp").mkdir()
            # Until platform changeset approval is connected, platform source is
            # readable for investigation but not editable through this entry point.
            tool_workspace.read_only_source = True

        from .runtime_agent_authoring import feedback_prompt
        explain_only = intent == 'explain'
        prompt = feedback_prompt(self.repository_root, feedback=feedback, locale=locale,
            package=package, history=history, intent=intent, feedback_context=feedback_context,
            tool_workspace=tool_workspace, full_access=full_access, tool_session=tool_session)
        with tempfile.TemporaryDirectory(prefix="sapba-agent-authoring-") as isolated:
            client = _tool_authoring_codex(tool_workspace, full_access=full_access) if tool_workspace else _agent_authoring_codex(Path(isolated))
            if tool_session is not None:
                client = _with_authoring_mcp(client, tool_session)
            key = operation_id or f"local-{id(client)}"
            # SDK startup uses to_thread. Shield it: cancelling the await must not lose
            # ownership of a process that the worker thread may still create later.
            start = asyncio.create_task(client.__aenter__())
            state: dict[str, Any] = {"client": client, "start": start, "proc": None, "cleanup": None,
                                     "tool_mode": tool_workspace is not None}
            self._authoring_clients[key] = state
            try:
                if full_access:
                    from .runtime_execution import RuntimeExecutionError
                    try:
                        codex = await asyncio.wait_for(asyncio.shield(start), timeout=30)
                    except TimeoutError:
                        raise RuntimeExecutionError("runtime_sdk_initialization_timeout") from None
                else:
                    codex = await asyncio.shield(start)
                state["proc"] = self._authoring_process(client)
                if tool_workspace:
                    async def probe_command(workspace: Any, command: list[str], *, timeout: float = 30) -> Any:
                        # Probe the same client/profile that will own tool calls.
                        return await _authoring_preflight_command(codex, workspace, command, timeout=timeout)
                    if full_access:
                        from .runtime_execution import command_preflight, execution_snapshot
                        preflight = await command_preflight(codex, tool_workspace.source)
                    else:
                        preflight = await sandbox_preflight(tool_workspace, accept_loopback_access=True, command_runner=probe_command)
                    checks = []
                    if full_access:
                        from .runtime_agent_authoring import repair_feedback
                        async def turn(candidate, issues, remaining):
                            return await self._run_agent_feedback(codex,
                                prompt + ("\nController check failures: " + json.dumps(issues) if issues else ""),
                                candidate, None, str(tool_workspace.source), tool_workspace=tool_workspace,
                                explain_only=explain_only, image_inputs=image_inputs)
                        decision = await repair_feedback(package, tool_workspace, turn, checks=checks)
                    else:
                        decision = await self._run_agent_feedback(codex, prompt, package, None,
                            str(tool_workspace.source), tool_workspace=tool_workspace,
                            explain_only=explain_only, image_inputs=image_inputs)
                    decision["harness"] = {"mode": "full_access" if full_access else "isolated_tools", "workspace_id": tool_workspace.root.name,
                        "base_commit": tool_workspace.base_commit, "preflight": preflight,
                        "live_testing": "not_performed", "platform_apply": "not_performed"}
                    if full_access:
                        decision["harness"].update(execution_snapshot(model=self.model, effort=self.reasoning_effort),
                                                   base_digest=tool_workspace.base_digest, checks=checks)
                        if tool_workspace.platform_changes():
                            from .authoring_harness import AuthoringHarnessError
                            raise AuthoringHarnessError("agent_harness_platform_approval_required")
                    return decision
                return await self._run_agent_feedback(
                    codex, prompt, package, thread_id, isolated, explain_only=explain_only,
                    image_inputs=image_inputs,
                )
            finally:
                cleanup = asyncio.create_task(self._close_authoring_client(key, state))
                state["cleanup"] = cleanup
                # A second cancellation can end this coroutine, but not cleanup. Keep
                # the state registered until shutdown is actually confirmed.
                await asyncio.shield(cleanup)

    @staticmethod
    def _authoring_process(client: Any) -> Any:
        return getattr(getattr(getattr(client, "_client", None), "_sync", None), "_proc", None)

    async def _close_authoring_client(self, key: str, state: dict[str, Any]) -> None:
        try:
            client = state["client"]
            state["proc"] = state.get("proc") or self._authoring_process(client)
            if state.get("tool_mode") and not state["start"].done() and state["proc"] is not None and state["proc"].poll() is None:
                # Initialization can wait forever on a transport response. Kill its
                # owned process before waiting for the background startup thread.
                proc = state["proc"]
                if os.name == "nt":
                    killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(proc.pid), "/T", "/F",
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                    await asyncio.wait_for(killer.wait(), timeout=8)
                else:
                    proc.kill()
            try:
                await asyncio.wait_for(asyncio.shield(state["start"]), timeout=2)
            except TimeoutError:
                # Keep ownership until a late worker-thread startup resolves.
                if not state.get("late_cleanup_registered"):
                    state["late_cleanup_registered"] = True
                    def close_late(_task: Any) -> None:
                        state["cleanup"] = asyncio.create_task(self._close_authoring_client(key, state))
                    state["start"].add_done_callback(close_late)
                return
            except asyncio.CancelledError:
                # A cancelled cleanup must not mistake an in-flight worker-thread
                # startup for "no process" and release ownership prematurely.
                if not state["start"].done() or state["start"].cancelled():
                    raise
            except Exception:
                pass
            state["proc"] = state.get("proc") or self._authoring_process(client)
            if state.get("tool_mode") and state["proc"] is not None and state["proc"].poll() is None:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(state["proc"].pid), "/T", "/F",
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                await asyncio.wait_for(killer.wait(), timeout=8)
            close = getattr(client, "close", None)
            if callable(close):
                await close()
            else:
                await client.__aexit__(None, None, None)
            proc = state.get("proc")
            if proc is not None and proc.poll() is None:
                await asyncio.to_thread(proc.wait, timeout=1)
            if proc is None or proc.poll() is not None:
                self._closed_authoring_operations.append(key)
                self._authoring_clients.pop(key, None)
        except Exception:
            # The operation remains owned and cannot be declared safe to retry.
            return

    async def abort_agent_feedback(self, operation_id: str) -> bool:
        """Emergency cleanup is restricted to the exact process owned by this operation."""
        if operation_id in self._closed_authoring_operations:
            return True
        state = self._authoring_clients.get(operation_id)
        if state is None:
            return False
        proc = state.get("proc") or self._authoring_process(state["client"])
        state["proc"] = proc
        if proc is None:
            return False
        if proc is not None and proc.poll() is None:
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(proc.pid), "/T", "/F",
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                await asyncio.wait_for(killer.wait(), timeout=8)
            else:
                proc.kill()
            await asyncio.to_thread(proc.wait, timeout=1)
        # Startup may still be creating/initializing resources in a worker thread.
        # An exited PID alone does not prove that startup and its cleanup are done.
        start, cleanup = state.get("start"), state.get("cleanup")
        return bool(proc.poll() is not None and start is not None and start.done() and not start.cancelled()
                    and cleanup is not None and cleanup.done())

    async def _run_agent_feedback(self, codex: Any, prompt: str, package: dict[str, Any], thread_id: str | None, isolated: str, *, tool_workspace: Any = None, explain_only: bool = False, image_inputs: list[str] | None = None) -> dict[str, Any]:
        from openai_codex import ApprovalMode, Sandbox

        full_access = bool(tool_workspace and getattr(tool_workspace, "full_access", False))
        if full_access:
            thread = await codex.thread_start(
                cwd=isolated, sandbox=Sandbox.full_access, approval_mode=ApprovalMode.deny_all,
                model=self.model, service_name="sap_business_agents_agent_authoring",
                developer_instructions="Inspect the work copy but edit ONLY agent-package. Platform source is read-only for draft feedback. Preserve identity and acceptance. Never apply production changes or approve/publish. Return structured results; do not invent test evidence.",
            )
        elif tool_workspace:
            # High-level SDK 0.147.0 has no permissions argument. Send the real
            # named profile on the low-level request, not the legacy broad sandbox.
            from openai_codex.api import AsyncThread
            from openai_codex.generated.v2_all import ThreadStartResponse
            started = await codex._client.request("thread/start", {
                "cwd": isolated, "permissions": "sapba-authoring", "approvalPolicy": "never",
                "model": self.model, "ephemeral": True,
                "developerInstructions": "Inspect the isolated snapshot and edit only agent-package. Use local tests; no network, SAP, publication or approvals. Return the required structured result.",
            }, response_model=ThreadStartResponse)
            thread = AsyncThread(codex, started.thread.id)
        elif thread_id:
            thread = await codex.thread_resume(
                thread_id, cwd=isolated, sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all, model=self.model,
            )
        else:
            thread = await codex.thread_start(
                cwd=isolated, sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all, model=self.model,
                service_name="sap_business_agents_agent_authoring",
                developer_instructions=(
                    ("Explain only the supplied saved Agent definition and existing evidence. "
                     "Return reply or clarify and never propose or apply package changes. "
                     if explain_only else
                     "Revise only the supplied isolated Agent package. ")
                    + "Never call tools, inspect files, run commands, contact SAP, or edit the repository."
                ),
            )
        turn_options = {"sandbox": Sandbox.full_access, "model": self.model,
                        "approval_mode": ApprovalMode.deny_all, "cwd": isolated} if full_access else {}
        if image_inputs:
            from openai_codex import ImageInput, TextInput
            prompt_input: Any = [TextInput(prompt), *[ImageInput(url=value) for value in image_inputs]]
        else:
            prompt_input = prompt
        result = await thread.run(prompt_input, output_schema=AGENT_FEEDBACK_OUTPUT_SCHEMA, effort=self.reasoning_effort, **turn_options)
        raw = json.loads(result.final_response)
        from .runtime_agent_authoring import decode_feedback
        return decode_feedback(raw, package, thread.id, tool_workspace=tool_workspace, explain_only=explain_only)


    async def author_workflow_v2(self, **kwargs: Any) -> dict[str, Any]:
        from .workflow_authoring_runtime import run
        return await run(self, **kwargs)
