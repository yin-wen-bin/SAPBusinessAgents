"""Shared SDK execution settings. Full access is NOT an OS isolation boundary.

This module changes only task-local SDK configuration, never the user's Codex
configuration or managed requirements. External writes still need application
approval. Missing bridges are reported as unavailable, not enabled by a flag.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import subprocess
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

FULL_ACCESS_POLICY = {"version": 2, "mode": "full_access"}
LEGACY_TOOL_POLICY = {"version": 1, "mode": "isolated_tools", "accept_loopback_access": True}


class RuntimeExecutionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@asynccontextmanager
async def owned_client(client: Any):
    """Own startup and shutdown, including the SDK's background startup thread."""
    start = asyncio.create_task(client.__aenter__())
    try:
        try:
            active = await asyncio.wait_for(asyncio.shield(start), timeout=30)
        except TimeoutError:
            raise RuntimeExecutionError("runtime_sdk_initialization_timeout") from None
        yield active
    finally:
        async def cleanup():
            proc = getattr(getattr(getattr(client, "_client", None), "_sync", None), "_proc", None)
            if proc is not None and proc.poll() is None:
                if os.name == "nt":
                    killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(proc.pid), "/T", "/F",
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                    await asyncio.wait_for(killer.wait(), timeout=8)
                else:
                    proc.kill()
            try:
                await asyncio.wait_for(asyncio.shield(start), timeout=2)
            except Exception:
                pass
            await asyncio.wait_for(client.close(), timeout=5)
            if not start.done() or (proc is not None and proc.poll() is None):
                raise RuntimeExecutionError("runtime_cleanup_incomplete")
        task = asyncio.create_task(cleanup())
        await asyncio.shield(task)


def sandbox() -> Any:
    from openai_codex import Sandbox
    return Sandbox.full_access


def tool_catalog() -> dict[str, Any]:
    return {
        "shell": "available", "file_edit": "available", "web_search": "available",
        "browser": "not_connected", "subagents": "not_connected",
        "plugins": "platform_broker_only",
    }


def execution_snapshot(*, model: str, effort: str | None) -> dict[str, Any]:
    from importlib.metadata import version
    catalog = tool_catalog()
    return {"policy": dict(FULL_ACCESS_POLICY), "model": model, "reasoning_effort": effort,
            "sdk_version": version("openai-codex"), "cli_version": version("openai-codex-cli-bin"),
            "tool_catalog": catalog,
            "tool_catalog_digest": hashlib.sha256(json.dumps(catalog, sort_keys=True).encode()).hexdigest(),
            "os_isolation": False}


def configure_client(client: Any) -> Any:
    """Upgrade an explicitly constructed client, without importing global MCPs.

    Browser/child-thread/plugin bridges are separate capabilities, not native
    feature flags. Do not enable unmanaged global integrations accidentally.
    """
    config = client._client._sync.config
    args = list(config.launch_args_override)
    for index in range(len(args) - 2, 0, -1):
        if args[index] == "--disable" and args[index + 1] in {"shell_tool", "apply_patch_streaming_events"}:
            del args[index:index + 2]
    overrides = ["--enable", "shell_tool", "--enable", "apply_patch_streaming_events",
                 "--config", 'sandbox_mode="danger-full-access"',
                 "--config", 'approval_policy="never"',
                 "--config", 'web_search="live"']
    # Keep required Windows runtime/search paths, not arbitrary host secrets.
    from .authoring_workspace import isolated_environment
    environment = isolated_environment()
    environment["PATH"] = os.environ.get("PATH", environment.get("PATH", ""))
    overrides += ["--config", 'shell_environment_policy.inherit="none"', "--config",
                  "shell_environment_policy.set={" + ",".join(
                      json.dumps(key) + "=" + json.dumps(value) for key, value in environment.items()) + "}"]
    args[args.index("app-server"):args.index("app-server")] = overrides
    cleared = {key: "" for key in os.environ
               if key.upper().startswith(("SAP_", "SAPBA_SAP", "SAP_ADT_"))
               or any(word in key.upper() for word in ("PASSWORD", "SECRET", "API_KEY", "TOKEN"))
               or key.upper() in {"PYTHONPATH", "NODE_OPTIONS", "SSH_AUTH_SOCK"}}
    client._client._sync.config = replace(config, launch_args_override=tuple(args),
                                        env={**(config.env or {}), **cleared, **environment})
    return client


async def command_preflight(codex: Any, cwd: Path, *, timeout: float = 10) -> dict[str, Any]:
    """No model, SAP or permission initialization; run one fixed local command."""
    from openai_codex.generated.v2_all import CommandExecResponse
    try:
        response = await asyncio.wait_for(codex._client.request("command/exec", {
            "command": [sys.executable, "-I", "-c", "print('sapba-command-ready')"],
            "cwd": str(cwd), "sandboxPolicy": {"type": "dangerFullAccess"},
            "timeoutMs": int(timeout * 1000),
        }, response_model=CommandExecResponse), timeout=timeout + 10)
    except TimeoutError:
        raise RuntimeExecutionError("runtime_command_preflight_timeout") from None
    if response.exit_code != 0 or response.stdout.strip() != "sapba-command-ready":
        raise RuntimeExecutionError("runtime_command_preflight_failed")
    return {"status": "passed", "command": "fixed_python_probe", "sap_calls": 0,
            "sandbox": "full_access", "os_isolation": False}


def public_tool_event(item: dict[str, Any]) -> dict[str, Any]:
    """Do not persist command text, raw output, file bodies or child messages."""
    allowed = {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall",
               "collabAgentToolCall", "customToolCall"}
    kind = str(item.get("type") or "")
    status = str(item.get("status") or "unknown")
    return {"kind": kind if kind in allowed else "unknown",
            "status": status if status in {"inProgress", "completed", "failed", "declined", "cancelled"} else "unknown"}
